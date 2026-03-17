# auto_apply_app/application/use_cases/agent_use_cases.py
from dataclasses import dataclass
from uuid import UUID
from typing import Optional, Callable

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.service_ports.agent_port import AgentServicePort
from auto_apply_app.application.dtos.agent_dtos import (
  StartAgentRequest, 
  ResumeAgentRequest, 
  KillAgentRequest,
  AgentResponse
)
from auto_apply_app.domain.entities.job_search import JobSearch
from auto_apply_app.domain.entities.job_offer import JobOffer
from auto_apply_app.domain.entities.user_preferences import UserPreferences
from auto_apply_app.domain.value_objects import ApplicationStatus

@dataclass
class StartJobSearchAgentUseCase:
    uow: UnitOfWork
    agent_service: AgentServicePort


    async def execute(
        self, 
        request: StartAgentRequest,
        progress_callback: Optional[Callable] = None
    ) -> Result:
        try:
            params = request.to_execution_params()
            async with self.uow as uow:
                # 1. Fetch User
                user = await uow.user_repo.get(UUID(params["user_id"]))
                print(f"Agent Use Case: Fetched user {user.id} for job search agent")


                if not user:
                    return Result.failure(Error.not_found("User", str(params["user_id"])))

                # 2. Fetch Subscription (Authorization)
                subscription = await uow.subscription_repo.get_by_user_id(str(user.id))
                print(f"Agent Use Case: Fetched subscription for user {user.id}")

                if not subscription or not subscription.can_run_agent():
                    return Result.failure(Error.unauthorized("Subscription invalid or expired"))
                
                # 3. Fetch User Preferences
                preferences = await uow.user_pref_repo.get_by_user_id(user.id)
                print(f"Agent Use Case: Fetched preferences for user {user.id}: {preferences}")
                if not preferences:
                    # Use defaults if preferences don't exist
                    preferences = UserPreferences(user_id=user.id)
                    print(f"Agent Use Case: No preferences found for user {user.id}, using defaults: {preferences}")


                # 🚨 V2 UPDATE: 4. Validate Prerequisites for ALL requested Job Boards
                job_boards = preferences.active_boards # Now expects a list!
                board_credentials = {} # Dictionary to pass down to the Master
                board_names = [b for b, v in job_boards.items() if v] # Extract active board names
                print(f"Agent Use Case: User {user.id} has active boards: {board_names}")

                if preferences.is_full_automation:
                    for board_name in board_names:
                        credential = await uow.board_cred_repo.get_by_user_and_board(user.id, board_name)
                        
                        if not credential or not credential.login_encrypted:
                            return Result.failure(Error.validation_error(
                                f"Full automation requires credentials for {board_name}. "
                                "Please configure credentials in Settings before running a multi-board search."
                            ))
                        
                        # Store validated credentials by board name
                        board_credentials[board_name] = credential

                # 5. Handle resume path
                resume_path = params.get("resume_path") or user.resume_path
                if not resume_path:
                    return Result.failure(Error.validation_error("Resume is required"))
                
                # Update user entity if resume path was provided in request
                if params.get("resume_path") and params["resume_path"] != user.resume_path:
                    user.resume_path = params["resume_path"]
                    await uow.user_repo.save(user)
            

                # 6. Create & Save Mission
                search_mission = JobSearch(
                    user_id=user.id,
                    job_title=params["job_title"],
                    job_boards=params["job_boards"],
                    location=params.get("location", ""),
                    min_salary=params.get("min_salary", 0),
                    contract_types=params.get("contract_types", [])
                )
                print(f"Agent Use Case: Created JobSearch entity: {search_mission}")
                search_mission.start_searching()
                await uow.search_repo.save(search_mission)

            # 🚨 V2 UPDATE: 7. Run Agent with multiple credentials
            await self.agent_service.run_job_search(
                user=user,
                search=search_mission,
                subscription=subscription,
                preferences=preferences, 
                credentials=board_credentials if preferences.is_full_automation else None, # Pass the dict!
                progress_callback=progress_callback
            )
            print(f"Agent Use Case: Started Master Agent for {len(board_names)} boards.")

            return Result.success(AgentResponse.from_job_search(
                search=search_mission,
                status="started",
                message=f"Parallel job search agent started for '{params['job_title']}' on {len(board_names)} platforms."
            ))

        except Exception as e:
            return Result.failure(Error.system_error(str(e)))


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
        progress_callback: Optional[Callable] = None
    ) -> Result:
        try:
            async with self.uow as uow:
                params = request.to_execution_params()
                user_id = params["user_id"]
                search_id = params["search_id"]
                apply_all = params["apply_all"]

                # 1. Validation
                user = await uow.user_repo.get(user_id)
                if not user:
                    return Result.failure(Error.not_found("User", str(user_id)))
                
                subscription = await uow.subscription_repo.get_by_user_id(user_id)
                if not subscription:
                    return Result.failure(Error.not_found("Subscription", str(user_id)))
                
                search_mission = await uow.search_repo.get(str(search_id))
                if not search_mission:
                    return Result.failure(Error.not_found("JobSearch", str(search_id)))
                
                # 2. Authorization: Verify ownership
                if search_mission.user_id != user_id:
                    return Result.failure(Error.unauthorized("You do not own this job search"))
                
                # ✅ 3. Fetch User Preferences
                preferences = await uow.user_pref_repo.get_by_user_id(user_id)
                if not preferences:
                    preferences = UserPreferences(user_id=user_id)
                
                # 4. Load & Approve Drafts
                if apply_all:
                    drafts = await uow.job_repo.get_by_search(
                        search_id, 
                        status=ApplicationStatus.GENERATED
                    )
                    
                    if not drafts:
                        return Result.failure(Error.validation_error("No drafts found to apply"))
                    
                    for job in drafts:
                        job.status = ApplicationStatus.APPROVED
                    
                    await uow.job_repo.save_all(drafts)
                else:
                    approved_jobs = await uow.job_repo.get_by_search(
                        search_id, 
                        status=ApplicationStatus.APPROVED
                    )
                    if not approved_jobs:
                        return Result.failure(Error.validation_error("No approved jobs found"))

            # 5. Resume the Agent
            await self.agent_service.resume_job_search(
                user=user,
                search=search_mission,
                subscription=subscription,
                preferences=preferences,
                progress_callback=progress_callback
            )

            return Result.success(AgentResponse.from_job_search(
                search=search_mission,
                status="resumed",
                message="Job application workflow resumed"
            ))
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))


@dataclass
class KillJobSearchUseCase:
    """
    Emergency stop: Immediately terminate a running job search.
    """
    uow: UnitOfWork
    agent_service: AgentServicePort

    async def execute(self, request: KillAgentRequest) -> Result:
        try:
            async with self.uow as uow:
                params = request.to_execution_params()
                user_id = params["user_id"]
                search_id = params["search_id"]

                # 1. Fetch and validate
                search = await uow.search_repo.get(search_id)
                if not search:
                    return Result.failure(Error.not_found("JobSearch", str(search_id)))
                
                # 2. Authorization: Verify ownership
                if search.user_id != user_id:
                    return Result.failure(Error.unauthorized("You do not own this job search"))
                
                # 3. Mark as cancelled in DB
                search.cancel()
                await uow.search_repo.save(search)
                await uow.commit()
            
            # 4. Kill the agent process (force cleanup)
            await self.agent_service.kill_job_search(search_id)
            
            return Result.success(AgentResponse.from_job_search(
                search=search,
                status="killed",
                message="Job search terminated successfully"
            ))
        
        except Exception as e:
            return Result.failure(Error.system_error(str(e)))



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
            for offer in offers:                
                async with self.uow as uow:
                    await uow.job_repo.save(offer)                
                    count += 1
                    
            print(f"Saving Use Case: Saved {count} job applications.")
            return Result.success(f"Successfully saved {count} applications")

        except Exception as e:
            return Result.failure(Error.system_error(f"DB Save Failed: {str(e)}"))
        


@dataclass
class GetJobsForReviewUseCase:
    """
    Fetch all jobs in GENERATED status for Premium user review.
    
    Premium Flow:
    1. Agent pauses after analyzing jobs
    2. User calls this endpoint to see generated applications
    3. User edits cover letters, approves/discards jobs
    4. User calls /resume to submit approved jobs
    """
    uow: UnitOfWork

    async def execute(self, user_id: str, search_id: str) -> Result:
        """
        Args:
            user_id: UUID of authenticated user (from JWT)
            search_id: UUID of job search
        
        Returns:
            Result containing List[JobOffer] or Error
        """
        try:
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
    
    Validation:
    - User must own the job (via search ownership)
    - Job must be in GENERATED status (cannot edit submitted jobs)
    - Cover letter must be valid (not empty, within length limits)
    """
    uow: UnitOfWork

    async def execute(
        self, 
        user_id: str, 
        job_id: str, 
        cover_letter: str
    ) -> Result:
        """
        Args:
            user_id: UUID of authenticated user
            job_id: UUID of job to update
            cover_letter: New cover letter text
        
        Returns:
            Result with success message or Error
        """
        try:
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
    
    Premium users can selectively approve jobs one-by-one.
    Approved jobs will be submitted when user calls /resume endpoint.
    """
    uow: UnitOfWork

    async def execute(self, user_id: str, job_id: str) -> Result:
        """
        Args:
            user_id: UUID of authenticated user
            job_id: UUID of job to approve
        
        Returns:
            Result with success message or Error
        """
        try:
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
        

@dataclass
class DiscardJobUseCase:
    """
    Mark a job application as REJECTED.
    
    Premium users can discard unwanted jobs from the review queue.
    Discarded jobs will NOT be submitted when user calls /resume.
    
    Note: Job is not deleted, just marked as REJECTED for audit trail.
    """
    uow: UnitOfWork

    async def execute(self, user_id: str, job_id: str) -> Result:
        """
        Args:
            user_id: UUID of authenticated user
            job_id: UUID of job to discard
        
        Returns:
            Result with success message or Error
        """
        try:
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