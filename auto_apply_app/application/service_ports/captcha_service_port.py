from abc import ABC, abstractmethod


class CaptchaServicePort(ABC):
    """Port for human-verification (CAPTCHA) services."""

    @abstractmethod
    async def verify(self, token: str, remote_ip: str | None = None) -> bool:
        """
        Returns True if the token is valid (= a real human),
        False otherwise (invalid, expired, network error, missing token).
        """
        pass