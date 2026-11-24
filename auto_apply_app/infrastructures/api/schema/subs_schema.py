# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/schema/subs_schema.py
#
# WHAT IT DOES:
#   Pydantic shapes for billing: the checkout request and a subscription view.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/routers/subscription.py: CheckoutSessionRequest
#
# EXTERNAL DEPENDENCIES:
#   - pydantic: data validation and serialization
# ==============================================================================
# interfaces/api/schemas/subscription_schemas.py
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime




class CheckoutSessionRequest(BaseModel):
    """
    Request to create a checkout session.
    
    Note: user_id is NOT included - it's extracted from the JWT token.
    """
    plan_name: str = Field(..., description="Subscription plan to purchase")


class SubscriptionViewModel(BaseModel):
    """Response model for subscription details."""
    account_type: str
    is_active: bool
    current_period_end: Optional[datetime]
    next_billing_date: Optional[datetime]
    cancel_at: Optional[datetime]
    grace_days: Optional[int]
    stripe_customer_id: Optional[str]
    stripe_subscription_id: Optional[str]

    class Config:
        from_attributes = True