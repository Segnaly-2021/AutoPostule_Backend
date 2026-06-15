# auto_apply_app/interfaces/controllers/free_search_controller.py

from dataclasses import dataclass
from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.interfaces.presenters.base_presenter import FreeSearchPresenter
from auto_apply_app.application.use_cases.free_search_use_cases import FreeSearchUseCase
from auto_apply_app.application.dtos.free_search_dtos import FreeSearchRequest
from auto_apply_app.application.common.result import Result


@dataclass
class FreeSearchController:
    """
    Interface Adapter: Orchestrates authenticated free-tier job search
    with per-user daily quota enforcement.
    """
    free_search_use_case: FreeSearchUseCase
    presenter: FreeSearchPresenter

    async def handle_search(
        self, user_id: str, query: str, target_count: int
    ) -> OperationResult:
        # 1. Build & validate the request DTO
        try:
            request = FreeSearchRequest(
                user_id=user_id,
                query=query,
                target_count=target_count,
            )
        except ValueError as e:
            return OperationResult.fail(
                message=str(e),
                code="VALIDATION_ERROR",
            )

        # 2. Delegate to the use case
        result: Result = await self.free_search_use_case.execute(request)

        # 3. Success → present the viewmodel
        if result.is_success:
            view_model = self.presenter.present_search_results(result.value)
            return OperationResult.succeed(value=view_model)

        # 4. Failure → unwrap enums to strings for the HTTP layer
        error = result.error
        return OperationResult.fail(
            message=error.message,
            code=error.code.value if error.code else None,
            reason=error.reason.value if error.reason else None,
            details=error.details,
        )