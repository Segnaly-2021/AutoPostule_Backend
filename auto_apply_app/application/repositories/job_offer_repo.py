"""
This module defines the repository interface for JobOffer entity persistence.
"""

from abc import ABC, abstractmethod
from typing import Set, List, Optional, Tuple
from uuid import UUID

from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import ApplicationStatus

class JobOfferRepository(ABC):
    """Repository interface for JobOffer entity persistence."""

    @abstractmethod
    async def get(self, job_id: UUID) -> JobOffer:
        """Retrieve a job offer by its ID."""
        pass

    @abstractmethod
    async def get_recent_application_hashes(self, user_id: UUID, days: int = 14) -> Set[str]:
        """Performance-optimized query for deduplication (fingerprints only)."""
        pass

    @abstractmethod
    async def save(self, job: JobOffer) -> None:
        """Save or update a single job offer."""
        pass

    # --- NEW METHODS FOR BATCH/REVIEW FLOW ---

    @abstractmethod
    async def save_all(self, jobs: List[JobOffer]) -> None:
        """
        Bulk save/update a list of job offers.
        
        Essential for the 'Writer Node' to persist a batch of drafts efficiently
        before the system pauses for Premium review.
        """
        pass

    @abstractmethod
    async def get_by_search(self, search_id: UUID, status: Optional[ApplicationStatus] = None) -> List[JobOffer]:
        """
        Retrieve job offers by their search ID, optionally filtering by status.

        Args:
            search_id: The unique identifier of the job search (batch).
            status: (Optional) Filter by status (e.g., fetch only APPROVED jobs).

        Returns:
            List of JobOffer entities.
        """
        pass

    @abstractmethod
    async def get_by_search_and_status(
        self, 
        search_id: str, 
        status: ApplicationStatus
    ) -> List[JobOffer]:
        """
        Fetch all jobs for a specific search with a specific status.
        
        Used for Premium review flow to get GENERATED jobs.
        """
        pass

    @abstractmethod
    async def delete(self, job_id: UUID) -> None:
        """Delete a job offer from the repository."""
        pass

    @abstractmethod
    async def delete_by_search_and_status(self, search_id: UUID, status: ApplicationStatus) -> int:
        """
        Delete all job offers for a given search ID that match a specific status.
        Returns the number of deleted records.
        """
        pass

    
    @abstractmethod
    async def get_total_job(self) -> int:
        pass

    
    @abstractmethod
    async def get_user_applications(
        user_id: str, 
        filters: dict, 
        pagination: dict,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> Tuple[List[JobOffer], int]:
        pass

    @abstractmethod
    async def update_response_status(
        job_id: str, 
        has_response: bool,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> JobOffer:
        pass

    @abstractmethod
    async def update_interview_status(
        job_id: str, 
        has_interview: bool,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> JobOffer:
        pass


    async def get_analytics(
        user_id: str, 
        period: str,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> dict: 
    
        pass


    