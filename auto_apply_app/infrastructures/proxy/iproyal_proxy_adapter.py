# auto_apply_app/infrastructures/proxy/iproyal_proxy_adapter.py
import os
import hashlib
import logging
from typing import Optional

from auto_apply_app.application.service_ports.proxy_service_port import (
    ProxyServicePort,
    ProxyConfig,
)

logger = logging.getLogger(__name__)


class IPRoyalProxyAdapter(ProxyServicePort):
    """
    Concrete adapter for IPRoyal residential proxies.
    Each user gets a deterministic sticky session derived from their user_id,
    so the same user always lands on the same exit IP across runs.
    """

    def __init__(self):
        self.host = os.getenv("IPROYAL_HOST")
        self.port = os.getenv("IPROYAL_PORT")
        self.username = os.getenv("IPROYAL_USERNAME")
        self.password_template = os.getenv("IPROYAL_PASSWORD_TEMPLATE")

        if not all([self.host, self.port, self.username, self.password_template]):
            raise RuntimeError(
                "IPRoyalProxyAdapter requires IPROYAL_HOST, IPROYAL_PORT, "
                "IPROYAL_USERNAME, and IPROYAL_PASSWORD_TEMPLATE env vars."
            )

        if "{session_id}" not in self.password_template:
            raise RuntimeError(
                "IPROYAL_PASSWORD_TEMPLATE must contain '{session_id}' placeholder. "
                "Example: 'yourpass_country-fr_session-{session_id}_lifetime-59m'"
            )

        logger.info("✅ IPRoyalProxyAdapter initialized")

    def _derive_session_id(self, user_id: str) -> str:
        """
        Derives a stable, alphanumeric session_id from the user_id.
        Same user → same session_id → same sticky exit IP across runs.
        """
        digest = hashlib.md5(user_id.encode()).hexdigest()
        return digest[:10]

    def get_proxy_for_user(self, user_id: str) -> Optional[ProxyConfig]:
        session_id = self._derive_session_id(user_id)
        password = self.password_template.replace("{session_id}", session_id)

        return ProxyConfig(
            server=f"http://{self.host}:{self.port}",
            username=self.username,
            password=password,
        )