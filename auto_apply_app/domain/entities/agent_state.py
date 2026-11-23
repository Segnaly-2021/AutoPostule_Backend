# ==============================================================================
# FILE: auto_apply_app/domain/entities/agent_state.py
#
# WHAT IT DOES:
#   One agent run's control row: the kill-switch flag (is_shutdown) and the
#   heartbeat timestamp the UI uses to decide whether the agent is still alive.
#
# USED BY:
#   - auto_apply_app/application/repositories/agent_state_repo.py: repository interface
#   - auto_apply_app/application/use_cases/agent_state_use_cases.py: kill and liveness logic
#   - auto_apply_app/application/use_cases/agent_use_cases.py: creates the row at start
#   - auto_apply_app/infrastructures/persistence/database/repositories/agent_state_repo_db.py: persistence
#   - auto_apply_app/interfaces/presenters/base_presenter.py: presenter type hints
#   - auto_apply_app/interfaces/presenters/web.py: presents agent state
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
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
        """Mark this search's agent as shut down, and stop it reading as alive.

        Clearing the heartbeat matters for the UI. `is_alive` is only
        'last_heartbeat is recent', so a run whose process has already died
        kept reading alive until the beat went stale -- and the widget sat on
        "Terminating..." for that whole window instead of finishing. Shutdown is
        the one moment we know for certain the agent should not count as alive,
        so we say so rather than waiting for a timeout to infer it.
        """
        self.is_shutdown = True
        self.last_heartbeat = None

    def beat(self) -> None:
        """Mark the agent as alive right now."""
        self.last_heartbeat = datetime.now(timezone.utc)