from dataclasses import dataclass
from typing import Optional
from pydantic import EmailStr

from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.application.common.result import Result
from auto_apply_app.interfaces.presenters.base_presenter import UserPresenter
from auto_apply_app.application.use_cases.user_use_cases import (
    GetUserUseCase,
    UpdateUserUseCase,
    DeleteUserUseCase
)
from auto_apply_app.application.dtos.user_dtos import (
    GetUserRequest,
    UpdateUserRequest
)



@dataclass
class UserController:
    """
    Interface Adapter: Orchestrates User Profile management.
    """
    get_user_use_case: GetUserUseCase
    update_user_use_case: UpdateUserUseCase
    delete_user_use_case: DeleteUserUseCase
    presenter: UserPresenter
   

    async def handle_get(self, user_id: str) -> OperationResult:
        try:
            request = GetUserRequest(user_id=user_id)
            # Use cases are now awaited to support async repository/DB calls
            result = await self.get_user_use_case.execute(request)

            if result.is_success:
                view_model = self.presenter.present_user(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_update(
        self, 
        user_id: str,
        fname: Optional[str] = None,
        lname: Optional[str] = None,
        email: Optional[EmailStr] = None,
        current_position: Optional[str] = None,
        current_company: Optional[str] = None,
        resume_path: Optional[str] = None,        
        phone_number: Optional[str] = None,
        school_type: Optional[str] = None,
        graduation_year: Optional[str] = None,
        major: Optional[str] = None,
        study_level: Optional[str] = None
    ) -> OperationResult:
        try:
            request = UpdateUserRequest(
                user_id=user_id,
                user_firstname=fname,
                user_lastname=lname,
                user_email=email,
                user_resume_dir=resume_path,
                user_current_position=current_position,
                user_current_company=current_company,
                user_phone_number=phone_number,  
                user_school_type=school_type,
                user_graduation_year=graduation_year,
                user_major=major,
                user_study_level=study_level  
            )
            
            result = await self.update_user_use_case.execute(request)

            if result.is_success:
                view_model = self.presenter.present_user(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_delete(self, user_id: str) -> OperationResult:
        try:
            request = GetUserRequest(user_id=user_id)
            result = await self.delete_user_use_case.execute(request)

            if result.is_success:
                # DeletionOutcome usually contains the deleted ID or a success message
                return OperationResult.succeed(result.value)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    # --- Private Helpers for Consistent Error Handling ---

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