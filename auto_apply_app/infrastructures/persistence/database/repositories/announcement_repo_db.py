# auto_apply_app/infrastructures/persistence/database/repositories/announcement_repo_db.py
from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import delete as sql_delete, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.application.repositories.announcement_repo import (
    AnnouncementRepository,
)
from auto_apply_app.domain.entities.announcement import Announcement
from auto_apply_app.infrastructures.persistence.database.models.schema import (
    AnnouncementDB,
    AnnouncementViewDB,
)


class AnnouncementRepoDB(AnnouncementRepository):
    """Database implementation of AnnouncementRepository using SQLAlchemy."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # --- Read path ---

    async def list_active_for_user(
        self, user_id: UUID, moment: datetime
    ) -> list[Announcement]:
        # NOT EXISTS rather than a LEFT JOIN ... IS NULL: it stops at the first
        # matching view row, and there is a unique index on the exact pair it
        # probes. This runs on every authenticated page load.
        dismissed = (
            select(AnnouncementViewDB.id)
            .where(AnnouncementViewDB.user_id == user_id)
            .where(AnnouncementViewDB.announcement_id == AnnouncementDB.id)
            .exists()
        )
        stmt = (
            select(AnnouncementDB)
            .where(AnnouncementDB.is_published.is_(True))
            .where(AnnouncementDB.starts_at <= moment)
            # ends_at IS NULL means open-ended, so it cannot be compared away.
            .where(or_(AnnouncementDB.ends_at.is_(None), AnnouncementDB.ends_at > moment))
            .where(~dismissed)
            .order_by(AnnouncementDB.starts_at.desc())
        )
        result = await self.session.execute(stmt)
        return [self._map_to_entity(row) for row in result.scalars().all()]

    async def dismiss(self, user_id: UUID, announcement_id: UUID) -> bool:
        # Same reasoning as MessageLogRepoDB.record: a read-then-write loses to a
        # double-click, ON CONFLICT cannot.
        stmt = (
            pg_insert(AnnouncementViewDB)
            .values(user_id=user_id, announcement_id=announcement_id)
            .on_conflict_do_nothing(
                constraint="uq_announcement_view_user_announcement"
            )
        )
        result = await self.session.execute(stmt)
        return (result.rowcount or 0) > 0

    # --- Admin path ---

    async def get(self, announcement_id: UUID) -> Optional[Announcement]:
        db_row = await self.session.get(AnnouncementDB, announcement_id)
        return self._map_to_entity(db_row) if db_row else None

    async def list_all(self, limit: int = 100) -> list[Announcement]:
        stmt = (
            select(AnnouncementDB)
            .order_by(AnnouncementDB.created_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return [self._map_to_entity(row) for row in result.scalars().all()]

    async def save(self, announcement: Announcement) -> Announcement:
        db_row = AnnouncementDB(
            id=announcement.id,  # Explicit so merge() knows whether to UPDATE
            title_fr=announcement.title_fr,
            title_en=announcement.title_en,
            body_fr=announcement.body_fr,
            body_en=announcement.body_en,
            cta_label_fr=announcement.cta_label_fr,
            cta_label_en=announcement.cta_label_en,
            cta_url=announcement.cta_url,
            icon=announcement.icon,
            is_published=announcement.is_published,
            starts_at=announcement.starts_at,
            ends_at=announcement.ends_at,
            created_at=announcement.created_at,
            updated_at=announcement.updated_at,
        )
        await self.session.merge(db_row)
        return announcement

    async def delete(self, announcement_id: UUID) -> bool:
        # The FK on announcement_views is ON DELETE CASCADE, so the dismissals go
        # with it -- correct here, because the row is being erased, not retired.
        stmt = sql_delete(AnnouncementDB).where(AnnouncementDB.id == announcement_id)
        result = await self.session.execute(stmt)
        return (result.rowcount or 0) > 0

    def _map_to_entity(self, db_row: AnnouncementDB) -> Announcement:
        announcement = Announcement(
            title_fr=db_row.title_fr,
            title_en=db_row.title_en,
            body_fr=db_row.body_fr,
            body_en=db_row.body_en,
            cta_label_fr=db_row.cta_label_fr,
            cta_label_en=db_row.cta_label_en,
            cta_url=db_row.cta_url,
            icon=db_row.icon,
            is_published=db_row.is_published,
            starts_at=db_row.starts_at,
            ends_at=db_row.ends_at,
            created_at=db_row.created_at,
            updated_at=db_row.updated_at,
        )
        announcement.id = db_row.id
        return announcement
