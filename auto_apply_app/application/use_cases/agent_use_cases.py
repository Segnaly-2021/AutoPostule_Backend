# auto_apply_app/application/use_cases/agent_use_cases.py
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from uuid import UUID
from typing import Optional, Callable

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.service_ports.agent_port import AgentServicePort
from auto_apply_app.application.dtos.agent_dtos import (
    StartAgentRequest, 
    ResumeAgentRequest, 
    KillAgentRequest,
    AgentResponse,
    GetJobsForReviewRequest,
    UpdateCoverLetterRequest,
    ApproveJobRequest,
    DiscardJobRequest
)
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.entities.agent_state import AgentState
from auto_apply_app.domain.value_objects import ApplicationStatus

logger = logging.getLogger(__name__)


@dataclass
class StartJobSearchAgentUseCase:
    uow: UnitOfWork
    agent_service: AgentServicePort

    async def execute(
        self,
        request: StartAgentRequest,
        progress_callback: Optional[Callable] = None,
    ) -> Result:
        try:
            params = request.to_execution_params()
            user_uuid = UUID(params["user_id"])

            # Variables that need to escape the UoW context to be passed to the agent
            user = None
            subscription = None
            preferences = None
            board_credentials = {}
            board_names = []
            search_mission = None

            async with self.uow as uow:
                # 1. Fetch User
                user = await uow.user_repo.get(user_uuid)
                if not user:
                    return Result.failure(
                        Error.not_found("User", str(user_uuid))
                    )

                # 2. Fetch Subscription (Authorization — tier + active status)
                subscription = await uow.subscription_repo.get_by_user_id(str(user.id))
                if not subscription or not subscription.can_run_agent():
                    return Result.failure(
                        Error.unauthorized("Subscription invalid or expired")
                    )

                # 3. NEW: Daily quota + cooldown check (atomic with usage row)
                usage = await uow.agent_usage_repo.get_or_create_for_today(user.id)
                allowed, reason = usage.can_start_run(
                    daily_limit=subscription.agent_daily_limit,
                    base_cooldown_minutes=subscription.agent_cooldown_base_minutes,
                )
                if not allowed:
                    return Result.failure(Error.too_many_requests(reason))

                # 4. Fetch User Preferences
                preferences = await uow.user_pref_repo.get_by_user_id(user.id)
                if not preferences:
                    preferences = UserPreferences(user_id=user.id)

                # 5. Validate prerequisites for active job boards
                job_boards = preferences.active_boards
                board_names = [b for b, v in job_boards.items() if v]

                if preferences.is_full_automation:
                    for board_name in board_names:
                        credential = await uow.board_cred_repo.get_by_user_and_board(
                            user.id, board_name
                        )
                        if not credential or not credential.login_encrypted:
                            return Result.failure(Error.validation_error(
                                f"Full automation requires credentials for {board_name}. "
                                "Please configure credentials in Settings before running "
                                "a multi-board search."
                            ))
                        board_credentials[board_name] = credential

                # 6. Handle resume path
                resume_path = params.get("resume_path") or user.resume_path
                if not resume_path:
                    return Result.failure(
                        Error.validation_error("Resume is required")
                    )
                if params.get("resume_path") and params["resume_path"] != user.resume_path:
                    user.resume_path = params["resume_path"]
                    await uow.user_repo.save(user)

                # 7. Create & save the search mission
                search_mission = JobSearch(
                    user_id=user.id,
                    job_title=params["job_title"],
                    job_boards=params["job_boards"],
                    location=params.get("location", ""),
                    min_salary=params.get("min_salary", 0),
                    contract_types=params.get("contract_types", []),
                )
                search_mission.start_searching()
                await uow.search_repo.save(search_mission)

                # 8. NEW: Bind kill-switch to this specific search
                #    (atomic with everything else in this UoW)
                agent_state = await uow.agent_state_repo.get_by_search_id(search_mission.id)
                if agent_state is None:
                    agent_state = AgentState(
                    user_id=user.id,
                    search_id=search_mission.id,
                    )
                await uow.agent_state_repo.save(agent_state)

            # 9. Run the agent (outside UoW — long-running, blocking)
            await self.agent_service.run_job_search(
                user=user,
                search=search_mission,
                subscription=subscription,
                preferences=preferences,
                credentials=board_credentials if preferences.is_full_automation else None,
                progress_callback=progress_callback,
            )

            return Result.success(AgentResponse.from_job_search(
                search=search_mission,
                status="started",
                message=(
                    f"Parallel job search agent started for "
                    f"'{params['job_title']}' on {len(board_names)} platforms."
                ),
            ))

        except Exception:
            logger.exception("StartJobSearchAgentUseCase failed")
            return Result.failure(
                Error.system_error("Could not start the job search agent.")
            )


@dataclass
class ResumeJobApplicationUseCase:
    """
    Called when a Premium User clicks "Apply" or "Apply All" after reviewing drafts.
    """
    uow: UnitOfWork
    agent_service: AgentServicePort

    async def execute(
        self,
        request: ResumeAgentRequest,
        progress_callback: Optional[Callable] = None,
    ) -> Result:
        try:
            params = request.to_execution_params()
            user_id = params["user_id"]
            search_id = params["search_id"]
            apply_all = params["apply_all"]

            # Variables to escape UoW
            user = None
            subscription = None
            preferences = None
            search_mission = None
            board_credentials = {}
            approved_jobs = []

            async with self.uow as uow:
                user = await uow.user_repo.get(user_id)
                if not user:
                    return Result.failure(Error.not_found("User", str(user_id)))

                subscription = await uow.subscription_repo.get_by_user_id(str(user.id))
                if not subscription:
                    return Result.failure(Error.not_found("Subscription", str(user_id)))

                search_mission = await uow.search_repo.get(search_id)
                if not search_mission:
                    return Result.failure(Error.not_found("JobSearch", str(search_id)))

                # Authorization: verify ownership
                if search_mission.user_id != user.id:
                    return Result.failure(
                        Error.unauthorized("You do not own this job search")
                    )

                preferences = await uow.user_pref_repo.get_by_user_id(user.id)
                if not preferences:
                    preferences = UserPreferences(user_id=user.id)

                # Fetch credentials
                job_boards = preferences.active_boards
                board_names = [b for b, v in job_boards.items() if v]

                if preferences.is_full_automation:
                    for board_name in board_names:
                        credential = await uow.board_cred_repo.get_by_user_and_board(
                            user.id, board_name
                        )
                        if not credential or not credential.login_encrypted:
                            return Result.failure(Error.validation_error(
                                f"Full automation requires credentials for {board_name}. "
                                "Please configure credentials in Settings."
                            ))
                        board_credentials[board_name] = credential

                # Load & approve drafts
                if apply_all:
                    drafts = await uow.job_repo.get_by_search_and_status(
                        str(search_id).strip(),
                        status=ApplicationStatus.GENERATED,
                    )
                    if not drafts:
                        return Result.failure(
                            Error.validation_error("No drafts found to apply")
                        )
                    for job in drafts:
                        job.status = ApplicationStatus.APPROVED
                    await uow.job_repo.save_all(drafts)
                    approved_jobs = drafts
                else:
                    approved_jobs = await uow.job_repo.get_by_search_and_status(
                        str(search_id).strip(),
                        status=ApplicationStatus.APPROVED,
                    )
                    if not approved_jobs:
                        return Result.failure(
                            Error.validation_error("No approved jobs found")
                        )

            # Resume the agent (outside UoW)
            await self.agent_service.resume_job_search(
                user=user,
                search=search_mission,
                subscription=subscription,
                preferences=preferences,
                approved_jobs=approved_jobs,
                credentials=board_credentials,
                progress_callback=progress_callback,
            )

            return Result.success(AgentResponse.from_job_search(
                search=search_mission,
                status="resumed",
                message="Job application workflow resumed",
            ))

        except Exception:
            logger.exception("ResumeJobApplicationUseCase failed")
            return Result.failure(
                Error.system_error("Could not resume the job search agent.")
            )


@dataclass
class KillJobSearchUseCase:
    """
    Emergency stop: immediately terminate a running job search.
    
    Two responsibilities:
    1. Mark JobSearch as CANCELLED (persistent record)
    2. Set the kill-switch on AgentState (live signal to running workers,
       scoped to this specific search_id to prevent stale-shutdown bugs)
    """
    uow: UnitOfWork
    agent_service: AgentServicePort

    async def execute(self, request: KillAgentRequest) -> Result:
        try:
            params = request.to_execution_params()
            user_id = params["user_id"]
            search_id = params["search_id"]

            async with self.uow as uow:
                # 1. Fetch and validate the search
                search = await uow.search_repo.get(search_id)
                if not search:
                    return Result.failure(
                        Error.not_found("JobSearch", str(search_id))
                    )
                if search.user_id != user_id:
                    return Result.failure(
                        Error.unauthorized("You do not own this job search")
                    )

                # 2. Mark search as cancelled
                search.cancel()
                await uow.search_repo.save(search)

              
                # 3. Signal the kill-switch (scoped to this specific search_id)
                agent_state = await uow.agent_state_repo.get_by_search_id(search_id)
                if agent_state is not None:
                    agent_state.shutdown()
                    await uow.agent_state_repo.save(agent_state)

            # 4. Force-cleanup any in-flight workers (outside UoW)
            await self.agent_service.kill_job_search(search_id)

            return Result.success(AgentResponse.from_job_search(
                search=search,
                status="killed",
                message="Job search terminated successfully",
            ))

        except Exception:
            logger.exception("KillJobSearchUseCase failed")
            return Result.failure(
                Error.system_error("Could not terminate the job search.")
            )


@dataclass
class GetIgnoredHashesUseCase:
    """
    Called by the Workers before scraping.
    Returns a set of fingerprint hashes for jobs the user recently applied to.
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, days: int = 14) -> Result:
        try:
            async with self.uow as uow:
                hashes = await uow.job_repo.get_recent_application_hashes(user_id, days=days)
                return Result.success(hashes)
        except Exception:
            logger.exception(f"GetIgnoredHashesUseCase failed for user {user_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving application history."))


@dataclass
class ProcessAgentResultsUseCase:
    """
    Called by the Agent (or a listener) when scraping is done.
    It takes raw found jobs, deduplicates them, and saves them.
    """
    uow: UnitOfWork    

    async def execute(self, user_id: UUID, search_id: UUID, raw_offers: list[JobOffer]) -> Result:
        try:
            async with self.uow as uow:
                search_mission = await uow.search_repo.get(search_id)
                
                ignored_hashes = await uow.job_repo.get_recent_application_hashes(user_id, days=14)
                count_new = 0
                
                for offer in raw_offers:
                    offer.set_job_posting_id(user_id)
                    
                    if offer.get_job_posting_id() in ignored_hashes:
                        continue
                    
                    try:
                        search_mission.add_job(offer)
                        count_new += 1
                    except ValueError:
                        continue
                        
            return Result.success(search_mission.all_matched_jobs)
        except Exception:
            logger.exception(f"ProcessAgentResultsUseCase failed for search {search_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while processing job matches."))


@dataclass
class SaveJobApplicationsUseCase:
    """
    Called by the Agent AFTER analysis and submission attempts.
    It persists the fully processed JobOffer entities to the database.
    """
    uow: UnitOfWork
    
    async def execute(self, offers: list[JobOffer]) -> Result:
        try:
            if not offers:
                return Result.success("No jobs to save")

            count = 0
            
            async with self.uow as uow:
                for offer in offers:                
                    if not offer.application_date and offer.status in [ApplicationStatus.SUBMITTED]:
                        offer.application_date = datetime.now(timezone.utc)
                        offer.followup_date = offer.application_date + timedelta(days=7) 
                        
                    await uow.job_repo.save(offer)                
                    count += 1
                    
            return Result.success(f"Successfully saved {count} applications")

        except Exception:
            logger.exception("SaveJobApplicationsUseCase failed to save batch.")
            return Result.failure(Error.system_error("An unexpected error occurred while saving job applications."))
        

@dataclass
class GetJobsForReviewUseCase:
    """
    Fetch all jobs in GENERATED status for Premium user review.
    """
    uow: UnitOfWork

    async def execute(self, request: GetJobsForReviewRequest) -> Result:
        try:
            user_id = str(request.user_id)
            search_id = str(request.search_id)

            async with self.uow as uow:
                search = await uow.search_repo.get(UUID(search_id.strip()))
                
                if not search:
                    return Result.failure(Error.not_found("JobSearch", search_id))
                
                if str(search.user_id) != user_id:
                    return Result.failure(
                        Error.unauthorized(f"Search {search_id} does not belong to user {user_id}")
                    )
                
                jobs = await uow.job_repo.get_by_search_and_status(
                    search_id=search_id,
                    status=ApplicationStatus.GENERATED
                )
                
                return Result.success(jobs)
                
        except Exception:
            logger.exception(f"GetJobsForReviewUseCase failed for search {request.search_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving jobs for review."))


@dataclass
class UpdateCoverLetterUseCase:
    """
    Update the cover letter for a job application.
    """
    uow: UnitOfWork

    async def execute(self, request: UpdateCoverLetterRequest) -> Result:
        try:
            user_id = str(request.user_id)
            job_id = str(request.job_id)
            cover_letter = request.cover_letter

            if not cover_letter or not cover_letter.strip():
                return Result.failure(Error.validation_error("Cover letter cannot be empty"))
            
            if len(cover_letter) > 5000:
                return Result.failure(Error.validation_error("Cover letter too long (max 5000 characters)"))
            
            async with self.uow as uow:
                job = await uow.job_repo.get(UUID(job_id.strip()))
                
                if not job:
                    return Result.failure(Error.not_found("JobOffer", job_id))
                
                search = await uow.search_repo.get(job.search_id)
                
                if not search or str(search.user_id) != user_id:
                    return Result.failure(
                        Error.unauthorized(f"Job {job_id} does not belong to user {user_id}")
                    )
                
                if job.status != ApplicationStatus.GENERATED:
                    return Result.failure(
                        Error.conflict("Cannot edit job in its current status. Only GENERATED jobs can be edited.")
                    )
                
                job.cover_letter = cover_letter.strip()
                
                await uow.job_repo.save(job)
                await uow.commit()
                
                return Result.success({
                    "job_id": str(job.id),
                    "message": "Cover letter updated successfully"
                })
                
        except Exception:
            logger.exception(f"UpdateCoverLetterUseCase failed for job {request.job_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while updating the cover letter."))


@dataclass
class ApproveJobUseCase:
    """
    Mark a job application as APPROVED for submission.
    """
    uow: UnitOfWork

    async def execute(self, request: ApproveJobRequest) -> Result:
        try:
            user_id = str(request.user_id)
            job_id = str(request.job_id)

            async with self.uow as uow:
                job = await uow.job_repo.get(UUID(job_id.strip()))
                
                if not job:
                    return Result.failure(Error.not_found("JobOffer", job_id))
                
                search = await uow.search_repo.get(job.search_id)
                
                if not search or str(search.user_id) != user_id:
                    return Result.failure(
                        Error.unauthorized(f"Job {job_id} does not belong to user {user_id}")
                    )
                
                if job.status != ApplicationStatus.GENERATED:
                    return Result.failure(
                        Error.conflict("Cannot approve job. It has already been processed.")
                    )
                
                job.status = ApplicationStatus.APPROVED
                
                await uow.job_repo.save(job)
                await uow.commit()
                
                return Result.success({
                    "job_id": str(job.id),
                    "status": "APPROVED",
                    "message": "Job approved successfully"
                })
                
        except Exception:
            logger.exception(f"ApproveJobUseCase failed for job {request.job_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while approving the job."))


@dataclass
class DiscardJobUseCase:
    """
    Mark a job application as REJECTED.
    """
    uow: UnitOfWork

    async def execute(self, request: DiscardJobRequest) -> Result:
        try:
            user_id = str(request.user_id)
            job_id = str(request.job_id)

            async with self.uow as uow:
                job = await uow.job_repo.get(UUID(job_id.strip()))
                
                if not job:
                    return Result.failure(Error.not_found("JobOffer", job_id))
                
                search = await uow.search_repo.get(job.search_id)
                
                if not search or str(search.user_id) != user_id:
                    return Result.failure(
                        Error.unauthorized(f"Job {job_id} does not belong to user {user_id}")
                    )
                
                if job.status in [ApplicationStatus.SUBMITTED]:
                    return Result.failure(
                        Error.conflict("Cannot discard job. It has already been submitted.")
                    )
                
                job.status = ApplicationStatus.REJECTED
                
                await uow.job_repo.save(job)
                await uow.commit()
                
                return Result.success({
                    "job_id": str(job.id),
                    "status": "REJECTED",
                    "message": "Job discarded successfully"
                })
                
        except Exception:
            logger.exception(f"DiscardJobUseCase failed for job {request.job_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while discarding the job."))


@dataclass
class ConsumeAiCreditsUseCase:
    """
    Called by the Agent immediately after generating cover letters.
    Deducts AI credits from the user's subscription wallet.
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, amount: int) -> Result:
        try:
            async with self.uow as uow:
                subscription = await uow.subscription_repo.get_by_user_id(str(user_id))
                
                if not subscription:
                    return Result.failure(Error.not_found("Subscription", str(user_id)))
                
                try:
                    subscription.consume_credits(amount)
                except ValueError as e:
                    # Domain errors like "Insufficient AI credits" are safe to show to the user
                    return Result.failure(Error.validation_error(str(e)))
                
                await uow.subscription_repo.save(subscription)
                await uow.commit()
                
                return Result.success(subscription.ai_credits_balance)

        except Exception:
            logger.exception(f"ConsumeAiCreditsUseCase failed for user {user_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while updating AI credits."))