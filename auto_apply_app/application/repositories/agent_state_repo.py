# auto_apply_app/application/repositories/agent_state_repo.py
from abc import ABC, abstractmethod
from uuid import UUID
from typing import Optional

from auto_apply_app.domain.entities.agent_state import AgentState


class AgentStateRepository(ABC):
    """
    Repository for managing agent state.
    """

    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> Optional[AgentState]:
        """Retrieve agent state for a specific user."""
        pass

    @abstractmethod
    async def save(self, agent_state: AgentState) -> None:
        """Save or update agent state."""
        pass

    @abstractmethod
    async def delete(self, user_id: UUID) -> None:
        """Delete agent state (e.g., when user is deleted)."""
        pass