from abc import ABC, abstractmethod
from typing import List, Optional
from uuid import UUID

from auto_apply_app.domain.entities.user_subscription import UserSubscription

class SubscriptionRepository(ABC):
    """
    Interface for UserSubscription persistence.
    Lives in the Application Layer.
    """

    @abstractmethod
    async def get_by_user_id(self, user_id: str) -> Optional[UserSubscription]:
        """
        Retrieves the subscription associated with a specific domain User.
        Used by: GetUserSubscriptionUseCase, CreateCheckoutSessionUseCase.
        """
        pass

    @abstractmethod
    async def get_by_stripe_id(self, stripe_subscription_id: str) -> Optional[UserSubscription]:
        """
        Finds a record using the Stripe Subscription ID (sub_xxx).
        Used by: HandlePaymentWebhookUseCase (failed payments).
        """
        pass

    @abstractmethod
    async def get_by_customer_id(self, stripe_customer_id: str) -> Optional[UserSubscription]:
        """
        Finds a record using the Stripe Customer ID (cus_xxx).
        Used by: HandlePaymentWebhookUseCase (invoice payments).
        """
        pass

    @abstractmethod
    async def save(self, subscription: UserSubscription) -> None:
        """
        Persists a new subscription or updates an existing one.
        In a SQL implementation, this would handle 'upsert' logic.
        """
        pass

    @abstractmethod
    async def try_consume_credits(self, user_id: str | UUID, amount: int) -> Optional[int]:
        """
        Atomically deduct `amount` AI credits iff the balance can cover it.

        Implemented as a single guarded UPDATE so concurrent agent workers cannot
        lose a decrement (read-modify-write on the whole row would drop one) or
        overspend below zero.

        Returns the NEW balance on success, or None if the row is missing or the
        balance is insufficient (caller disambiguates).

        Implementations MUST also append the matching CONSUME row to the credit ledger
        within the same statement — see CreditTransactionRepository's docstring.
        """
        pass

    @abstractmethod
    async def list_active(self) -> List[UserSubscription]:
        """
        All subscriptions with is_active = True.

        Used by the admin dashboard to estimate credits consumed in the current period
        (allocated - balance) for the transition window before the ledger has history.
        Returns entities so the allocation rules stay in the domain rather than in SQL.
        """
        pass