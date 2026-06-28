# auto_apply_app/domain/entities/agent_state.py
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
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

    `last_heartbeat` is the liveness signal: the agent stamps it at node entry
    and inside long loops. The reconnection poll judges the agent dead when this
    timestamp goes stale. Distinct from `is_shutdown` (a user-requested stop).
    """
    user_id: UUID = None
    search_id: UUID = None
    is_shutdown: bool = False
    last_heartbeat: Optional[datetime] = None

    def shutdown(self) -> None:
        """Mark this specific search's agent as shut down."""
        self.is_shutdown = True

    def beat(self) -> None:
        """Mark the agent as alive right now."""
        self.last_heartbeat = datetime.now(timezone.utc)