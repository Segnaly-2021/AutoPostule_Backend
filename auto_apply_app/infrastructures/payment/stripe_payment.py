# ==============================================================================
# FILE: auto_apply_app/infrastructures/payment/stripe_payment.py
#
# WHAT IT DOES:
#   Adapter for Stripe billing. It creates hosted Checkout pages and Customer Portal
#   links, syncs customer emails, and verifies incoming webhooks. Payment flow: the
#   user pays on Stripe, Stripe calls POST /subscription/webhook, this file checks the
#   signature, and HandlePaymentWebhookUseCase updates the subscription.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/app.py: injected as payment_port
#
# EXTERNAL DEPENDENCIES:
#   - stripe: official SDK for Checkout, Billing Portal, Customers and Webhooks
#   - Stripe: the payment service (STRIPE_API_KEY, STRIPE_WEBHOOK_SECRET)
# ==============================================================================
import stripe
import logging
from typing import Any, Dict, Optional
from uuid import UUID
import os

from auto_apply_app.application.service_ports.payment_port import PaymentPort

logger = logging.getLogger(__name__)


class StripePaymentAdapter(PaymentPort):
    def __init__(self):
        stripe.api_key = os.getenv("STRIPE_API_KEY")
        self.webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")
        self.return_url = os.getenv("APP_RETURN_URL", "https://autopostule.com/job-search/home")

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
        Initializes the Stripe Checkout flow.
        We pass the user_id in metadata so we can identify them in the webhook.
        """
        # NOTE: possible issue: this is the synchronous Stripe call inside an async method,
        # so it blocks the event loop (all requests on this worker) until Stripe answers.
        # The portal and customer-update calls below do the same.
        session = stripe.checkout.Session.create(
            customer_email=email,
            payment_method_types=['card'],
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode='subscription',
            success_url=success_url,
            cancel_url=cancel_url,
            metadata={
                "user_id": str(user_id),
                **(metadata or {}),
            },
            client_reference_id=str(user_id),

            # --- UI / UX improvements ---
            locale='fr',                             # French UI to match the app
            allow_promotion_codes=True,              # let users enter promo codes
            custom_text={
                'submit': {
                    'message': (
                        "En confirmant votre abonnement, vous acceptez nos conditions de vente et d'utilisation."
                        "Vous pouvez annuler à tout moment depuis votre espace client."
                    )
                }
            },
        )
        return session.url

    async def create_portal_session(self, stripe_customer_id: str) -> str:
        """
        Generates a link to the Stripe-hosted Customer Portal.
        """
        session = stripe.billing_portal.Session.create(
            customer=stripe_customer_id,
            return_url=self.return_url,
        )
        return session.url

    async def update_customer_email(self, stripe_customer_id: str, email: str) -> None:
        """
        Syncs the Stripe customer's email. Best-effort: any Stripe failure is
        logged and swallowed so it can never roll back a committed DB change.
        """
        try:
            stripe.Customer.modify(stripe_customer_id, email=email)
        except Exception:
            logger.exception("Stripe customer email sync failed for %s", stripe_customer_id)

    def parse_webhook_event(self, payload: bytes, sig_header: str) -> Dict[str, Any]:
        """
        Verifies the signature to prevent 'webhook spoofing'.
        """
        try:
            # The webhook URL is public, so this signature check is what proves the
            # call came from Stripe. It must receive the exact raw request bytes:
            # re-serialized JSON would not match the signature.
            event = stripe.Webhook.construct_event(
                payload, sig_header, self.webhook_secret
            )
            return event
        except ValueError as e:
            print(f"STRIPE WEBHOOK PAYLOAD ERROR: {e}")
            raise Exception("Invalid payload")
        # NOTE: possible issue: confirm `stripe.error` still exists in the pinned stripe==14.4.1;
        # newer SDKs expose this as stripe.SignatureVerificationError. If it is gone, a bad
        # signature raises AttributeError here (the request is still rejected).
        except stripe.error.SignatureVerificationError as e:
            print(f"STRIPE WEBHOOK SIGNATURE ERROR: {e}")
            raise Exception("Invalid signature")