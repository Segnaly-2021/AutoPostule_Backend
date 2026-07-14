# auto_apply_app/interfaces/controllers/analytics_controllers.py

from dataclasses import dataclass
from typing import Optional

from auto_apply_app.application.dtos.analytics_dtos import RecordPageViewRequest
from auto_apply_app.application.use_cases.analytics_use_cases import RecordPageViewUseCase
from auto_apply_app.interfaces.presenters.base_presenter import AnalyticsPresenter
from auto_apply_app.interfaces.viewmodels.base import OperationResult


@dataclass
class AnalyticsController:

    record_page_view_use_case: RecordPageViewUseCase
    analytics_presenter: AnalyticsPresenter

    async def handle_record_page_view(
        self,
        visitor_id: str,
        path: str,
        referrer: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> OperationResult:
        try:
            request_dto = RecordPageViewRequest(
                visitor_id=visitor_id,
                path=path,
                referrer=referrer,
                user_id=user_id,
            )

            result = await self.record_page_view_use_case.execute(request_dto)

            if result.is_success:
                return OperationResult.succeed(result.value)

            return self._handle_error(result)

        except ValueError as e:
            error_vm = self.analytics_presenter.present_error(str(e), "VALIDATION_ERROR")
            return OperationResult.fail(error_vm.message, error_vm.code)

    def _handle_error(self, result):
        error_vm = self.analytics_presenter.present_error(
            result.error.message,
            str(result.error.code.name)
        )
        return OperationResult.fail(error_vm.message, error_vm.code)
