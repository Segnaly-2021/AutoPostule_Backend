# ==============================================================================
# FILE: auto_apply_app/interfaces/viewmodels/agent_vm.py
#
# WHAT IT DOES:
#   Immutable response shapes for agent commands (start, resume, kill) and for the
#   real-time progress events streamed to the UI over SSE.
#
# USED BY:
#   - auto_apply_app/interfaces/presenters/base_presenter.py: AgentPresenter types
#   - auto_apply_app/interfaces/presenters/web.py: builds these view models
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================

from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class AgentViewModel:
    """
    ViewModel for agent operation responses.
    Used for start/resume/kill operations.
    """
    search_id: str
    status: str  
    message: str

@dataclass(frozen=True)
class AgentProgressViewModel:
    """
    ViewModel for SSE progress events.
    Sent during real-time streaming.
    """
    source: str         # 🚨 V2 [NEW]: Tells UI which worker this is (e.g., 'MASTER', 'APEC')
    stage: str  
    node: str   
    status: str 
    search_id: str
    progress_percent: Optional[int] = None  
    error: Optional[str] = None # 🚨 V2: Captures our new circuit breaker fatal errors