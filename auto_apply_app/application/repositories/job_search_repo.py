"""
This module defines the repository interface for JobSearch entity persistence.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer



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

   