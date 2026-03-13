from dataclasses import dataclass
from typing import Optional, Dict, List, Self
from uuid import UUID

from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.value_objects import ContractType, SearchStatus

@dataclass(frozen=True)
class CreateJobSearchRequest:
    """Data carrier: Application Layer -> Domain Layer"""

    job_title: str
    job_seeker_id: str  # UUID as string
    contract_types: List[ContractType]= None

    def __post_init__(self) -> None:
        if not self.job_title.strip():
            raise ValueError("Job title is required")

        if not self.job_seeker_id.strip():
            raise ValueError("Job seeker ID is required")

        if self.contract_types is not None:
            if not isinstance(self.contract_types, list):
                raise ValueError("contract_types must be a list")
            for c in self.contract_types:
                if not isinstance(c, ContractType):
                    raise ValueError("contract_types must contain ContractType values")

    def to_execution_params(self) -> Dict:
        params = {
            "job_title": self.job_title.strip(),
            "job_seeker_id": UUID(self.job_seeker_id),
        }

        if self.contract_types:
            params["contract_types"] = self.contract_types

        return params



@dataclass(frozen=True)
class GetJobSearchRequest:
    """Data carrier: Application Layer -> Domain Layer"""

    job_search_id: str

    def __post_init__(self) -> None:
        if not self.job_search_id.strip():
            raise ValueError("Job search ID is required")

    def to_execution_params(self) -> Dict:
        return {"job_search_id": UUID(self.job_offer_id)}


@dataclass(frozen=True)
class JobSearchResponse:
    """Data carrier: Domain Layer -> Application Layer"""

    job_search_id: str
    job_title: str
    job_seeker_id: str
    search_status: SearchStatus 
    contract_types: List[ContractType]
    matched_jobs: Dict[str, Dict]  # optional: serialized JobOffer info

    @classmethod
    def from_entity(cls, job_search: JobSearch) -> Self:
        return cls(
            job_search_id=str(job_search.id),
            job_title=job_search.job_title,
            job_seeker_id=str(job_search.job_seeker.id),
            search_status=job_search.search_status,
            contract_types=list(job_search.contract_type),
            matched_jobs={                
                key: 
                {
                    "job_id": str(job.id),
                    "title": job.job_title,
                    "company": job.company_name,    
                    "location": job.location,
                    "job_posting_id": job.get_job_posting_id(),
                    # Add any fields you want from JobOffer
                }
                for key, job in job_search._matched_jobs.items()
            },
        )



@dataclass(frozen=True)
class UpdateJobSearchRequest:
    """Request data for an update: Application Layer -> Domain Layer"""

    job_search_id: str
    job_title: Optional[str] = None
    search_status: SearchStatus = None
    contract_types: Optional[List[ContractType]] = None

    def __post_init__(self) -> None:
        if not self.job_search_id.strip():
            raise ValueError("Job search ID is required")

        if self.job_title is not None and not self.job_title.strip():
            raise ValueError("Job title cannot be empty")
        
        if not isinstance(self.search_status, SearchStatus):
            raise ValueError("Search status should be instance of SearchStatus type.")

        if self.contract_types is not None:
            if not isinstance(self.contract_types, list):
                raise ValueError("contract_types must be a list")
            for c in self.contract_types:
                if not isinstance(c, ContractType):
                    raise ValueError("contract_types must contain ContractType values")

    def to_execution_params(self) -> Dict:
        params = {"job_search_id": UUID(self.job_search_id)}

        if self.job_title is not None:
            params["job_title"] = self.job_title.strip()
            
        if self.search_status is not None:
            params["status"] = self.search_status

        if self.contract_types is not None:
            params["contract_types"] = self.contract_types

        return params
