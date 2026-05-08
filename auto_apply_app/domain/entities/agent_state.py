# auto_apply_app/domain/entities/agent_state.py
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity


@dataclass
class AgentState(Entity):
    """
    Kill-switch for an in-flight agent run.
    
    One row per user, lifetime = forever, rebinds on each new search.
    The search_id binding prevents stale shutdown signals from killing
    a fresh run, and prevents a fresh run from accidentally un-killing
    a still-shutting-down run.
    """
    user_id: UUID = None
    search_id: UUID | None = None  # Bound to the current/last run
    is_shutdown: bool = False

    def bind_to_search(self, search_id: UUID) -> None:
        """
        Called at the start of a new run.
        Atomically binds the new search and clears any stale shutdown flag.
        """
        self.search_id = search_id
        self.is_shutdown = False

    def request_shutdown(self, search_id: UUID) -> bool:
        """
        Caller must provide the search_id they intend to kill.
        Returns True if the shutdown was applied, False if it was rejected
        (because the bound search_id doesn't match — the run is stale).
        """
        if self.search_id is None or self.search_id != search_id:
            return False
        self.is_shutdown = True
        return True

    def is_killed_for(self, search_id: UUID) -> bool:
        """
        Workers call this to check whether they should stop.
        Only returns True if the shutdown is for THIS specific search.
        """
        return self.is_shutdown and self.search_id == search_id