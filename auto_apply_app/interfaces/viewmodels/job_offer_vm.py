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