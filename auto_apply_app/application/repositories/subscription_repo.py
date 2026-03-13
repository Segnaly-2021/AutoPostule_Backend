from abc import ABC, abstractmethod
from typing import Optional

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