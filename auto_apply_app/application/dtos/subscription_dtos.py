from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from auto_apply_app.domain.entities.user_subscription import UserSubscription



@dataclass
class GetUserSubscriptionRequest:
    user_id: UUID


    def __post_init__(self):
        if not self.user_id:
            raise ValueError("User ID is required")

    def to_execution_params(self) -> dict:
        return {"user_id": self.user_id}
    

    
@dataclass
class UserSubscriptionResponse:
    
    user_id: UUID
    account_type: str
    is_active: bool
    is_past_due: bool
    current_period_end: datetime
    current_period_start: datetime  
    daily_limit: int
    cancel_at: datetime
    can_run_agent: bool
    next_billing_date: datetime
    
    # --- [NEW] Expose Credits to Frontend ---
    ai_credits_balance: int
    allocated_ai_credits: int

    @classmethod
    def from_entity(cls, entity: UserSubscription) -> "UserSubscriptionResponse":
        return cls(
            user_id=entity.user_id,
            account_type=entity.account_type.value,
            is_active=entity.is_active,
            is_past_due=entity.is_past_due,
            current_period_end=entity.current_period_end,
            current_period_start=entity.current_period_start,
            cancel_at=entity.cancel_at,
            daily_limit=entity.daily_limit,
            can_run_agent=entity.can_run_agent(),
            next_billing_date=entity.next_billing_date,
            
            # --- [NEW] Map the credit properties ---
            ai_credits_balance=entity.ai_credits_balance,
            allocated_ai_credits=entity.allocated_ai_credits,
        )



@dataclass
class CreateCheckoutSessionRequest:
    user_id: UUID
    plan_name: str  # e.g., "BASIC" or "PRO"

    def __post_init__(self):
        if not self.user_id:
            raise ValueError("User ID is required")

        if not self.plan_name:
            raise ValueError("Subscription plan is required")

    def to_execution_params(self) -> dict:
        
        return {
            "user_id": self.user_id,
            "plan_name": self.plan_name.upper()
        }

@dataclass
class CheckoutSessionResponse:
    checkout_url: str


@dataclass
class HandlePaymentWebhookRequest:
    payload: bytes
    signature: str

    def __post_init__(self):
        if not self.payload:
            raise ValueError("Payload is required")
        
        if not self.signature:
            raise ValueError("Signature is required")

    def to_execution_params(self) -> dict:
        return {
            "payload": self.payload,
            "signature": self.signature
        }
    

@dataclass
class CancelSubscriptionRequest:
    user_id: UUID

@dataclass
class CancelSubscriptionResponse:
    portal_url: str