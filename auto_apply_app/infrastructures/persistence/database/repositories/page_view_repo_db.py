# =============================================================================
# page_view_repo_db.py
# =============================================================================
from datetime import datetime

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.application.repositories.page_view_repo import PageViewRepository
from auto_apply_app.domain.entities.page_view import PageView
from auto_apply_app.infrastructures.persistence.database.models.schema import PageViewDB


# A session starts on a visitor's first view, or on any view following a gap longer than
# the inactivity threshold. Counting those starts counts the visits.
#
# The look-back window is the subtle part. We scan from (start - gap), not from start,
# so a visit already in progress at midnight is recognised as ongoing. Without it, LAG()
# would see NULL for that visitor's first view after midnight and invent a phantom
# session for every single visitor who happened to be browsing at 00:00.
#
# Visits that straddle midnight still count once in each day. That is what every
# analytics tool does, and it keeps each day's number self-contained.
#
# The CASTs are load-bearing, not decoration. Without them Postgres cannot infer the type
# of the bound parameter in `:start - make_interval(...)`, guesses `interval`, and the
# whole predicate collapses with "operator does not exist: timestamp with time zone >=
# interval". Keep them.
_COUNT_SESSIONS_SQL = text("""
    WITH ordered AS (
        SELECT
            created_at,
            LAG(created_at) OVER (PARTITION BY visitor_id ORDER BY created_at) AS prev_at
        FROM page_views
        WHERE created_at >= CAST(:start AS timestamptz)
                            - make_interval(mins => CAST(:gap AS int))
          AND created_at <  CAST(:end AS timestamptz)
    )
    SELECT count(*)
      FROM ordered
     WHERE created_at >= CAST(:start AS timestamptz)
       AND (
             prev_at IS NULL
             OR created_at - prev_at > make_interval(mins => CAST(:gap AS int))
           )
""")


class PageViewRepoDB(PageViewRepository):

    def __init__(self, session: AsyncSession):
        self.session = session

    async def save(self, page_view: PageView) -> None:
        # Page views are append-only, so a plain add() is enough — merge() would cost a
        # pointless SELECT to check for a row we know does not exist.
        self.session.add(PageViewDB(
            id=page_view.id,
            visitor_id=page_view.visitor_id,
            user_id=page_view.user_id,
            path=page_view.path,
            referrer=page_view.referrer,
            created_at=page_view.created_at,
        ))

    async def count_unique_visitors_between(self, start: datetime, end: datetime) -> int:
        stmt = (
            select(func.count(func.distinct(PageViewDB.visitor_id)))
            .where(PageViewDB.created_at >= start)
            .where(PageViewDB.created_at < end)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_page_views_between(self, start: datetime, end: datetime) -> int:
        stmt = (
            select(func.count(PageViewDB.id))
            .where(PageViewDB.created_at >= start)
            .where(PageViewDB.created_at < end)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_signed_in_visitors_between(self, start: datetime, end: datetime) -> int:
        stmt = (
            select(func.count(func.distinct(PageViewDB.visitor_id)))
            .where(PageViewDB.created_at >= start)
            .where(PageViewDB.created_at < end)
            .where(PageViewDB.user_id.is_not(None))
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def count_sessions_between(
        self,
        start: datetime,
        end: datetime,
        inactivity_minutes: int = 30,
    ) -> int:
        result = await self.session.execute(
            _COUNT_SESSIONS_SQL,
            {"start": start, "end": end, "gap": inactivity_minutes},
        )
        return int(result.scalar_one())

    async def delete_older_than(self, cutoff: datetime) -> int:
        result = await self.session.execute(
            delete(PageViewDB).where(PageViewDB.created_at < cutoff)
        )
        return result.rowcount or 0

    def _map_to_entity(self, pv_db: PageViewDB) -> PageView:
        page_view = PageView(
            visitor_id=pv_db.visitor_id,
            path=pv_db.path,
            user_id=pv_db.user_id,
            referrer=pv_db.referrer,
            created_at=pv_db.created_at,
        )
        page_view.id = pv_db.id
        return page_view
