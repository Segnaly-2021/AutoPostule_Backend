# auto_apply_app/interfaces/controllers/job_offer_controller.py

from dataclasses import dataclass
from typing import Optional
from datetime import date

from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.interfaces.presenters.base_presenter import JobPresenter
from auto_apply_app.application.use_cases.job_offer_use_cases import (
    GetApplicationAnalyticsUseCase,
    GetUserApplicationsUseCase,
    ToggleInterviewStatusUseCase,
    ToggleResponseStatusUseCase,
    GetDailyStatsUseCase  # <-- NEW IMPORT
)
from auto_apply_app.application.dtos.job_offer_dtos import (
    GetUserApplicationsRequest,
    GetAnalyticsRequest,
    ToggleStatusRequest,
    GetDailyStatsRequest  
)


@dataclass
class JobOfferController:
    
    get_user_applications_use_case: GetUserApplicationsUseCase
    toggle_response_status_use_case: ToggleResponseStatusUseCase
    toggle_interview_status_use_case: ToggleInterviewStatusUseCase
    get_analytics_use_case: GetApplicationAnalyticsUseCase
    get_daily_stats_use_case: GetDailyStatsUseCase  # <-- NEW DEPENDENCY
    job_offer_presenter: JobPresenter

    # ---------- SEARCH / LIST ----------
    async def handle_get_list(
        self, 
        user_id: str, 
        page: int, 
        limit: int,
        company: Optional[str] = None,
        title: Optional[str] = None,
        location: Optional[str] = None,
        board: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        has_response: Optional[bool] = None,
        has_interview: Optional[bool] = None
    ) -> OperationResult:
        
        try:
            # 1. Construct the Internal DTO from raw inputs
            # The Controller acts as the factory for Application DTOs
            request_dto = GetUserApplicationsRequest(
                user_id=user_id,
                page=page,
                limit=limit,
                company=company,
                title=title,
                location=location,
                board=board,
                date_from=date_from,
                date_to=date_to,
                has_response=has_response,
                has_interview=has_interview
            )

            # 2. Await Use Case (Async)
            result = await self.get_user_applications_use_case.execute(request_dto)

            # 3. Present Result
            if result.is_success:
                dashboardView = self.job_offer_presenter.present_dashboard(result.value)
                return OperationResult.succeed(value=dashboardView)

            # 4. Handle Errors
            return self._handle_error(result)

        except ValueError as e:
            # Catch DTO validation errors (e.g. invalid date formats that slipped through)
            error_vm = self.job_offer_presenter.present_error(str(e), "VALIDATION_ERROR")
            return OperationResult.fail(error_vm.message, error_vm.code)


    # ---------- TOGGLE STATUS ----------
    async def handle_toggle_response(self, job_id: str, status: bool) -> OperationResult:
        try:
            request_dto = ToggleStatusRequest(job_offer_id=job_id, status=status)        
            result = await self.toggle_response_status_use_case.execute(request_dto)
            
            if result.is_success:
                # Return simple confirmation object
                return OperationResult.succeed({"id": job_id, "has_response": status})
                
            return self._handle_error(result)    
        except ValueError as e:
            error_vm = self.job_offer_presenter.present_error(str(e), "VALIDATION_ERROR")
            return OperationResult.fail(error_vm.message, error_vm.code)      


    # ---------- ANALYTICS ----------
    async def handle_analytics(self, user_id: str, period: str) -> OperationResult:
        try:

            request_dto = GetAnalyticsRequest(user_id=str(user_id), period=period)        
            result = await self.get_analytics_use_case.execute(request_dto)
            
            if result.is_success:
                return OperationResult.succeed(result.value)
                
            return self._handle_error(result)
        except ValueError as e:
            error_vm = self.job_offer_presenter.present_error(str(e), "VALIDATION_ERROR")
            return OperationResult.fail(error_vm.message, error_vm.code)
        


    # ---------- DAILY STATS ----------
    async def handle_get_daily_stats(self, user_id: str) -> OperationResult:
        try:
            request_dto = GetDailyStatsRequest(user_id=str(user_id))        
            result = await self.get_daily_stats_use_case.execute(request_dto)
            
            if result.is_success:
                stats_vm = self.job_offer_presenter.present_daily_stats(result.value)
                return OperationResult.succeed(value=stats_vm)
                
            return self._handle_error(result)
            
        except ValueError as e:
            error_vm = self.job_offer_presenter.present_error(str(e), "VALIDATION_ERROR")
            return OperationResult.fail(error_vm.message, error_vm.code)

    def _handle_error(self, result):
        error_vm = self.job_offer_presenter.present_error(
            result.error.message, 
            str(result.error.code.name)
        )
        return OperationResult.fail(error_vm.message, error_vm.code)

 