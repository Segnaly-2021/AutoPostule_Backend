from dataclasses import dataclass
from typing import Optional, List
from auto_apply_app.interfaces.viewmodels.job_offer_vm import JobOfferViewModel


@dataclass(frozen=True)
class JobSearchViewModel:
    """
    ViewModel for a complete job search session.
    Used when fetching search details or history.
    """
    id: str
    user_id: str
    job_title: str
    job_board: str
    status: str  # "PENDING", "RUNNING", "PAUSED", "COMPLETED", "CANCELLED"
    
    # Metadata
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    
    # Statistics
    total_jobs_found: int = 0
    jobs_applied: int = 0
    jobs_pending_review: int = 0
    
    # Optional: Include jobs list (for detail view)
    jobs: Optional[List[JobOfferViewModel]] = None