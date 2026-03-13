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