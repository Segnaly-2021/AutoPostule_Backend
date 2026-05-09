import os
import logging
import httpx

from auto_apply_app.application.service_ports.captcha_service_port import CaptchaServicePort

logger = logging.getLogger(__name__)


class TurnstileCaptchaAdapter(CaptchaServicePort):
    """
    Cloudflare Turnstile adapter.
    Strict mode: any failure (missing token, network error, invalid response) → False.
    """

    VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"

    def __init__(self):
        self.secret_key = os.getenv("TURNSTILE_SECRET_KEY")
        if not self.secret_key:
            raise ValueError("TURNSTILE_SECRET_KEY environment variable is not set")

    async def verify(self, token: str, remote_ip: str = None) -> bool:
        if not token:
            logger.warning("Turnstile: empty token")
            return False
        if not self.secret_key:
            logger.error("Turnstile: SECRET_KEY missing on backend!")
            return False

        data = {"secret": self.secret_key, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(self.VERIFY_URL, data=data)
                result = resp.json()
                if not result.get("success"):
                    logger.warning(
                        "Turnstile rejected token. error-codes=%s",
                        result.get("error-codes"),
                    )
                return result.get("success", False)
        except Exception:
            logger.exception("Turnstile verify network error")
            return False