# ==============================================================================
# FILE: auto_apply_app/infrastructures/persistence/database/repositories/agent_state_repo_db.py
#
# WHAT IT DOES:
#   Stores each run's control record: its kill flag (is_shutdown) and last heartbeat.
#   This database row is how the API and a worker in another container coordinate:
#   the API sets the flag, and the worker writes heartbeats and checks for a kill.
#
# USED BY:
#   - auto_apply_app/infrastructures/persistence/database/repositories/unit_of_work_repo_db.py: uow.agent_state_repo
#
# EXTERNAL DEPENDENCIES:
#   - SQLAlchemy: async queries and ORM
#   - PostgreSQL (Supabase): the agent_states table
# ==============================================================================
# auto_apply_app/infrastructures/persistence/database/repositories/agent_state_repo_db.py
from datetime import datetime, timezone
from uuid import UUID
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.agent_state import AgentState
from auto_apply_app.application.repositories.agent_state_repo import AgentStateRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import AgentStateDB


class AgentStateRepoDB(AgentStateRepository):

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_search_id(self, search_id: UUID) -> Optional[AgentState]:
        result = await self.session.execute(
            select(AgentStateDB).where(AgentStateDB.search_id == search_id)
        )
        agent_state_db = result.scalar_one_or_none()
        if agent_state_db is None:
            return None
        return self._map_to_entity(agent_state_db)

    async def save(self, agent_state: AgentState) -> None:
        agent_state_db = AgentStateDB(
            id=agent_state.id,
            user_id=agent_state.user_id,
            search_id=agent_state.search_id,
            is_shutdown=agent_state.is_shutdown,
            last_heartbeat=agent_state.last_heartbeat,
        )
        # merge() = load by primary key, then update it, or insert if it does not exist.
        # It writes every field, so use the targeted UPDATEs below on a live run.
        await self.session.merge(agent_state_db)

    async def touch_heartbeat(self, search_id: UUID) -> None:
        # Field-scoped UPDATE: only last_heartbeat. Never reads/writes is_shutdown,
        # so a concurrent heartbeat cannot clobber a kill committed in between.
        await self.session.execute(
            update(AgentStateDB)
            .where(AgentStateDB.search_id == search_id)
            .values(last_heartbeat=datetime.now(timezone.utc))
        )

    async def set_shutdown(self, search_id: UUID) -> bool:
        # Field-scoped UPDATE. last_heartbeat is cleared in the SAME statement so
        # a heartbeat racing this write cannot resurrect the run: after shutdown
        # the agent must not read as alive, and `is_alive` is derived purely from
        # heartbeat freshness. Without this the widget showed "Terminating..."
        # until the last beat aged out.
        result = await self.session.execute(
            update(AgentStateDB)
            .where(AgentStateDB.search_id == search_id)
            .values(is_shutdown=True, last_heartbeat=None)
        )
        return (result.rowcount or 0) > 0

    def _map_to_entity(self, agent_state_db: AgentStateDB) -> AgentState:
        state = AgentState(
            user_id=agent_state_db.user_id,
            search_id=agent_state_db.search_id,
        )
        state.id = agent_state_db.id
        state.is_shutdown = agent_state_db.is_shutdown
        state.last_heartbeat = agent_state_db.last_heartbeat
        return state