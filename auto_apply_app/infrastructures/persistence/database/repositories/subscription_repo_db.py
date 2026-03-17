# =============================================================================
# subscription_repo_db.py
# =============================================================================
from uuid import UUID
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.application.repositories.subscription_repo import SubscriptionRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import UserSubscriptionDB


class SubscriptionRepoDB(SubscriptionRepository):

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: str | UUID) -> Optional[UserSubscription]:
        """Accepts both str and UUID — normalizes internally."""
        uuid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id
        result = await self.session.execute(
            select(UserSubscriptionDB).where(UserSubscriptionDB.user_id == uuid)
        )
        sub_db = result.scalar_one_or_none()
        return self._map_to_entity(sub_db) if sub_db else None

    async def get_by_stripe_id(self, stripe_subscription_id: str) -> Optional[UserSubscription]:
        if not stripe_subscription_id:
            return None
        result = await self.session.execute(
            select(UserSubscriptionDB).where(
                UserSubscriptionDB.stripe_subscription_id == stripe_subscription_id
            )
        )
        sub_db = result.scalar_one_or_none()
        return self._map_to_entity(sub_db) if sub_db else None

    async def get_by_customer_id(self, stripe_customer_id: str) -> Optional[UserSubscription]:
        if not stripe_customer_id:
            return None
        result = await self.session.execute(
            select(UserSubscriptionDB).where(
                UserSubscriptionDB.stripe_customer_id == stripe_customer_id
            )
        )
        sub_db = result.scalar_one_or_none()
        return self._map_to_entity(sub_db) if sub_db else None

    async def save(self, subscription: UserSubscription) -> None:
        sub_db = UserSubscriptionDB(
            user_id=subscription.user_id,
            email=subscription.email,
            account_type=subscription.account_type,
            is_active=subscription.is_active,
            is_past_due=subscription.is_past_due,
            grace_days=subscription.grace_days,
            ai_credits_balance=subscription.ai_credits_balance, # ✅ Added AI Credits
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            cancel_at=subscription.cancel_at,
            next_billing_date=subscription.next_billing_date,
            stripe_customer_id=subscription.stripe_customer_id,
            stripe_subscription_id=subscription.stripe_subscription_id,
        )
        await self.session.merge(sub_db)

    def _map_to_entity(self, sub_db: UserSubscriptionDB) -> UserSubscription:
        subs = UserSubscription(
            # Optional: if UserSubscription extends Entity, you might need `id=sub_db.user_id` here too
            user_id=sub_db.user_id,
            email=sub_db.email,
            account_type=sub_db.account_type,
            is_active=sub_db.is_active,
            is_past_due=sub_db.is_past_due,
            grace_days=sub_db.grace_days,
            ai_credits_balance=sub_db.ai_credits_balance, # ✅ Added AI Credits
            current_period_start=sub_db.current_period_start,
            current_period_end=sub_db.current_period_end,
            cancel_at=sub_db.cancel_at,
            next_billing_date=sub_db.next_billing_date,
            stripe_customer_id=sub_db.stripe_customer_id,
            stripe_subscription_id=sub_db.stripe_subscription_id,
        )
        
        return subs