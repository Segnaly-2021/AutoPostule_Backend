# auto_apply_app/interfaces/viewmodels/agent_state_vm.py
from dataclasses import dataclass
from typing import Optional


@dataclass
class AgentStateViewModel:
    isShutdown: bool
    searchId: Optional[str] = None  # NEW


@dataclass
class AgentStateMessageViewModel:
    message: str
    isShutdown: bool
    searchId: Optional[str] = None  # NEW