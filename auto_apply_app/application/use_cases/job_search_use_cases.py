import logging
from dataclasses import dataclass

from auto_apply_app.application.repositories.job_search_repo import JobSearchRepository
from auto_apply_app.application.repositories.user_repo import UserRepository
from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.dtos.job_search_dtos import (
    CreateJobSearchRequest,
    JobSearchResponse,
    GetJobSearchRequest,
    UpdateJobSearchRequest,
)
from auto_apply_app.application.dtos.operations import DeletionOutcome
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.exceptions import (
    JobSearchNotFoundError,
    ValidationError,
    BusinessRuleViolation,
)

logger = logging.getLogger(__name__)


@dataclass
class CreateJobSearchUseCase:

    job_search_repository: JobSearchRepository
    user_repository: UserRepository  # needed to load job seeker User
        

    def execute(self, request: CreateJobSearchRequest) -> Result:
        try:
            params = request.to_execution_params()

            # load User entity
            job_seeker = self.user_repository.get(params["job_seeker_id"])

            if job_seeker is not None:
                job_search = JobSearch(
                    job_title=params["job_title"],
                    job_seeker=job_seeker
                )

            # optional contract types
            for c in params.get("contract_types", []):
                job_search.add_contract_type(c)

            self.job_search_repository.save(job_search)
            return Result.success(JobSearchResponse.from_entity(job_search))

        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
        except BusinessRuleViolation as e:
            return Result.failure(Error.business_rule_violation(str(e)))
        except Exception:
            logger.exception("CreateJobSearchUseCase failed")
            return Result.failure(Error.system_error("An unexpected error occurred while creating the job search."))


@dataclass
class GetJobSearchUseCase:

    job_search_repository: JobSearchRepository

    def execute(self, request: GetJobSearchRequest) -> Result:
        try:
            params = request.to_execution_params()
            job_search = self.job_search_repository.get(params["job_search_id"])
            return Result.success(JobSearchResponse.from_entity(job_search))

        except JobSearchNotFoundError:
            return Result.failure(
                Error.not_found("JobSearch", str(params["job_search_id"]))
            )
        except Exception:
            logger.exception("GetJobSearchUseCase failed")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving the job search."))


@dataclass
class UpdateJobSearchUseCase:

    job_search_repository: JobSearchRepository

    def execute(self, request: UpdateJobSearchRequest) -> Result:
        try:
            params = request.to_execution_params()

            job_search = self.job_search_repository.get(params["job_search_id"])

            # update fields
            if "job_title" in params:
                job_search.job_title = params["job_title"]

            if "status" in params:
                job_search.search_status = params["status"]

            if "contract_types" in params:
                job_search.contract_type = []   # reset
                for c in params["contract_types"]:
                    job_search.add_contract_type(c)

            self.job_search_repository.save(job_search)
            return Result.success(JobSearchResponse.from_entity(job_search))

        except JobSearchNotFoundError:
            return Result.failure(
                Error.not_found("JobSearch", str(params["job_search_id"]))
            )
        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
        except BusinessRuleViolation as e:
            return Result.failure(Error.business_rule_violation(str(e)))
        except Exception:
            logger.exception("UpdateJobSearchUseCase failed")
            return Result.failure(Error.system_error("An unexpected error occurred while updating the job search."))


@dataclass
class CompleteJobSearchUseCase:

    job_search_repository: JobSearchRepository

    def execute(self, request: GetJobSearchRequest) -> Result:
        try:
            params = request.to_execution_params()
            job_search = self.job_search_repository.get(params["job_search_id"])

            job_search.complete_search()

            self.job_search_repository.save(job_search)
            return Result.success(JobSearchResponse.from_entity(job_search))

        except JobSearchNotFoundError:
            return Result.failure(
                Error.not_found("JobSearch", str(params["job_search_id"]))
            )
        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
        except BusinessRuleViolation as e:
            return Result.failure(Error.business_rule_violation(str(e)))
        except Exception:
            logger.exception("CompleteJobSearchUseCase failed")
            return Result.failure(Error.system_error("An unexpected error occurred while completing the job search."))


@dataclass
class DeleteJobSearchUseCase:
    """Use case for deleting a job search"""
    job_search_repository: JobSearchRepository

    def execute(self, request: GetJobSearchRequest) -> Result:
        """
        Execute the use case.

        Args:
            request: a GetJobSearchRequest object that contains
                     the unique identifier of the job search to delete

        Returns:
            Result containing DeletionOutcome if successful
        """
        try:
            params = request.to_execution_params()

            # ensure it exists
            self.job_search_repository.get(params["job_search_id"])

            # delete it
            self.job_search_repository.delete(params["job_search_id"])

            return Result.success(DeletionOutcome(params["job_search_id"]))

        except JobSearchNotFoundError:
            return Result.failure(
                Error.not_found("JobSearch", str(params["job_search_id"]))
            )
        except Exception:
            logger.exception("DeleteJobSearchUseCase failed")
            return Result.failure(Error.system_error("An unexpected error occurred while deleting the job search."))