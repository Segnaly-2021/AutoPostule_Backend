# ==============================================================================
# FILE: auto_apply_app/interfaces/viewmodels/agent_state_vm.py
#
# WHAT IT DOES:
#   Response shapes for agent state: kill-switch status, shutdown confirmation, and
#   liveness. Field names are camelCase because they go directly to the frontend.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/routers/agent.py: response typing
#   - auto_apply_app/interfaces/presenters/base_presenter.py: AgentStatePresenter types
#   - auto_apply_app/interfaces/presenters/web.py: builds these view models
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================

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


@dataclass
class AgentLivenessViewModel:
    searchId: str
    isAlive: bool
    isShutdown: bool
    lastHeartbeat: Optional[str] = None