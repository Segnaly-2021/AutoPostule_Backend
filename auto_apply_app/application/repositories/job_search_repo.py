# ==============================================================================
# FILE: auto_apply_app/application/repositories/job_search_repo.py
#
# WHAT IT DOES:
#   Interface for job searches (one agent mission each) and their matched jobs.
#   The database implementation's list_recent_by_user takes `statuses` (a list),
#   which is what callers pass, rather than the single `status` declared here.
#
# USED BY:
#   - auto_apply_app/application/repositories/unit_of_work.py: exposes it as search_repo
#   - auto_apply_app/application/use_cases/job_search_use_cases.py: type for direct injection
#   - auto_apply_app/infrastructures/persistence/database/repositories/job_search_repo_db.py: implementation
#   - auto_apply_app/infrastructures/persistence/in_memory/memory.py: in-memory implementation
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
"""
This module defines the repository interface for JobSearch entity persistence.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.value_objects import SearchStatus



class JobSearchRepository(ABC):
    """Repository interface for JobSearch entity persistence."""

    @abstractmethod
    async def get(self, search_id: UUID) -> JobSearch:
        """
        Retrieve a job search by its ID.

        Args:
            search_id: The unique identifier of the job

        Returns:
            The requested JobSearch entity

        Raises:
            JobSearchNotFoundError: If no job exists with the given ID
        """
        pass

    @abstractmethod
    async def get_all_jobs(self) -> list[JobOffer]:
        """
        Retrieve all matched Jobs.
        """
        pass

    @abstractmethod
    async def save(self, search: JobSearch) -> None:
        """
        Save a job search to the repository.

        Args:
            search: The JobSearch entity to save
        """
        pass

    @abstractmethod
    async def save_all_jobs(self, search: JobSearch) -> None:
        """
        Save all matched job to the repository.

        Args:
            search: The JobSearch entity to save
        """
        pass

    @abstractmethod
    async def delete_job(self, job_id: UUID) -> None:
        """
        Delete a job  from the search repository.

        Args:
            job_id: The unique identifier of the job  to delete
        """
        pass

    @abstractmethod
    async def list_recent_by_user(
        self,
        user_id: UUID,
        status: SearchStatus,
        limit: int = 5,
    ) -> list[JobSearch]:
        """
        Retrieve the most recent job searches for a user, filtered by status,
        ordered by updated_at descending. Does NOT hydrate matched jobs.

        Args:
            user_id: Owner of the searches
            status: SearchStatus to filter on (e.g. SEARCHING)
            limit: Max rows to return (default 5)

        Returns:
            List of JobSearch entities (possibly empty), newest first.
        """
        pass

    