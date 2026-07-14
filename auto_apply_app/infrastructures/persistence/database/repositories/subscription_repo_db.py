# =============================================================================
# subscription_repo_db.py
# =============================================================================
from uuid import UUID
from typing import List, Optional
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.domain.entities.user_subscription import UserSubscription
from auto_apply_app.application.repositories.subscription_repo import SubscriptionRepository
from auto_apply_app.infrastructures.persistence.database.models.schema import UserSubscriptionDB


# Deduct credits and write the ledger row in ONE statement. The guarded UPDATE stays a
# single atomic check-and-set (the property commit 350ce89 established), and the INSERT
# feeds off its RETURNING, so a CONSUME row can never exist without its decrement, nor a
# decrement without its row. 'CONSUME' is the literal stored by the enum column, which is
# mapped with native_enum=False and therefore persists the enum NAME as VARCHAR.
_CONSUME_CREDITS_SQL = text("""
    WITH consumed AS (
        UPDATE user_subscriptions
           SET ai_credits_balance = ai_credits_balance - :amount
         WHERE user_id = :user_id
           AND ai_credits_balance >= :amount
        RETURNING user_id, ai_credits_balance
    )
    INSERT INTO credit_transactions (user_id, delta, balance_after, kind, created_at)
    SELECT user_id, (0 - :amount), ai_credits_balance, 'CONSUME', now()
      FROM consumed
    RETURNING balance_after
""")


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
            id=subscription.id, # 🚨 FIX 1: Crucial for merge() to UPDATE instead of INSERT!
            user_id=subscription.user_id,
            email=subscription.email,
            account_type=subscription.account_type,
            is_active=subscription.is_active,
            is_past_due=subscription.is_past_due,
            grace_days=subscription.grace_days,
            ai_credits_balance=subscription.ai_credits_balance,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            cancel_at=subscription.cancel_at,
            next_billing_date=subscription.next_billing_date,
            stripe_customer_id=subscription.stripe_customer_id,
            stripe_subscription_id=subscription.stripe_subscription_id,
        )
        await self.session.merge(sub_db)

    async def try_consume_credits(self, user_id: str | UUID, amount: int) -> Optional[int]:
        # Atomic guarded decrement: the WHERE balance >= amount clause makes the
        # check-and-deduct a single statement, so concurrent workers can neither
        # lose a decrement nor drive the balance negative. RETURNING gives us the
        # post-decrement balance without a second read.
        #
        # The ledger row is written by the same statement, via a data-modifying CTE.
        # Appending it afterwards with a second INSERT would reopen the window this
        # method exists to close: the decrement could commit while the ledger write
        # failed, and the two would drift apart forever. Here, if the guard does not
        # match, `consumed` is empty, the INSERT selects zero rows, and the outer
        # RETURNING yields nothing — so `None` still means exactly what it meant
        # before: missing row, or insufficient balance.
        uuid = UUID(str(user_id)) if not isinstance(user_id, UUID) else user_id
        result = await self.session.execute(
            _CONSUME_CREDITS_SQL,
            {"user_id": uuid, "amount": amount},
        )
        row = result.first()
        return row[0] if row is not None else None

    async def list_active(self) -> List[UserSubscription]:
        result = await self.session.execute(
            select(UserSubscriptionDB).where(UserSubscriptionDB.is_active.is_(True))
        )
        return [self._map_to_entity(s) for s in result.scalars().all()]

    def _map_to_entity(self, sub_db: UserSubscriptionDB) -> UserSubscription:
        subs = UserSubscription(
            user_id=sub_db.user_id,
            email=sub_db.email,
            account_type=sub_db.account_type,
            is_active=sub_db.is_active,
            is_past_due=sub_db.is_past_due,
            grace_days=sub_db.grace_days,
            ai_credits_balance=sub_db.ai_credits_balance,
            current_period_start=sub_db.current_period_start,
            current_period_end=sub_db.current_period_end,
            cancel_at=sub_db.cancel_at,
            next_billing_date=sub_db.next_billing_date,
            stripe_customer_id=sub_db.stripe_customer_id,
            stripe_subscription_id=sub_db.stripe_subscription_id,
        )
        # 🚨 FIX 2: Restore the real ID!
        subs.id = sub_db.id 
        return subs