# auto_apply_app/infrastructures/persistence/database/repositories/agent_usage_repo_db.py
from datetime import datetime, timezone
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.agent_usage import AgentUsage
from auto_apply_app.application.repositories.agent_usage_repo import AgentUsageRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import AgentUsageDB


class AgentUsageRepoDB(AgentUsageRepository):

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_for_today(self, user_id: UUID) -> AgentUsage:
        """
        Atomic UPSERT for today's usage row.
        
        Uses Postgres ON CONFLICT (user_id, usage_date) DO NOTHING:
        - If no row exists: inserts a fresh one with runs_count=0
        - If a row already exists: leaves it untouched
        Either way, the SELECT after returns the canonical row.
        Safe under concurrent calls.
        """
        today = datetime.now(timezone.utc).date()

        # Try to insert; do nothing if it already exists
        stmt = (
            pg_insert(AgentUsageDB)
            .values(user_id=user_id, usage_date=today, runs_count=0)
            .on_conflict_do_nothing(index_elements=["user_id", "usage_date"])
        )
        await self.session.execute(stmt)

        # Fetch the canonical row (whether we just inserted or it existed)
        result = await self.session.execute(
            select(AgentUsageDB).where(
                AgentUsageDB.user_id == user_id,
                AgentUsageDB.usage_date == today,
            )
        )
        usage_db = result.scalar_one()
        return self._map_to_entity(usage_db)

    async def save(self, usage: AgentUsage) -> None:
        usage_db = AgentUsageDB(
            id=usage.id,
            user_id=usage.user_id,
            usage_date=usage.usage_date,
            runs_count=usage.runs_count,
            last_completed_at=usage.last_completed_at,
        )
        await self.session.merge(usage_db)

    def _map_to_entity(self, usage_db: AgentUsageDB) -> AgentUsage:
        usage = AgentUsage(
            user_id=usage_db.user_id,
            usage_date=usage_db.usage_date,
            runs_count=usage_db.runs_count,
            last_completed_at=usage_db.last_completed_at,
        )
        usage.id = usage_db.id
        return usage