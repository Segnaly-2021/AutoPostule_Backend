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
                agent_state = await uow.agent_state_repo.get_by_user_id(user.id)
                if agent_state is None:
                    agent_state = AgentState(user_id=user.id)
                agent_state.bind_to_search(search_mission.id)
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

                # 3. NEW: Signal the kill-switch (scoped to this search_id)
                agent_state = await uow.agent_state_repo.get_by_user_id(user_id)
                if agent_state is not None:
                    # request_shutdown returns False if search_id doesn't match
                    # (e.g., a newer search has already taken over). That's fine —
                    # the cancel() above is what matters for the user-facing record.
                    agent_state.request_shutdown(search_id)
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
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))
        


        
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
                print(f"hashes: {ignored_hashes}")
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
                        
            print(f"Processed Agent Results: {count_new} new jobs added to search {search_id}")
            return Result.success(search_mission.all_matched_jobs)
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))

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
            
            # 🚨 FIX 1: Open the transaction ONCE for the entire batch
            async with self.uow as uow:
                for offer in offers:                
                    
                    # 🚨 FIX 2: Only set the dates if they don't exist yet!
                    # This prevents overwriting the original date during future status updates.
                    if not offer.application_date and offer.status in [ApplicationStatus.SUBMITTED]:
                        offer.application_date = datetime.now(timezone.utc)
                        offer.followup_date = offer.application_date + timedelta(days=7) 
                        
                    await uow.job_repo.save(offer)                
                    count += 1
                    
            # uow context manager automatically commits the batch here!
                
            print(f"Saving Use Case: Saved {count} job applications.")
            return Result.success(f"Successfully saved {count} applications")

        except Exception as e:
            return Result.failure(Error.system_error(f"DB Save Failed: {str(e)}"))
        



    
@dataclass
class GetJobsForReviewUseCase:
    """
    Fetch all jobs in GENERATED status for Premium user review.
    """
    uow: UnitOfWork

    async def execute(self, request: GetJobsForReviewRequest) -> Result:
        try:
            # 🚨 Extract from DTO (adjust depending on if you use .to_execution_params() or direct properties)
            user_id = str(request.user_id)
            search_id = str(request.search_id)

            async with self.uow as uow:
                # 1. Verify search exists and belongs to user
                search = await uow.search_repo.get(UUID(search_id.strip()))
                
                if not search:
                    return Result.failure(
                        Error.not_found("JobSearch", search_id)
                    )
                
                if str(search.user_id) != user_id:
                    return Result.failure(
                        Error.unauthorized(
                            f"Search {search_id} does not belong to user {user_id}"
                        )
                    )
                
                # 2. Fetch all jobs with status = GENERATED
                jobs = await uow.job_repo.get_by_search_and_status(
                    search_id=search_id,
                    status=ApplicationStatus.GENERATED
                )
                
                return Result.success(jobs)
                
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))


      
@dataclass
class UpdateCoverLetterUseCase:
    """
    Update the cover letter for a job application.
    """
    uow: UnitOfWork

    async def execute(self, request: UpdateCoverLetterRequest) -> Result:
        try:
            # 🚨 Extract from DTO
            user_id = str(request.user_id)
            job_id = str(request.job_id)
            cover_letter = request.cover_letter  # or request.text based on your DTO

            # 1. Validate cover letter
            if not cover_letter or not cover_letter.strip():
                return Result.failure(
                    Error.validation_error("Cover letter cannot be empty")
                )
            
            if len(cover_letter) > 5000:
                return Result.failure(
                    Error.validation_error("Cover letter too long (max 5000 characters)")
                )
            
            async with self.uow as uow:
                # 2. Fetch job
                job = await uow.job_repo.get(UUID(job_id.strip()))
                
                if not job:
                    return Result.failure(
                        Error.not_found("JobOffer", job_id)
                    )
                
                # 3. Verify ownership (job → search → user)
                search = await uow.search_repo.get(job.search_id)
                
                if not search or str(search.user_id) != user_id:
                    return Result.failure(
                        Error.unauthorized(
                            f"Job {job_id} does not belong to user {user_id}"
                        )
                    )
                
                # 4. Validate status (can only edit GENERATED jobs)
                if job.status != ApplicationStatus.GENERATED:
                    return Result.failure(
                        Error.conflict(
                            f"Cannot edit job in {job.status.value} status. "
                            "Only GENERATED jobs can be edited."
                        )
                    )
                
                # 5. Update cover letter
                job.cover_letter = cover_letter.strip()
                
                # 6. Save
                await uow.job_repo.save(job)
                await uow.commit()
                
                return Result.success({
                    "job_id": str(job.id),
                    "message": "Cover letter updated successfully"
                })
                
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))




@dataclass
class ApproveJobUseCase:
    """
    Mark a job application as APPROVED for submission.
    """
    uow: UnitOfWork

    async def execute(self, request: ApproveJobRequest) -> Result:
        try:
            # 🚨 Extract from DTO
            user_id = str(request.user_id)
            job_id = str(request.job_id)

            async with self.uow as uow:
                # 1. Fetch job
                job = await uow.job_repo.get(UUID(job_id.strip()))
                
                if not job:
                    return Result.failure(
                        Error.not_found("JobOffer", job_id)
                    )
                
                # 2. Verify ownership
                search = await uow.search_repo.get(job.search_id)
                
                if not search or str(search.user_id) != user_id:
                    return Result.failure(
                        Error.unauthorized(
                            f"Job {job_id} does not belong to user {user_id}"
                        )
                    )
                
                # 3. Validate status (can only approve GENERATED jobs)
                if job.status != ApplicationStatus.GENERATED:
                    return Result.failure(
                        Error.conflict(
                            f"Cannot approve job in {job.status.value} status. "
                            "Already processed."
                        )
                    )
                
                # 4. Update status to APPROVED
                job.status = ApplicationStatus.APPROVED
                
                # 5. Save
                await uow.job_repo.save(job)
                await uow.commit()
                
                return Result.success({
                    "job_id": str(job.id),
                    "status": "APPROVED",
                    "message": "Job approved successfully"
                })
                
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))





@dataclass
class DiscardJobUseCase:
    """
    Mark a job application as REJECTED.
    """
    uow: UnitOfWork

    async def execute(self, request: DiscardJobRequest) -> Result:
        try:
            # 🚨 Extract from DTO
            user_id = str(request.user_id)
            job_id = str(request.job_id)

            async with self.uow as uow:
                # 1. Fetch job
                job = await uow.job_repo.get(UUID(job_id.strip()))
                
                if not job:
                    return Result.failure(
                        Error.not_found("JobOffer", job_id)
                    )
                
                # 2. Verify ownership
                search = await uow.search_repo.get(job.search_id)
                
                if not search or str(search.user_id) != user_id:
                    return Result.failure(
                        Error.unauthorized(
                            f"Job {job_id} does not belong to user {user_id}"
                        )
                    )
                
                # 3. Validate status (cannot discard submitted jobs)
                if job.status in [ApplicationStatus.SUBMITTED]:
                    return Result.failure(
                        Error.conflict(
                            f"Cannot discard job in {job.status.value} status. "
                            "Job already submitted."
                        )
                    )
                
                # 4. Update status to REJECTED
                job.status = ApplicationStatus.REJECTED
                
                # 5. Save
                await uow.job_repo.save(job)
                await uow.commit()
                
                return Result.success({
                    "job_id": str(job.id),
                    "status": "REJECTED",
                    "message": "Job discarded successfully"
                })
                
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))
        




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
                # 1. Fetch Subscription
                subscription = await uow.subscription_repo.get_by_user_id(str(user_id))
                
                if not subscription:
                    return Result.failure(Error.not_found("Subscription", str(user_id)))
                
                # 2. Consume Credits (Domain logic handles validation)
                try:
                    subscription.consume_credits(amount)
                except ValueError as e:
                    # Catch the domain error (e.g., "Insufficient AI credits")
                    return Result.failure(Error.validation_error(str(e)))
                
                # 3. Save the updated balance
                await uow.subscription_repo.save(subscription)
                # Note: call await uow.commit() here if your UoW requires explicit commits!
                await uow.commit()
                
                return Result.success(subscription.ai_credits_balance)

        except Exception as e:
            return Result.failure(Error.system_error(str(e)))
        

