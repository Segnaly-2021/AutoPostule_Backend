# ==============================================================================
# FILE: auto_apply_app/application/service_ports/captcha_service_port.py
#
# WHAT IT DOES:
#   Interface for checking a human-verification token from the frontend.
#
# USED BY:
#   - auto_apply_app/infrastructures/captcha/turnstile_adapter.py: implementation
#   - auto_apply_app/infrastructures/configuration/container.py: wires the adapter
#
# EXTERNAL DEPENDENCIES:
#   - Cloudflare Turnstile: captcha service behind the adapter (no import here)
# ==============================================================================
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