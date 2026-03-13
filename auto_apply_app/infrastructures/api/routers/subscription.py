from fastapi import APIRouter, status, Request, Header, Depends
from typing import Annotated, Optional

from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.subscription_controllers import SubscriptionController
from auto_apply_app.infrastructures.api.schema.subs_schema import CheckoutSessionRequest


router = APIRouter()


def get_subscription_controller(
    container: Annotated[Application, Depends(get_container)]
) -> SubscriptionController:
    """Extract SubscriptionController from the application container."""
    return container.subscription_controller


SubscriptionControllerDep = Annotated[SubscriptionController, Depends(get_subscription_controller)]


@router.get(
    "/me",
    summary="Get my subscription",
    description="Get the authenticated user's subscription details"
)
async def get_my_subscription(
    current_user_id: CurrentUserId,
    controller: SubscriptionControllerDep
):
    """
    Get the authenticated user's subscription details.
    
    Returns:
    - **account_type**: FREE, BASIC, or PRO
    - **is_active**: Whether subscription is currently active
    - **current_period_end**: When current period ends
    - **cancel_at**: Scheduled cancellation date (if applicable)
    - **grace_days**: Days remaining in grace period
    """
    result = await controller.handle_get_subscription(current_user_id)
    return handle_result(result)



@router.post(
    "/checkout",
    status_code=status.HTTP_200_OK,
    summary="Create checkout session",
    description="Create a Stripe checkout session for subscription upgrade"
)
async def create_checkout_session(
    data: CheckoutSessionRequest,
    current_user_id: CurrentUserId,
    controller: SubscriptionControllerDep
):
    """
    Create a Stripe checkout session.
    
    **Security**: Uses user_id from token, not request body.
    
    - **plan_name**: "BASIC" or "PRO"
    
    Returns a checkout URL to redirect the user to Stripe's hosted page.
    After successful payment, user will be redirected back to your app.
    """
    # ✅ SECURITY: Use user_id from token, ignore any user_id in request body
    result = await controller.handle_create_checkout(
        user_id=current_user_id,
        plan_name=data.plan_name
    )
    return handle_result(result)



@router.post(
    "/webhook",
    status_code=status.HTTP_200_OK,
    summary="Stripe webhook endpoint",
    description="Receive and process Stripe webhook events"
)
async def stripe_webhook(
    request: Request,
    controller: SubscriptionControllerDep,
    stripe_signature: Annotated[Optional[str], Header(alias="stripe-signature")] = None
    
):
    """
    Stripe webhook endpoint.
    
    **Security**: This endpoint is called by Stripe, not by users.
    The signature header is validated to ensure authenticity.
    
    Handles events:
    - checkout.session.completed
    - invoice.paid
    - invoice.payment_failed
    - customer.subscription.updated
    
    ⚠️ **Important**: This endpoint must be publicly accessible (no auth).
    Configure this URL in your Stripe Dashboard.
    """
    payload = await request.body()
    
    result = await controller.handle_webhook(
        payload=payload,
        signature=stripe_signature or ""
    )
    
    return handle_result(result)



@router.get(
    "/portal",
    summary="Get customer portal",
    description="Get Stripe customer portal URL for subscription management"
)
async def get_customer_portal(
    current_user_id: CurrentUserId,
    controller: SubscriptionControllerDep
):
    """
    Get Stripe customer portal URL.
    
    Users can manage their subscription through Stripe's hosted portal:
    - Update payment method
    - Cancel subscription
    - View billing history
    - Download invoices
    
    Returns a URL to redirect the user to.
    """
    result = await controller.handle_get_portal(current_user_id)
    return handle_result(result)