from dataclasses import dataclass

from auto_apply_app.interfaces.viewmodels.base import OperationResult
from auto_apply_app.application.common.result import Result
from auto_apply_app.interfaces.presenters.base_presenter import SubPresenter
from auto_apply_app.application.use_cases.subscription_use_cases import (
    GetUserSubscriptionUseCase,
    CreateCheckoutSessionUseCase,
    HandlePaymentWebhookUseCase,
    GetManagementPortalUseCase
)
from auto_apply_app.application.dtos.subscription_dtos import (
    GetUserSubscriptionRequest,
    CreateCheckoutSessionRequest,
    HandlePaymentWebhookRequest
)


@dataclass
class SubscriptionController:
    """
    Interface Adapter: Orchestrates Subscription management.
    """
    get_subscription_use_case: GetUserSubscriptionUseCase
    create_checkout_use_case: CreateCheckoutSessionUseCase
    handle_webhook_use_case: HandlePaymentWebhookUseCase
    get_portal_use_case: GetManagementPortalUseCase
    presenter: SubPresenter

    async def handle_get_subscription(self, user_id: str) -> OperationResult:
        """Get user's subscription details."""
        try:
            request = GetUserSubscriptionRequest(user_id=user_id)
            result = await self.get_subscription_use_case.execute(request)

            if result.is_success:
                view_model = self.presenter.present_sub(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_create_checkout(
        self, 
        user_id: str,
        plan_name: str
    ) -> OperationResult:
        """Create a Stripe checkout session."""
        try:
            request = CreateCheckoutSessionRequest(
                user_id=user_id,
                plan_name=plan_name
            )
            result = await self.create_checkout_use_case.execute(request)

            if result.is_success:
                # Result.value is a dict: {"checkout_url": "..."}
                view_model = self.presenter.present_sub(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_webhook(
        self,
        payload: bytes,
        signature: str
    ) -> OperationResult:
        """Handle Stripe webhook events."""
        try:
            request = HandlePaymentWebhookRequest(
                payload=payload,
                signature=signature
            )
            result = await self.handle_webhook_use_case.execute(request)

            if result.is_success:
                # Result.value is either UserSubscriptionResponse or dict
                view_model = self.presenter.present_sub(result.value)
                return OperationResult.succeed(value=view_model)
            
            return self._present_error(result)

        except ValueError as e:
            return self._present_validation_exception(e)

    async def handle_get_portal(self, user_id: str) -> OperationResult:
        """Get Stripe customer portal URL."""
        try:
            result = await self.get_portal_use_case.execute(user_id)

            if result.is_success:
                # Result.value is a dict: {"portal_url": "..."}
                view_model = self.presenter.present_sub(result.value)
                return OperationResult.succeed(value=view_model)
            
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