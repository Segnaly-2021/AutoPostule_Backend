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