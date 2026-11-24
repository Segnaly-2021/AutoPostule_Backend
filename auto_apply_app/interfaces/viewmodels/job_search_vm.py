# ==============================================================================
# FILE: auto_apply_app/interfaces/viewmodels/job_search_vm.py
#
# WHAT IT DOES:
#   Immutable response shapes for job searches: full detail, the compact recent
#   searches item, and a one-field terminal status.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/routers/agent.py: response typing
#   - auto_apply_app/interfaces/controllers/job_search_controllers.py: return type
#   - auto_apply_app/interfaces/presenters/base_presenter.py: JobSearchPresenter types
#   - auto_apply_app/interfaces/presenters/web.py: builds these view models
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================

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






@dataclass(frozen=True)
class JobSearchSummaryViewModel:
    """Compact ViewModel for the 'recent searches' list (README §4a)."""
    id: str
    job_title: str
    status: str
    job_boards: List[str]
    contract_types: List[str]
    location: str
    min_salary: int
    updated_at: Optional[str] = None


@dataclass(frozen=True)
class SearchStatusViewModel:
    """Single-field VM for terminal disambiguation (complete vs failed)."""
    searchStatus: str