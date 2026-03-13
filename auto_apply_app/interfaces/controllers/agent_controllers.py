# auto_apply_app/interfaces/controllers/agent_controller.py
from dataclasses import dataclass
from typing import List, Optional

from auto_apply_app.domain.value_objects import ContractType
from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.application.common.result import Result
from auto_apply_app.interfaces.presenters.base_presenter import AgentPresenter, JobPresenter
from auto_apply_app.application.use_cases.agent_use_cases import (
    ApproveJobUseCase,
    DiscardJobUseCase,
    GetJobsForReviewUseCase,
    StartJobSearchAgentUseCase,
    ResumeJobApplicationUseCase,
    KillJobSearchUseCase,
    UpdateCoverLetterUseCase
)
from auto_apply_app.application.dtos.agent_dtos import (
    StartAgentRequest,
    ResumeAgentRequest,
    KillAgentRequest
)


@dataclass
class AgentController:
    """
    Interface Adapter: Orchestrates Agent operations.
    """
    start_agent_use_case: StartJobSearchAgentUseCase
    resume_agent_use_case: ResumeJobApplicationUseCase
    kill_agent_use_case: KillJobSearchUseCase
    get_jobs_for_review_use_case: GetJobsForReviewUseCase
    update_cover_letter_use_case: UpdateCoverLetterUseCase
    approve_job_use_case: ApproveJobUseCase
    discard_job_use_case: DiscardJobUseCase
    presenter: AgentPresenter
    job_presenter: JobPresenter

    
    async def handle_start_agent(
        self,
        user_id: str,
        job_title: str,
        job_boards: List[str],
        location: Optional[str] = None,
        contract_types: Optional[List[ContractType]] = None,
        min_salary: Optional[int] = None,
        resume_path: Optional[str] = None,
        progress_callback: Optional[callable] = None
    ) -> OperationResult:
        """
        Start a new job search agent workflow.
        """
        try:
            request = StartAgentRequest(
                user_id=user_id,
                job_title=job_title,
                job_boards=job_boards,
                location=location,
                min_salary=min_salary,
                resume_path=resume_path,
                contract_types=contract_types,
            )
            
            result = await self.start_agent_use_case.execute(
                request,
                progress_callback=progress_callback
            )

            if result.is_success:
                view_model = self.presenter.present_agent_result(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)


    async def handle_resume_agent(
        self,
        user_id: str,
        search_id: str,
        apply_all: bool = True,
        progress_callback: Optional[callable] = None
    ) -> OperationResult:
        """
        Resume a paused job search workflow (for Premium users).
        """
        try:
            request = ResumeAgentRequest(
                user_id=user_id,
                search_id=search_id,
                apply_all=apply_all
            )
            
            result = await self.resume_agent_use_case.execute(
                request,
                progress_callback=progress_callback
            )

            if result.is_success:
                view_model = self.presenter.present_agent_result(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_kill_agent(
        self,
        user_id: str,
        search_id: str
    ) -> OperationResult:
        """
        Emergency stop: Kill a running job search.
        """
        try:
            request = KillAgentRequest(
                user_id=user_id,
                search_id=search_id
            )
            
            result = await self.kill_agent_use_case.execute(request)

            if result.is_success:
                view_model = self.presenter.present_agent_result(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)
        

    async def handle_get_jobs_for_review(
        self, 
        user_id: str, 
        search_id: str
    ) -> Result:
        """Get all GENERATED jobs for a search."""
        result = await self.get_jobs_for_review_use_case.execute(
            user_id=user_id,
            search_id=search_id
        )
        
        if result.is_failure:
            return result
        
        # Convert domain entities to ViewModels
        jobs = result.value
        job_vms = [self.job_presenter.present_job(job) for job in jobs]
        
        return Result.success(job_vms)

    async def handle_update_cover_letter(
        self, 
        user_id: str, 
        job_id: str, 
        cover_letter: str
    ) -> Result:
        """Update cover letter for a job."""
        result = await self.update_cover_letter_use_case.execute(
            user_id=user_id,
            job_id=job_id,
            cover_letter=cover_letter
        )
        
        if result.is_failure:
            return result
        
        return Result.success({
            "message": "Cover letter updated successfully",
            "job_id": job_id
        })

    async def handle_approve_job(
        self, 
        user_id: str, 
        job_id: str
    ) -> Result:
        """Approve a single job for submission."""
        result = await self.approve_job_use_case.execute(
            user_id=user_id,
            job_id=job_id
        )
        
        if result.is_failure:
            return result
        
        return Result.success({
            "message": "Job approved successfully",
            "job_id": job_id,
            "status": "APPROVED"
        })

    async def handle_discard_job(
        self, 
        user_id: str, 
        job_id: str
    ) -> Result:
        """Reject/discard a job."""
        result = await self.discard_job_use_case.execute(
            user_id=user_id,
            job_id=job_id
        )
        
        if result.is_failure:
            return result
        
        return Result.success({
            "message": "Job discarded successfully",
            "job_id": job_id,
            "status": "REJECTED"
        })
        



    # --- Private Helpers ---
    def _present_error(self, result: Result) -> OperationResult:
        """Maps Application-layer Error objects to ViewModels."""
        error_vm = self.presenter.present_error(
            result.error.message, 
            str(result.error.code.name)
        )
        return OperationResult.fail(error_vm.message, error_vm.code)

    def _present_validation_exception(self, e: ValueError) -> OperationResult:
        """Maps DTO/Data validation errors to ViewModels."""
        error_vm = self.presenter.present_error(str(e), "VALIDATION_ERROR")
        return OperationResult.fail(error_vm.message, error_vm.code)