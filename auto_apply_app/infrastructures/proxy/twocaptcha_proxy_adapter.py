# ==============================================================================
# FILE: auto_apply_app/infrastructures/proxy/twocaptcha_proxy_adapter.py
#
# WHAT IT DOES:
#   Builds the residential proxy settings for a browser run. A hash of the session key
#   becomes a "sticky session" id, so one run (and its resume) keeps the same exit IP.
#   Selected with PROXY_PROVIDER=twocaptcha; connection details come from env vars.
#
# USED BY:
#   - auto_apply_app/infrastructures/configuration/container.py: _resolve_proxy_service()
#   - tests/test_session_binding.py: tests sticky session behavior
#
# EXTERNAL DEPENDENCIES:
#   - 2Captcha residential proxies: the proxy service (TWOCAPTCHA_* env vars)
#   - Playwright / Chromium: consume the returned ProxyConfig in the workers
# ==============================================================================
# auto_apply_app/infrastructures/proxy/twocaptcha_proxy_adapter.py
import os
import hashlib
import logging
from typing import Optional

from auto_apply_app.application.service_ports.proxy_service_port import (
    ProxyServicePort,
    ProxyConfig,
)

logger = logging.getLogger(__name__)


class TwoCaptchaProxyAdapter(ProxyServicePort):
    """
    Concrete adapter for 2Captcha residential proxies.

    Each run gets a deterministic sticky session derived from its session_key
    (the JobSearch id), so a single run — including a human-review resume of the
    same search — keeps the same exit IP, while a brand-new run rotates to a
    fresh IP.

    The exact 2Captcha connection syntax (how the session token, country and
    lifetime are encoded) is kept out of the code: it lives in the
    USERNAME/PASSWORD templates. Embed the '{session_id}' placeholder in
    whichever field 2Captcha uses to carry the session token — the adapter
    substitutes it in both, so either layout works.
    """

    def __init__(self):
        self.host = os.getenv("TWOCAPTCHA_HOST")
        self.port = os.getenv("TWOCAPTCHA_PORT")
        self.username_template = os.getenv("TWOCAPTCHA_USERNAME_TEMPLATE")
        self.password_template = os.getenv("TWOCAPTCHA_PASSWORD_TEMPLATE")

        if not all(
            [self.host, self.port, self.username_template, self.password_template]
        ):
            raise RuntimeError(
                "TwoCaptchaProxyAdapter requires TWOCAPTCHA_HOST, TWOCAPTCHA_PORT, "
                "TWOCAPTCHA_USERNAME_TEMPLATE, and TWOCAPTCHA_PASSWORD_TEMPLATE env vars."
            )

        if "{session_id}" not in self.username_template + self.password_template:
            raise RuntimeError(
                "At least one of TWOCAPTCHA_USERNAME_TEMPLATE / "
                "TWOCAPTCHA_PASSWORD_TEMPLATE must contain the '{session_id}' "
                "placeholder. Example username: "
                "'user-yourid_country-fr_session-{session_id}_lifetime-59m'"
            )

        logger.info("✅ TwoCaptchaProxyAdapter initialized")

    def _derive_session_id(self, session_key: str) -> str:
        """
        Derives a stable, alphanumeric session_id from the session_key.
        Same key -> same session_id -> same sticky exit IP for the whole run.
        """
        # MD5 is fine here: it is only a stable short id, not a security measure.
        digest = hashlib.md5(session_key.encode()).hexdigest()
        return digest[:10]

    def get_proxy_for_run(self, user_id: str, session_key: str) -> Optional[ProxyConfig]:
        session_id = self._derive_session_id(session_key)
        username = self.username_template.replace("{session_id}", session_id)
        password = self.password_template.replace("{session_id}", session_id)

        return ProxyConfig(
            server=f"http://{self.host}:{self.port}",
            username=username,
            password=password,
        )
