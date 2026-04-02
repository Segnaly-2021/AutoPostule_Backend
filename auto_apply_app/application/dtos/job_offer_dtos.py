from dataclasses import dataclass
from typing import Optional, Dict, Self
from uuid import UUID
from datetime import datetime, date

from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import JobBoard, ApplicationStatus


@dataclass(frozen=True)
class CreateJobOfferRequest:
    """Data carrier: Application Layer -> Domain Layer"""

    company_name: str
    job_title: str
    location: str
    job_board: JobBoard
    url: str
    form_url: str
    

    def __post_init__(self) -> None:
        if not self.url.strip():
            raise ValueError("URL is required")
        
        if not self.form_url.strip():
            raise ValueError("Form URL is required")
        
        if not self.company_name.strip():
            raise ValueError("Company name is required")

        if not self.job_title.strip():
            raise ValueError("Job title is required")

        if not self.location.strip():
            raise ValueError("Location is required")

        if not isinstance(self.job_board, JobBoard):
            raise ValueError("job_board must be a JobBoard value")


    def to_execution_params(self) -> Dict:
        return {
            "company_name": self.company_name.strip(),
            "job_title": self.job_title.strip(),
            "location": self.location.strip(),
            "job_board": self.job_board,
            "url": self.url.strip(),
            "form_url": self.form_url.strip(),
        }
        
        
    

@dataclass(frozen=True)
class ApplyToJobOfferRequest:
    """Data carrier: Application Layer -> Domain Layer"""

    job_offer_id: str
    user_id: str

    def __post_init__(self) -> None:
        if not self.job_offer_id.strip():
            raise ValueError("Job offer ID is required")

        if not self.user_id.strip():
            raise ValueError("User ID is required")

    def to_execution_params(self) -> Dict:
        return {
            "job_offer_id": UUID(self.job_offer_id),
            "user_id": UUID(self.user_id),
        }
    


@dataclass(frozen=True)
class GetJobOfferRequest:
    """Data carrier: Application Layer -> Domain Layer"""

    job_offer_id: str

    def __post_init__(self) -> None:
        if not self.job_offer_id.strip():
            raise ValueError("Job offer ID is required")

    def to_execution_params(self) -> Dict:
        return {"job_offer_id": UUID(self.job_offer_id)}



@dataclass(frozen=True)
class DeleteJobOfferRequest:
    """Data carrier: Application Layer -> Domain Layer"""

    job_offer_id: str

    def __post_init__(self) -> None:
        if not self.job_offer_id.strip():
            raise ValueError("Job offer ID is required")

    def to_execution_params(self) -> Dict:
        return {"job_offer_id": UUID(self.job_offer_id)}
    

@dataclass(frozen=True)
class GetUserApplicationsRequest:
    user_id: str
    page: int
    limit: int
    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    board: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    has_response: Optional[bool] = None
    has_interview: Optional[bool] = None

    def to_execution_params(self) -> Dict:
        # Transforms the DTO into the dict format the Repo expects
        filters = {
            "company": self.company,
            "title": self.title,
            "location": self.location,
            "board": self.board,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "has_response": self.has_response,
            "has_interview": self.has_interview,
        }
        # Remove None values to keep the query clean
        clean_filters = {k: v for k, v in filters.items() if v is not None}
        
        return {
            "user_id": self.user_id,
            "filters": clean_filters,
            "pagination": {"page": self.page, "limit": self.limit}
        }

@dataclass(frozen=True)
class ToggleStatusRequest:
    job_offer_id: str
    status: bool

@dataclass(frozen=True)
class GetAnalyticsRequest:
    user_id: str
    period: str

@dataclass(frozen=True)
class GetDailyStatsRequest:
    user_id: str

    def to_execution_params(self) -> dict:
        return {"user_id": self.user_id}

@dataclass(frozen=True)
class JobOfferResponse:
    """Data carrier: Domain Layer -> Application Layer"""

    id: str
    url: str
    formUrl: str
    board: JobBoard
    ranking: Optional[int]
    coverLetter: Optional[str]
    jobDesc: Optional[str]
    company: Optional[str]
    title: Optional[str]
    location: Optional[str]
    searchId: Optional[str]
    userId: Optional[str]
    interview: Optional[bool]
    response: Optional[bool]
    jobPostingId: Optional[str]
    appliedDate:Optional[datetime]
    status: Optional[ApplicationStatus]
    followUpDate: Optional[datetime]

    @classmethod
    def from_entity(cls, job_offer: JobOffer) -> Self:
        return cls(
            id=str(job_offer.id),
            company=job_offer.company_name,
            url=job_offer.url,
            formUrl=job_offer.form_url,    
            ranking=job_offer.ranking,
            coverLetter=job_offer.cover_letter,
            jobDesc=job_offer.job_desc,
            title=job_offer.job_title,
            location=job_offer.location,
            searchId=job_offer.search_id,
            userId=job_offer.user_id,
            response=job_offer.has_response,
            interview=job_offer.has_interview,
            board=job_offer.job_board,
            jobPostingId=job_offer._job_posting_id,
            appliedDate=job_offer.application_date,
            status=job_offer.status,
            followUpDate=job_offer.followup_date,
        )

