# auto_apply_app/infrastructures/persistence/database/repositories/agent_state_repo_db.py
from uuid import UUID
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.agent_state import AgentState
from auto_apply_app.application.repositories.agent_state_repo import AgentStateRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import AgentStateDB




class AgentStateRepoDB(AgentStateRepository):

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: UUID) -> Optional[AgentState]:
        result = await self.session.execute(
            select(AgentStateDB).where(AgentStateDB.user_id == user_id)
        )
        agent_state_db = result.scalar_one_or_none()
        if agent_state_db is None:
            return None
        return self._map_to_entity(agent_state_db)

    async def save(self, agent_state: AgentState) -> None:
        agent_state_db = AgentStateDB(
            user_id=agent_state.user_id,
            is_shutdown=agent_state.is_shutdown,
        )
        await self.session.merge(agent_state_db)

    async def delete(self, user_id: UUID) -> None:
        result = await self.session.execute(
            select(AgentStateDB).where(AgentStateDB.user_id == user_id)
        )
        agent_state_db = result.scalar_one_or_none()
        if agent_state_db:
            await self.session.delete(agent_state_db)

    def _map_to_entity(self, agent_state_db: AgentStateDB) -> AgentState:
        state = AgentState(user_id=agent_state_db.user_id)
        state.is_shutdown = agent_state_db.is_shutdown
        return state