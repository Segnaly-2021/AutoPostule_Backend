import logging
import math
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.domain.value_objects import ApplicationStatus

from auto_apply_app.application.repositories.job_offer_repo import JobOfferRepository
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.dtos.operations import DeletionOutcome
from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.exceptions import (
    JobNotFoundError,
    ValidationError,
    BusinessRuleViolation,
)
from auto_apply_app.application.dtos.job_offer_dtos import (
    CreateJobOfferRequest,
    GetJobOfferRequest,
    JobOfferResponse,
    GetUserApplicationsRequest, 
    ToggleStatusRequest,
    GetAnalyticsRequest,
    GetDailyStatsRequest
)

logger = logging.getLogger(__name__)


@dataclass
class GetUserApplicationsUseCase:
    uow: UnitOfWork  # Injected UoW instead of direct Repo

    async def execute(self, request: GetUserApplicationsRequest) -> Result:
        try:
            async with self.uow:
                params = request.to_execution_params()
                
                # The UoW provides access to job_repo
                # Note: Defaulting to SUBMITTED status as per requirements
                jobs, total_count, aggregations = await self.uow.job_repo.get_user_applications(
                    user_id=params["user_id"],
                    filters=params["filters"],
                    pagination=params["pagination"]
                )
            
                # Mapping Entities -> Response DTOs
                job_responses = [JobOfferResponse.from_entity(job) for job in jobs]
                
                return Result.success({
                    "applications": job_responses,
                    "total": total_count,  # Filtered count
                    "total_unfiltered": aggregations["total_unfiltered"],  # ✅ All user apps
                    "top_titles": aggregations["top_titles"],  # ✅ Top 3 from filtered
                    "page": request.page,
                    "limit": request.limit,
                    "total_pages": math.ceil(total_count / request.limit) if total_count > 0 else 0
                })
        except Exception:
            logger.exception(f"GetUserApplicationsUseCase failed for user {request.user_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving user applications."))


@dataclass
class ToggleResponseStatusUseCase:
    uow: UnitOfWork

    async def execute(self, request: ToggleStatusRequest) -> Result:
        try:
            async with self.uow:
                # We use the specific repo method you defined
                job = await self.uow.job_repo.update_response_status(
                    job_id=request.job_offer_id,
                    has_response=request.status
                )
                
                # Commit the transaction (if the repo method doesn't auto-commit)
                # In strict UoW patterns, changes are only persisted on exit/commit
                
                return Result.success({
                    "id": job.id,
                    "has_response": request.status
                })
                
        except JobNotFoundError:
            return Result.failure(Error.not_found("JobOffer", request.job_offer_id))
        except Exception:
            logger.exception(f"ToggleResponseStatusUseCase failed for job {request.job_offer_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while updating the response status."))


@dataclass
class ToggleInterviewStatusUseCase:
    uow: UnitOfWork

    async def execute(self, request: ToggleStatusRequest) -> Result:
        try:
            async with self.uow:
                job = await self.uow.job_repo.update_interview_status(
                    job_id=request.job_offer_id,
                    has_interview=request.status
                )
                await self.uow.commit()
                
                return Result.success({
                    "id": job.id,
                    "has_interview": request.status
                })
                
        except JobNotFoundError:
            return Result.failure(Error.not_found("JobOffer", request.job_offer_id))
        except Exception:
            logger.exception(f"ToggleInterviewStatusUseCase failed for job {request.job_offer_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while updating the interview status."))


@dataclass
class GetApplicationAnalyticsUseCase:
    uow: UnitOfWork

    async def execute(self, request: GetAnalyticsRequest) -> Result:
        try:
            async with self.uow:
                data = await self.uow.job_repo.get_analytics(
                    user_id=request.user_id,
                    period=request.period
                )
                return Result.success(data)
        except Exception:
            logger.exception(f"GetApplicationAnalyticsUseCase failed for user {request.user_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving application analytics."))


@dataclass
class CleanupUnsubmittedJobsUseCase:
    uow: UnitOfWork

    async def execute(self, search_id: UUID) -> Result[dict]:
        try:
            # 1. The UoW context opens the transaction
            async with self.uow:
                deleted_count = await self.uow.job_repo.delete_by_search_and_status(
                    search_id=search_id,
                    status=ApplicationStatus.APPROVED
                )
            # 2. Context exits gracefully -> UoW auto-commits here!

            # 3. Return the result outside the context
            return Result.success({
                "message": f"Cleaned up {deleted_count} failed/unsubmitted jobs.",
                "deleted_count": deleted_count
            })
                
        except Exception:
            # If an error happened inside the context, UoW auto-rollbacks, 
            # and we catch the error here.
            logger.exception(f"CleanupUnsubmittedJobsUseCase failed for search {search_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while cleaning up unsubmitted jobs."))


@dataclass
class CreateJobOfferUseCase:

    job_offer_repository: JobOfferRepository

    def execute(self, request: CreateJobOfferRequest) -> Result:
        try:
            params = request.to_execution_params()

            job_offer = JobOffer(
                company_name=params["company_name"],
                job_title=params["job_title"],
                location=params["location"],
                job_board=params["job_board"],
                url=params["url"],
                form_url=params["form_url"],
            )

            #self.job_offer_repository.save(job_offer)
            return Result.success(JobOfferResponse.from_entity(job_offer))

        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
        except BusinessRuleViolation as e:
            return Result.failure(Error.business_rule_violation(str(e)))
        except Exception:
            # Added catch-all block just to be safe
            logger.exception("CreateJobOfferUseCase failed")
            return Result.failure(Error.system_error("An unexpected error occurred while creating the job offer."))


@dataclass
class GetJobOfferUseCase:

    job_offer_repository: JobOfferRepository

    def execute(self, request: GetJobOfferRequest) -> Result:
        try:
            params = request.to_execution_params()
            job_offer = self.job_offer_repository.get(params["job_offer_id"])
            return Result.success(JobOfferResponse.from_entity(job_offer))

        except JobNotFoundError:
            return Result.failure(
                Error.not_found("JobOffer", str(params["job_offer_id"]))
            )
        except Exception:
            # Added catch-all block just to be safe
            logger.exception(f"GetJobOfferUseCase failed for job {request.job_offer_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving the job offer."))


@dataclass
class DeleteJobOfferUseCase:

    job_offer_repository: JobOfferRepository

    def execute(self, request: GetJobOfferRequest) -> Result:
        try:
            params = request.to_execution_params()

            self.job_offer_repository.delete(params["job_offer_id"])

            return Result.success(DeletionOutcome(params["job_offer_id"]))

        except JobNotFoundError:
            return Result.failure(
                Error.not_found("JobOffer", str(params["job_offer_id"]))
            )
        except Exception:
            # Added catch-all block just to be safe
            logger.exception(f"DeleteJobOfferUseCase failed for job {request.job_offer_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while deleting the job offer."))


@dataclass
class GetDailyStatsUseCase:
    uow: UnitOfWork

    async def execute(self, request: GetDailyStatsRequest) -> Result:
        try:
            async with self.uow:
                params = request.to_execution_params()
                
                # Use the UoW to access the repository and fetch the count
                count = await self.uow.job_repo.get_daily_application_count(
                    user_id=params["user_id"]
                )
                
                return Result.success({
                    "count": count
                })
                
        except Exception:
            logger.exception(f"GetDailyStatsUseCase failed for user {request.user_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving daily statistics."))