# auto_apply_app/interfaces/controllers/agent_state_controllers.py
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.application.common.result import Result
from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.interfaces.presenters.base_presenter import AgentStatePresenter
from auto_apply_app.application.use_cases.agent_state_use_cases import (
    GetAgentStateUseCase,
    RequestAgentShutdownUseCase,
)


@dataclass
class AgentStateController:
    """
    Interface Adapter for kill-switch state.
    Note: BindAgentToSearchUseCase is NOT exposed here — it's called
    internally by the MasterAgent at run start, not by API consumers.
    """
    get_agent_state_use_case: GetAgentStateUseCase
    request_shutdown_use_case: RequestAgentShutdownUseCase
    presenter: AgentStatePresenter

    async def handle_get(self, user_id: str) -> OperationResult:
        try:
            result = await self.get_agent_state_use_case.execute(UUID(user_id))
            if result.is_success:
                view_model = self.presenter.present(result.value)
                return OperationResult.succeed(value=view_model)
            return self._present_error(result)
        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_request_shutdown(self, user_id: str, search_id: str) -> OperationResult:
        """
        User clicks 'Stop' on a specific running search.
        Shutdown is rejected if search_id doesn't match the bound search
        (prevents stale shutdown signals from killing fresh runs).
        """
        try:
            result = await self.request_shutdown_use_case.execute(
                UUID(user_id), UUID(search_id)
            )
            if result.is_success:
                view_model = self.presenter.present(result.value)
                return OperationResult.succeed(value=view_model)
            return self._present_error(result)
        except ValueError as e:
            return self._present_validation_exception(e)

    def _present_error(self, result: Result) -> OperationResult:
        error_vm = self.presenter.present_error(
            result.error.message,
            str(result.error.code.name),
        )
        return OperationResult.fail(error_vm.message, error_vm.code)

    def _present_validation_exception(self, e: ValueError) -> OperationResult:
        error_vm = self.presenter.present_error(str(e), "VALIDATION_ERROR")
        return OperationResult.fail(error_vm.message, error_vm.code)