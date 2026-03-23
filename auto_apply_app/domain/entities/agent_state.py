# auto_apply_app/domain/entities/agent_state.py
from dataclasses import dataclass
from uuid import UUID

@dataclass
class AgentState:
    """
    Represents agent state and will allow us to kill the agent.
    """
    user_id: UUID

    # Agent State
    is_shutdown: bool = False

    # Business Logic
    def shutdown(self) -> None:
        """Signal the agent to stop."""
        self.is_shutdown = True

    def reset(self) -> None:
        """Reset the agent state for a new run."""
        self.is_shutdown = False