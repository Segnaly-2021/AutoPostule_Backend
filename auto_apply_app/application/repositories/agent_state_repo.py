# auto_apply_app/application/repositories/agent_state_repo.py
from abc import ABC, abstractmethod
from uuid import UUID
from typing import Optional

from auto_apply_app.domain.entities.agent_state import AgentState


class AgentStateRepository(ABC):
    """
    Repository for managing per-search agent kill-switch state.
    One row per (user_id, search_id) — search_id is unique.
    """

    @abstractmethod
    async def get_by_search_id(self, search_id: UUID) -> Optional[AgentState]:
        """Retrieve the kill-switch state for a specific search."""
        pass

    @abstractmethod
    async def save(self, agent_state: AgentState) -> None:
        """Save or update an agent state row."""
        pass