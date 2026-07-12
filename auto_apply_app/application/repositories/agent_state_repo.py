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
        """Save or update an agent state row (writes the whole row).

        Use only for create/seed. For concurrent hot-path updates prefer the
        field-scoped methods below so parallel workers cannot clobber each
        other's disjoint columns (a heartbeat must never overwrite is_shutdown).
        """
        pass

    @abstractmethod
    async def touch_heartbeat(self, search_id: UUID) -> None:
        """Field-scoped write: set last_heartbeat=now for this search only.

        Never touches is_shutdown, so a frequent worker heartbeat cannot erase a
        concurrently-committed kill. No-op if the row does not exist.
        """
        pass

    @abstractmethod
    async def set_shutdown(self, search_id: UUID) -> bool:
        """Field-scoped write: set is_shutdown=TRUE for this search only.

        Returns True if a row was updated, False if no row matched.
        """
        pass