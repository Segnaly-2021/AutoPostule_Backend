# auto_apply_app/interfaces/controllers/admin_controllers.py

from dataclasses import dataclass

from auto_apply_app.application.use_cases.admin_use_cases import GetAdminOverviewMetricsUseCase
from auto_apply_app.interfaces.presenters.base_presenter import AdminPresenter
from auto_apply_app.interfaces.viewmodels.base import OperationResult


@dataclass
class AdminController:

    get_overview_use_case: GetAdminOverviewMetricsUseCase
    admin_presenter: AdminPresenter

    async def handle_get_overview(self) -> OperationResult:
        try:
            result = await self.get_overview_use_case.execute()

            if result.is_success:
                overview_vm = self.admin_presenter.present_overview(result.value)
                return OperationResult.succeed(value=overview_vm)

            return self._handle_error(result)

        except ValueError as e:
            error_vm = self.admin_presenter.present_error(str(e), "VALIDATION_ERROR")
            return OperationResult.fail(error_vm.message, error_vm.code)

    def _handle_error(self, result):
        error_vm = self.admin_presenter.present_error(
            result.error.message,
            str(result.error.code.name)
        )
        return OperationResult.fail(error_vm.message, error_vm.code)
