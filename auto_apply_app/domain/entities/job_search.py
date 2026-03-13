from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID
from typing import Dict, List

from auto_apply_app.domain.entities.entity import Entity
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import ContractType, SearchStatus, JobBoard




@dataclass
class JobSearch(Entity):
    job_title: str     
    user_id: UUID
    job_boards: List[JobBoard]
    _matched_jobs: Dict[UUID, JobOffer] = field(default_factory=dict)
    search_status: SearchStatus = field(default=SearchStatus.PENDING)
    contract_types: List[ContractType] = field(default=None)
    min_salary: int = field(default=0)
    location: str= field(default="")
    updated_at: datetime = field(default=datetime.now(timezone.utc))  
    
    def start_searching(self) -> None:
        if self.search_status != SearchStatus.PENDING:
            raise ValueError("Job search without a PENDING status cannot be started")
        self.search_status = SearchStatus.SEARCHING

    def complete_search(self) -> None:
        if self.search_status == SearchStatus.COMPLETED:
            raise ValueError("We're already done searching")
        
        if self.search_status == SearchStatus.PENDING:
            raise ValueError("Search not yet started")
        
        self.search_status = SearchStatus.COMPLETED

    def cancel(self):
        """Mark search as cancelled (killed by user)."""
        self.search_status = SearchStatus.CANCELLED
        self.updated_at = datetime.now()

    def add_job(self, job: JobOffer) -> None:
        """Add a job by id."""
        if job.id in self._matched_jobs:
            raise ValueError("Job with id {job.id} already exists")        
        self._matched_jobs[job.id] = job

    def get_job(self, job_id: UUID) -> JobOffer:
        job = self._matched_jobs.get(job_id)
        if job is None:
            raise KeyError(f"Job with id {job_id} not found")
        return job

    def delete_job(self, job_id: UUID) -> None:
        """Remove job; raise KeyError if not found."""
        try:
            self._matched_jobs.pop(job_id)
        except KeyError:
            raise KeyError(f"Job with id {job_id} not found")
        
    def add_contract_type(self, contract: ContractType) -> None:
        if contract in self.contract_type:
            raise ValueError("Contract type {contract} is already considered")
        self.contract_type.append(contract)

    @property
    def all_matched_jobs(self) -> List[JobOffer]:
        """Return a list copy of all job offers."""
        return list(self._matched_jobs.values())

    def __contains__(self, job_id: str) -> bool:
        return job_id in self._matched_jobs

    def __len__(self) -> int:
        return len(self._matched_jobs)
