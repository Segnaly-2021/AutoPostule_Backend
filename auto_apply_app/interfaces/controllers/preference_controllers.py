# auto_apply_app/interfaces/controllers/preferences_controller.py
from dataclasses import dataclass
from typing import Dict, Optional

from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.application.common.result import Result
from auto_apply_app.interfaces.presenters.base_presenter import PreferencesPresenter
from auto_apply_app.application.use_cases.preferences_use_cases import (
    GetUserPreferencesUseCase,
    UpdateUserPreferencesUseCase
)
from auto_apply_app.application.dtos.preferences_dtos import (
    GetUserPreferencesRequest,
    UpdateUserPreferencesRequest,
    BoardCredentialDTO
)

@dataclass
class PreferencesController:
    """
    Interface Adapter: Orchestrates User Preferences & Credentials management.
    """
    get_prefs_use_case: GetUserPreferencesUseCase
    update_prefs_use_case: UpdateUserPreferencesUseCase
    presenter: PreferencesPresenter

    async def handle_get(self, user_id: str) -> OperationResult:
        try:
            request = GetUserPreferencesRequest(user_id=user_id)
            result = await self.get_prefs_use_case.execute(request)

            if result.is_success:
                view_model = self.presenter.present_preferences(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_update(
        self, 
        user_id: str,
        is_full_automation: bool,
        creativity_level: int,
        ai_model: str, # ✅ NEW: Receive from endpoint
        active_boards: Dict[str, bool],
        credentials: Optional[Dict[str, Dict[str, str]]] = None 
    ) -> OperationResult:
        try:
            # 1. Map raw credential dicts to DTOs
            credentials_dtos = None
            if credentials:
                credentials_dtos = {}
                for board, cred_data in credentials.items():
                    login = cred_data.get("login", "")
                    password = cred_data.get("password", "")
                    
                    if login or password:
                        credentials_dtos[board] = BoardCredentialDTO(
                            login=login,
                            password=password
                        )

            # 2. Build Request DTO
            request = UpdateUserPreferencesRequest(
                user_id=user_id,
                is_full_automation=is_full_automation,
                creativity_level=creativity_level,
                ai_model=ai_model, # ✅ NEW: Pass to Application Layer
                active_boards=active_boards,
                credentials=credentials_dtos
            )
            
            # 3. Execute Use Case
            result = await self.update_prefs_use_case.execute(request)

            if result.is_success:
                return OperationResult.succeed(
                    value={"message": "Preferences updated successfully", "success": True}
                )
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    # --- Private Helpers for Consistent Error Handling ---
    def _present_error(self, result: Result) -> OperationResult:
        error_vm = self.presenter.present_error(
            result.error.message, 
            str(result.error.code.name)
        )
        return OperationResult.fail(error_vm.message, error_vm.code)

    def _present_validation_exception(self, e: ValueError) -> OperationResult:
        error_vm = self.presenter.present_error(str(e), "VALIDATION_ERROR")
        return OperationResult.fail(error_vm.message, error_vm.code)