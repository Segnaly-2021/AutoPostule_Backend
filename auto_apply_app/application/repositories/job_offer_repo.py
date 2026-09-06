"""
This module defines the repository interface for JobOffer entity persistence.
"""

from abc import ABC, abstractmethod
from datetime import datetime
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
        user_id: str,
        has_response: bool,
        status: ApplicationStatus = ApplicationStatus.SUBMITTED
    ) -> JobOffer:
        pass

    @abstractmethod
    async def update_interview_status(
        job_id: str,
        user_id: str,
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


    @abstractmethod
    async def count_by_status(
        self,
        status: ApplicationStatus,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        """
        Count offers in a given status across ALL users, optionally windowed by
        application_date in [start, end). Used by the admin dashboard.

        Caveat: application_date is stamped when the offer row is first created (while
        it is still FOUND), not when it is submitted — it is immutable thereafter. Both
        happen inside one agent run, so daily/monthly buckets are near-identical, but
        this is find-time, not submit-time. get_daily_application_count already relies
        on the same approximation.
        """
        pass

    @abstractmethod
    async def count_submitted_between(
        self, user_id: str, start: datetime, end: datetime
    ) -> int:
        """Applications this user actually SENT in [start, end).

        Counts on `submitted_at`, not `application_date`: the latter is stamped
        at find time and is immutable, so an offer found on the last day of a
        cycle and sent on the first day of the next would bill to the wrong one.

        Used to enforce the per-cycle volume allowance, so it must not
        over-count: rows with a NULL submitted_at were never sent.
        """
        pass

    @abstractmethod
    async def get_daily_application_count(self, user_id: str) -> int:
        """
        Get the total number of applications submitted by the user today.
        
        Args:
            user_id: The unique identifier of the user.
            
        Returns:
            int: The number of applications submitted today (from midnight to now).
        """
        pass