# auto_apply_app/infrastructures/persistence/database/repositories/free_search_usage_repo_db.py
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.free_search_usage import FreeSearchUsage
from auto_apply_app.application.repositories.free_search_usage_repo import FreeSearchUsageRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import FreeSearchUsageDB


class FreeSearchUsageRepoDB(FreeSearchUsageRepository):

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_for_today(self, user_id: UUID) -> FreeSearchUsage:
        today = datetime.now(timezone.utc).date()

        stmt = (
            pg_insert(FreeSearchUsageDB)
            .values(user_id=user_id, usage_date=today, searches_count=0)
            .on_conflict_do_nothing(index_elements=["user_id", "usage_date"])
        )
        await self.session.execute(stmt)

        result = await self.session.execute(
            select(FreeSearchUsageDB).where(
                FreeSearchUsageDB.user_id == user_id,
                FreeSearchUsageDB.usage_date == today,
            )
        )
        usage_db = result.scalar_one()
        return self._map_to_entity(usage_db)

    async def save(self, usage: FreeSearchUsage) -> None:
        usage_db = FreeSearchUsageDB(
            id=usage.id,
            user_id=usage.user_id,
            usage_date=usage.usage_date,
            searches_count=usage.searches_count,
        )
        await self.session.merge(usage_db)

    def _map_to_entity(self, usage_db: FreeSearchUsageDB) -> FreeSearchUsage:
        usage = FreeSearchUsage(
            user_id=usage_db.user_id,
            usage_date=usage_db.usage_date,
            searches_count=usage_db.searches_count,
        )
        usage.id = usage_db.id
        return usage