# ==============================================================================
# FILE: auto_apply_app/domain/entities/job_search.py
#
# WHAT IT DOES:
#   One agent mission: what to search for, on which boards, and its status
#   (PENDING -> SEARCHING -> COMPLETED, or PAUSED / CANCELLED / FAILED).
#   Also holds the job offers matched during the run.
#
# USED BY:
#   - auto_apply_app/application/use_cases/agent_use_cases.py: creates and controls searches
#   - auto_apply_app/application/use_cases/run_context_use_cases.py: loads for a run
#   - auto_apply_app/application/repositories/job_search_repo.py: repository interface
#   - auto_apply_app/application/dtos/job_search_dtos.py: maps to responses
#   - auto_apply_app/application/dtos/agent_dtos.py: builds AgentResponse
#   - auto_apply_app/application/service_ports/agent_port.py: agent method signatures
#   - auto_apply_app/infrastructures/agent/master/master_agent.py: run orchestration
#   - auto_apply_app/infrastructures/agent/state.py: agent state type
#   - ... and 7 more
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
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
    # NOTE: possible issue: this default is computed once at import, so every new
    # search gets the process start time (default_factory would fix it).
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
