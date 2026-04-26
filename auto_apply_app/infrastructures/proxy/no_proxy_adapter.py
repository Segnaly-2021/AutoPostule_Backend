# auto_apply_app/infrastructures/proxy/no_proxy_adapter.py
import logging
from typing import Optional

from auto_apply_app.application.service_ports.proxy_service_port import (
    ProxyServicePort,
    ProxyConfig,
)

logger = logging.getLogger(__name__)


class NoProxyAdapter(ProxyServicePort):
    """
    No-op adapter for local development and testing.
    Returns None for every user — Playwright runs without a proxy.
    """

    def __init__(self):
        logger.info("⚠️  NoProxyAdapter initialized — agent will run WITHOUT proxies")

    def get_proxy_for_user(self, user_id: str) -> Optional[ProxyConfig]:
        return None