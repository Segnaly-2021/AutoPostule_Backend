# ==============================================================================
# FILE: auto_apply_app/interfaces/viewmodels/job_offer_vm.py
#
# WHAT IT DOES:
#   Immutable response shapes for job offers: application history items, the premium
#   review card, the paginated dashboard, and the daily application count.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/routers/agent.py: response typing
#   - auto_apply_app/interfaces/presenters/base_presenter.py: JobPresenter types
#   - auto_apply_app/interfaces/presenters/web.py: builds these view models
#   - auto_apply_app/interfaces/viewmodels/job_search_vm.py: nests JobOfferViewModel
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================

from dataclasses import dataclass
from typing import Optional, List
#from auto_apply_app.domain.value_objects import ApplicationStatus, JobBoard


@dataclass(frozen=True)
class JobOfferViewModel:
    """
    ViewModel for a single job offer.
    Used in both review interface and application history.
    """
    id: str  
    company: str
    title: str
    location: Optional[str]
    interview: bool
    response: bool

    cover_letter: str # Just added
    job_url: str      # Just added


    board: str  # "APEC", "HELLOWORK", "WTTJ"
    status: str  # "FOUND", "GENERATED", "APPROVED", "SUBMITTED", "REJECTED"
    
    # Optional fields
    followUpDate: Optional[str] = None
    appliedDate: Optional[str] = None


@dataclass(frozen=True)
class JobReviewViewModel:
    """ViewModel specifically for the Premium Review interface."""
    id: str
    company_name: str
    job_title: str
    location: Optional[str]
    cover_letter: str
    ranking: int
    url: str
    board: str
    status: str


@dataclass(frozen=True)
class DashboardViewModel:
    applications: List[JobOfferViewModel]
    total: int  # Filtered count
    total_unfiltered: int  # ✅ All user applications (not affected by filters)
    top_titles: List[dict]  # ✅ [{"name": "Engineer", "value": 15}, ...]
    page: int
    limit: int
    total_pages: int
    

@dataclass(frozen=True)
class DailyStatsViewModel:
    """ViewModel for the daily application count stats."""
    count: int