from dataclasses import dataclass
import math

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
    ApplyToJobOfferRequest,
    JobOfferResponse,
    GetUserApplicationsRequest, 
    ToggleStatusRequest,
    GetAnalyticsRequest
)




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
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))


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
                await self.uow.commit() 
                
                return Result.success({
                    "id": job.id,
                    "has_response": request.status

                })
                
        except JobNotFoundError:
            return Result.failure(Error.not_found("JobOffer", request.job_offer_id))
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))


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
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))


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
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))





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

@dataclass
class ApplyToJobOfferUseCase:

    job_offer_repository: JobOfferRepository
    
    def execute(self, request: ApplyToJobOfferRequest) -> Result:
        """
        Apply to a job offer.
        """
        try:
            params = request.to_execution_params()

            job_offer = self.job_offer_repository.get(params["job_offer_id"])

            # Start application (FOUND → IN_PROGRESS)
            job_offer.start_application()

            self.submit_app_port.fill_form(job_offer)
            self.submit_app_port.submit(job_offer)

            # Set posting id (companyName_offerId_userId)
            if params.get("user_id") is not None:
                job_offer.set_job_posting_id(params["user_id"])

            # Complete application
            job_offer.complete_application()

            self.job_offer_repository.save(job_offer)
            return Result.success(JobOfferResponse.from_entity(job_offer))

        except JobNotFoundError:
            return Result.failure(
                Error.not_found("JobOffer", str(params["job_offer_id"]))
            )
        except ValidationError as e:
            return Result.failure(Error.validation_error(str(e)))
        except BusinessRuleViolation as e:
            return Result.failure(Error.business_rule_violation(str(e)))
        except ValueError as e:
            # for domain-level status change errors
            return Result.failure(Error.business_rule_violation(str(e)))


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
