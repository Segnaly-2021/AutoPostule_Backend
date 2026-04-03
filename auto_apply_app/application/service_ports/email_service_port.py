from abc import ABC, abstractmethod

class EmailServicePort(ABC):
    """Port for sending transactional emails."""
    
    @abstractmethod
    async def send_password_reset_email(self, to_email: str, reset_token: str) -> None:
        """Sends an email containing the password reset link."""
        pass