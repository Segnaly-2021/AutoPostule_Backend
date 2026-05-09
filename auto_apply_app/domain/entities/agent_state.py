# auto_apply_app/domain/entities/agent_state.py
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity


@dataclass
class AgentState(Entity):
    """
    Kill-switch for ONE specific agent run.
    
    One row per (user_id, search_id). Created when a search starts,
    updated only when shutdown is requested. Old rows persist —
    they're a historical record, not a single mutable flag.
    
    No 'binding' or 'rebinding' — each row IS a specific search's state.
    Race conditions between concurrent searches are impossible by construction.
    """
    user_id: UUID = None
    search_id: UUID = None
    is_shutdown: bool = False

    def shutdown(self) -> None:
        """Mark this specific search's agent as shut down."""
        self.is_shutdown = True