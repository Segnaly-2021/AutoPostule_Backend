# auto_apply_app/interfaces/viewmodels/agent_state_vm.py
from dataclasses import dataclass


@dataclass
class AgentStateViewModel:
    """
    View Model for agent state.
    Matches the exact JSON structure required by the React frontend.
    """
    isShutdown: bool


@dataclass
class AgentStateMessageViewModel:
    """
    View Model for agent state mutation responses (shutdown/reset).
    """
    message: str
    isShutdown: bool