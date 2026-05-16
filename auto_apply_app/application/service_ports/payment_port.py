from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from uuid import UUID


class PaymentPort(ABC):
    """
    Outbound port for payment-related operations.
    Keeps the application decoupled from the specific payment provider (Stripe).
    """

    @abstractmethod
    async def create_checkout_session(
        self,
        user_id: UUID,
        email: str,
        price_id: str,
        success_url: str,
        cancel_url: str,
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Creates a session for the initial purchase and returns the redirect URL.
        """
        pass

    @abstractmethod
    async def create_portal_session(self, stripe_customer_id: str) -> str:
        """
        Creates a link to the hosted billing portal for subscription management.
        (Cancellations, payment method updates, etc.)
        """
        pass

    @abstractmethod
    def parse_webhook_event(self, payload: bytes, sig_header: str) -> Dict[str, Any]:
        """
        Verifies the authenticity of the provider's webhook and returns
        the event data in a structured format.
        """
        pass