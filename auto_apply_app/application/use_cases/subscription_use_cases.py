import logging
import os
from dataclasses import dataclass
from datetime import datetime, UTC

from auto_apply_app.application.common.result import Error, Result
from auto_apply_app.application.service_ports.payment_port import PaymentPort
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.domain.value_objects import ClientType
from auto_apply_app.application.dtos.subscription_dtos import (
    GetUserSubscriptionRequest,
    UserSubscriptionResponse,
    CreateCheckoutSessionRequest,
    CheckoutSessionResponse,
    HandlePaymentWebhookRequest,
)

logger = logging.getLogger(__name__)


@dataclass
class GetUserSubscriptionUseCase:
    
    uow: UnitOfWork

    async def execute(self, request: GetUserSubscriptionRequest) -> Result:
        try:
            params = request.to_execution_params()
            user_id = params["user_id"]
            async with self.uow as uow:
                # 1. Fetch the subscription directly from the repository
                subscription = await uow.subscription_repo.get_by_user_id(user_id)
                
                if not subscription:
                    # Based on Option B, a user should always have a record.
                    # If not, it's a legitimate 'Not Found' error.
                    return Result.failure(
                        Error.not_found("Subscription", user_id)
                    )

            # 2. Map to DTO and return success
            return Result.success(UserSubscriptionResponse.from_entity(subscription))

        except Exception:
            logger.exception(f"GetUserSubscriptionUseCase failed for user {request.user_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while retrieving the subscription."))
        

@dataclass
class CreateCheckoutSessionUseCase:
    uow: UnitOfWork 
    payment_port: PaymentPort
    FRONTEND_URL : str = os.getenv("FRONTEND_URL", "http://localhost:5173")

    async def execute(self, request: CreateCheckoutSessionRequest) -> Result:
        try:
            params = request.to_execution_params()
            user_id = params["user_id"]
            plan_name = params["plan_name"]
            async with self.uow as uow:
                # 1. Fetch User to get their email (Stripe needs this to pre-fill)
                subs = await uow.subscription_repo.get_by_user_id(user_id)
                if not subs:
                    return Result.failure(Error.not_found(f"User {user_id} not found"))

                # 2. Get the specific Price ID for the plan
                # In production, these IDs come from your config/environment variables
                price_id_env = self._map_plan_to_price_id(plan_name)
                if not price_id_env:
                    return Result.failure(Error.validation_error(f"Invalid plan: {plan_name}"))

                # 3. Use the Port to create the session
                # This generates the Stripe-hosted URL
                url = await self.payment_port.create_checkout_session(
                    user_id=subs.user_id,
                    email=subs.email,
                    price_id=os.getenv(price_id_env),
                    success_url=f"{self.FRONTEND_URL}/job-search/home?checkout=success",
                    cancel_url=f"{self.FRONTEND_URL}/#pricing",
                    metadata={
                        "user_id": str(subs.user_id),
                        "account_type": plan_name
                    }
                )

            return Result.success({"message": CheckoutSessionResponse(checkout_url=url)})

        except Exception:
            logger.exception(f"CreateCheckoutSessionUseCase failed for user {request.user_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while creating the checkout session."))

    def _map_plan_to_price_id(self, plan_name: str) -> str | None:
        """
        Maps domain plan names to Stripe Price IDs.
        In a real app, use: settings.STRIPE_BASIC_PRICE_ID
        """
        mapping = {
            "BASIC": "BASIC_PRICE_ID", 
            "PREMIUM": "PREMIUM_PRICE_ID"
        }
        return mapping.get(plan_name)


@dataclass
class HandlePaymentWebhookUseCase:
    uow: UnitOfWork
    payment_port: PaymentPort

    async def execute(self, request: HandlePaymentWebhookRequest) -> Result:
        try:
            params = request.to_execution_params()
            
            # 1. Parse and Verify the event via the Port
            # This ensures the request actually came from Stripe
            event = self.payment_port.parse_webhook_event(
                payload=params["payload"],
                sig_header=params["signature"]
            )

            # 2. Extract Business Data
            event_type = event.get("type")
            data_object = event.get("data", {}).get("object", {})
            if event_type in ["customer.subscription.updated"]:
                
                print(f"""
                    Event type: {event_type},\n 
                    Data object: {data_object}
                """)
                
            # 3. Handle specific events
            if event_type == "checkout.session.completed":
                print("Handling checkout.session.completed event...")
                return await self._handle_successful_payment(data_object, event_type)
            
            if event_type == "invoice.paid":
                print("Handling invoice.paid event...")
                return await self._handle_successful_payment(data_object, event_type)
            
            if event_type == "invoice.payment_failed":
                print("Handling invoice.payment_failed event...")
                return await self._handle_failed_payment(data_object)

            if event_type == "customer.subscription.updated":
                print("Handling customer.subscription.updated event...")
                return await self._handle_subscription_updated(data_object)
            
            if event_type == "customer.subscription.deleted":
                print("Handling customer.subscription.deleted event...")
                return await self._handle_subscription_updated(data_object)

            return Result.success("Event ignored (not relevant to business)")

        except Exception:
            logger.exception("HandlePaymentWebhookUseCase failed during Stripe webhook processing.")
            return Result.failure(Error.system_error("An unexpected error occurred while processing the payment webhook."))
        
        
        
    async def _handle_successful_payment(self, data: dict, event_type: str) -> Result:
        print(f"\nProcessing event: {event_type}")
        
        async with self.uow as uow:
            subscription = None
            customer_id = data.get("customer")

            # ---------------------------------------------------------
            # SCENARIO 1: FIRST PAYMENT (Checkout Session)
            # ---------------------------------------------------------
            if event_type == "checkout.session.completed":
                # 1. Identify by metadata (or client_reference_id)
                user_id = data.get("metadata", {}).get("user_id") or data.get("client_reference_id")
                account_type = data.get("metadata", {}).get("account_type")
                
                if not user_id:
                    user_email = data.get("customer_details", {}).get("email") or data.get("customer_email")
                    user = await uow.user_repo.get_by_email(user_email)
                    user_id = user.id if user else None

                    if not user_id:
                        return Result.failure(Error.system_error("Could not identify user for checkout session"))

                # 2. Fetch subscription by User ID
                subscription = await uow.subscription_repo.get_by_user_id(str(user_id).strip())
                if not subscription:
                    return Result.failure(Error.not_found("Subscription for user_id from checkout session"))
                
                # 3. Update subscription details
                subscription.is_active = True
                subscription.stripe_customer_id = customer_id
                subscription.stripe_subscription_id = data.get("subscription") 
                
                if not account_type:                        
                    amount_paid = data.get("amount_total") 
                    
                    if amount_paid == 990:          # €9.90 Basic Plan
                        account_type = "BASIC"
                    elif amount_paid == 4990:       # €49.90 Premium Plan
                        account_type = "PREMIUM"

                subscription.account_type = ClientType(account_type)                    
                subscription.grace_days = subscription.calculate_grace_days()
                
                # 🚨 [NEW] Fill the user's AI wallet for their new plan!
                subscription.replenish_credits()

            # ---------------------------------------------------------
            # SCENARIO 2: RECURRING PAYMENTS (Invoice)
            # ---------------------------------------------------------
            elif event_type == "invoice.paid":
                # 1. Identify purely by the Stripe Subscription ID
                stripe_sub_id = data.get("parent", {}).get("subscription_details", {}).get("subscription") 
                
                if not stripe_sub_id:
                    return Result.failure(Error.system_error("No subscription ID found on invoice"))

                # 2. Fetch subscription directly using the Stripe ID we saved earlier
                subscription = await uow.subscription_repo.get_by_stripe_id(stripe_sub_id)

                if not subscription:
                    user = await uow.user_repo.get_by_email(data.get("customer_email"))
                    if user:
                        subscription = await uow.subscription_repo.get_by_user_id(str(user.id))                  
                
                    if not subscription:
                        return Result.failure(Error.system_error(f"Subscription {stripe_sub_id} not found in database"))

                # 3. Update the dates (Metadata is irrelevant here)
                lines = data.get("lines", {}).get("data", [])
                if lines:
                    stripe_start = lines[0].get("period", {}).get("start")
                    stripe_end = lines[0].get("period", {}).get("end")
                    
                    subscription.current_period_start = datetime.fromtimestamp(stripe_start, tz=UTC) if stripe_start else None
                    subscription.current_period_end = datetime.fromtimestamp(stripe_end, tz=UTC)
                    subscription.next_billing_date = datetime.fromtimestamp(stripe_end, tz=UTC)
                
                subscription.is_active = True
                subscription.is_past_due = False
                
                # 🚨 [NEW] Replenish AI wallet for the new recurring billing cycle!
                subscription.replenish_credits()

            else:
                return Result.failure(Error.system_error(f"Unhandled event type: {event_type}"))

            # Save and return
            await uow.subscription_repo.save(subscription)
            await uow.commit() 
            
            return Result.success(UserSubscriptionResponse.from_entity(subscription))

    
    
    async def _handle_failed_payment(self, invoice: dict) -> Result:
        stripe_sub_id = invoice.get("parent", {}).get("subscription_details", {}).get("subscription") 
        customer_id = invoice.get("customer")
        user_id = invoice.get("parent").get("metadata", {}).get("user_id")
        
        print(f"Processing failed payment for stripe_sub_id: {stripe_sub_id},\ncustomer_id: {customer_id}")

        async with self.uow as uow:
            # 1. Try to find the user by Stripe's specific Sub ID
            subscription = await uow.subscription_repo.get_by_stripe_id(stripe_sub_id)
            
            # 2. Fallback: Find by Customer ID
            if (not subscription) and customer_id:
                print("No subscription found by stripe_sub_id, trying customer_id...")
                subscription = await uow.subscription_repo.get_by_customer_id(customer_id)
            
            # 3. Last Resort: Find by our internal User ID from metadata
            if (not subscription) and user_id:
                print("No subscription found by customer_id or stripe_sub_id, trying user_id from metadata...")
                subscription = await uow.subscription_repo.get_by_user_id(user_id)

            if not subscription:
                # If we still can't find them, something is wrong with the webhook/data integrity
                return Result.failure(Error.not_found("Subscription"))

            print(f"Handling failed payment for subscription: {subscription}")
            # LOGIC CHECK
            # If the local record has no stripe_subscription_id, it's Case A (First Time)
            if not subscription.stripe_subscription_id and not subscription.stripe_customer_id:
                subscription.downgrade_to_free()
                print(f"Downgraded subscription to FREE due to initial payment failure:\n{subscription}")
            else:
                # It's Case B (A renewal of an existing sub failed)
                subscription.handle_renewal_failure()
                print(f"Handled renewal failure for subscription:\n{subscription}")

            await uow.subscription_repo.save(subscription)
            await uow.commit()
        
        return Result.success({"message": "Payment failure handled"})
    
    # New Helper Method for Updates
    async def _handle_subscription_updated(self, stripe_sub_data: dict) -> Result:
        stripe_sub_id = stripe_sub_data.get("id")
        cancel_at_ts = stripe_sub_data.get("cancel_at", None)
        period_end_ts = stripe_sub_data.get("items").get("data")[0].get("current_period_end")
        print(f"""Handling subscription update for stripe_sub_id\n: 
              subs_id: {stripe_sub_id},\n 
              cancel_at: {cancel_at_ts},\n 
              period_end: {period_end_ts}\n
        """)
        
        async with self.uow as uow:
            subscription = await uow.subscription_repo.get_by_stripe_id(stripe_sub_id)
            print(f"Fetched subscription for update:\n{subscription}")
            
            if not subscription:
                return Result.failure(Error.not_found("Subscription"))
            
            # 1. Handle Cancellation via Portal
            if stripe_sub_data.get("status") == "active" and cancel_at_ts is not None:
                # User clicked cancel in portal
                subscription.cancel_at = datetime.fromtimestamp(cancel_at_ts, tz=UTC) if cancel_at_ts else datetime.now(tz=UTC)
                subscription.current_period_end = datetime.fromtimestamp(period_end_ts, tz=UTC)
                subscription.next_billing_date = None           
                print(f"\nUser scheduled cancellation. Updated subscription:\n{subscription}")
            # This helper function handles only for now the cancellation case in 
            # which the subscription ends at the end of the current billing period.
            # More logic can be added here for other update scenarios.

            elif stripe_sub_data.get("status") == "canceled":
                subscription.next_billing_date = None
                subscription.cancel_at = datetime.now(tz=UTC)
                subscription.downgrade_to_free()

            await self.uow.subscription_repo.save(subscription) 
            await uow.commit()        
            return Result.success(UserSubscriptionResponse.from_entity(subscription))
    
    
@dataclass
class GetManagementPortalUseCase:
    uow: UnitOfWork
    payment_port: PaymentPort

    async def execute(self, user_id: str) -> Result:
        try:
            async with self.uow:
                subscription = await self.uow.subscription_repo.get_by_user_id(user_id)
                
                if not subscription or not subscription.stripe_customer_id:
                    return Result.failure(Error.not_found("No payment record found."))

                # Generate the link
                portal_url = await self.payment_port.create_portal_session(
                    stripe_customer_id=subscription.stripe_customer_id                    
                )

            return Result.success({"message": {"portal_url": portal_url}})
        except Exception:
            logger.exception(f"GetManagementPortalUseCase failed for user {user_id}")
            return Result.failure(Error.system_error("An unexpected error occurred while accessing the management portal."))