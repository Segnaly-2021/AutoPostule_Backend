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

    async def verify(self, token: str, remote_ip: str | None = None) -> bool:
        if not token:
            return False

        payload = {"secret": self.secret_key, "response": token}
        if remote_ip:
            payload["remoteip"] = remote_ip

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(self.VERIFY_URL, data=payload, timeout=5.0)

            if response.status_code != 200:
                logger.warning("Turnstile verify returned %s", response.status_code)
                return False

            data = response.json()
            success = bool(data.get("success", False))
            if not success:
                # Cloudflare error codes: invalid-input-response, timeout-or-duplicate, etc.
                logger.info(
                    "Turnstile verify rejected: %s",
                    data.get("error-codes", []),
                )
            return success

        except httpx.RequestError:
            logger.exception("Turnstile verify network error")
            return False
        except Exception:
            logger.exception("Turnstile verify unexpected error")
            return False