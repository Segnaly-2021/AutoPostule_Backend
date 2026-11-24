# ==============================================================================
# FILE: auto_apply_app/application/service_ports/email_service_port.py
#
# WHAT IT DOES:
#   Interface for all outgoing email: password reset, verification codes,
#   email-change notices and scheduled lifecycle messages.
#   Callers and the Resend adapter name the verification argument `code`, not
#   `verification_token` as declared here.
#
# USED BY:
#   - auto_apply_app/application/use_cases/user_use_cases.py: sends account emails
#   - auto_apply_app/infrastructures/configuration/container.py: wires the adapter
#   - auto_apply_app/infrastructures/emailing_service/resend_email_service.py: implementation
#
# EXTERNAL DEPENDENCIES:
#   - Resend: email delivery service behind the adapter (no import here)
# ==============================================================================
from abc import ABC, abstractmethod

class EmailServicePort(ABC):
    """Port for sending transactional emails."""
    
    @abstractmethod
    async def send_password_reset_email(self, to_email: str, reset_token: str) -> None:
        pass

    @abstractmethod
    async def send_verification_email(self, to_email: str, verification_token: str) -> None:
        """Sends an email containing the email-verification link."""
        pass

    @abstractmethod
    async def send_email_changed_notification(self, to_email: str, new_email: str) -> None:
        """Notifies the OLD address that the account email was changed (security notice)."""
        pass

    # --- Lifecycle mail -------------------------------------------------
    #
    # These return bool, unlike the transactional methods above. A scheduled
    # job records a 'sent' row so nobody is emailed twice, and that row must
    # only be written on an accepted send -- otherwise a transient failure
    # suppresses the message permanently.

    @abstractmethod
    async def send_new_customer_checkin(self, to_email: str, first_name: str) -> bool:
        """Three days after a plan is purchased. Asks for a reply."""
        pass

    @abstractmethod
    async def send_free_account_reminder(
        self, to_email: str, first_name: str, unsubscribe_url: str
    ) -> bool:
        """Three days after signup, to a free account. Marketing: the
        unsubscribe_url is required, not decorative."""
        pass
