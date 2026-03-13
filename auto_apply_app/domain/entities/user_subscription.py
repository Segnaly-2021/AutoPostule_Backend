from dataclasses import dataclass, field
from datetime import datetime,timezone, timedelta
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity
from auto_apply_app.domain.value_objects import ClientType

@dataclass
class UserSubscription(Entity):
    user_id: UUID
    email: str
    account_type: ClientType = ClientType.FREE
    is_active: bool = False
    is_past_due: bool = False
    grace_days: int = 0
    ai_credits_balance: int = 0
    current_period_end: datetime = field(default_factory=lambda: datetime.now(timezone.utc))    
    current_period_start: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cancel_at: datetime | None = None
    next_billing_date: datetime | None = None
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None



    # --- Inside UserSubscription Entity ---
    def handle_renewal_failure(self):
        """Business rule: Decide whether to block or flag based on grace days."""
        effective_deadline = self.current_period_end + timedelta(days=self.grace_days)
        
        if datetime.now(timezone.utc) > effective_deadline:
           self.downgrade_to_free()
        else:
            # Still within grace period
            self.is_past_due = True
        

    def can_run_agent(self) -> bool:
        """The master rule for authorization including grace periods."""
        if not self.is_active:
            return False   

        if self.account_type == ClientType.FREE:
            return False   

        if self.ai_credits_balance <= 0:
            return False      
        
        effective_deadline = self.current_period_end + timedelta(days=self.grace_days)
        if datetime.now(timezone.utc) > effective_deadline:
            return False            
        
        if self.is_past_due and self.account_type == ClientType.BASIC:
            return False

        return True
    

    def downgrade_to_free(self):        
            self.is_active = False           
            self.is_past_due = False 
            self.grace_days = 0
            self.ai_credits_balance = 0
            self.account_type = ClientType.FREE

    def calculate_grace_days(self):       
        return 2 if self.account_type == ClientType.PREMIUM else 0
        

    @property
    def daily_limit(self) -> int:
        """Logic for limits lives here."""
        if self.account_type == ClientType.PREMIUM:
            return 50
        if self.account_type == ClientType.BASIC:
            return 15 
        
        return 0 # FREE users
    

    # --- [NEW] Credit System Logic ---
    @property
    def allocated_ai_credits(self) -> int:
        """
        Logic for AI generation limits per billing cycle (Cost Control).
        Includes a buffer of bonus credits for unsubmitted generations.
        """
        # Premium: Monthly (30 days) * (50 limit + 10 bonus) = 1800 credits
        if self.account_type == ClientType.PREMIUM:
            return 1800
            
        # Basic: Weekly (7 days) * (15 limit + 3 bonus) = 126 credits
        if self.account_type == ClientType.BASIC:
            return 126
            
        return 0

    def has_sufficient_credits(self, amount: int) -> bool:
        """Check if user can afford the current batch of cover letters."""
        return self.ai_credits_balance >= amount

    def consume_credits(self, amount: int):
        """Deducts credits. Must be called after the LLM generates the cover letters."""
        if not self.has_sufficient_credits(amount):
            raise ValueError(f"Insufficient AI credits. Required: {amount}, Balance: {self.ai_credits_balance}")
        self.ai_credits_balance -= amount

    def replenish_credits(self):
        """Called by your Stripe webhook when a new billing cycle starts."""
        self.ai_credits_balance = self.allocated_ai_credits


