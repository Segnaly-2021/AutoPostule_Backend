# auto_apply_app/infrastructures/agent/fake_state.py

from typing import TypedDict, List


class FakeAgentState(TypedDict):
    """
    State for the free/demo agent.
    No authentication, no persistence, no user context.
    """
    # Input
    query: str          # e.g., "Product Manager"
    target_count: int   # 10, 20, or 50
    
    # Output Buffer
    found_jobs: List[dict]  # List of JobSnippet.to_dict() results
    
    # Status Tracking
    status: str         # "searching", "complete", "error"
    current_board: str  # Which board is currently being scraped
    total_found: int    # Running count