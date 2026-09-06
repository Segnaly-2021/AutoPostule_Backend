# auto_apply_app/application/service_ports/proxy_service_port.py
from abc import ABC, abstractmethod
from typing import Optional, TypedDict


class ProxyConfig(TypedDict):
    """
    Playwright-compatible proxy configuration.
    Passed directly to browser.new_context(proxy=...).
    """
    server: str
    username: str
    password: str


class ProxyServicePort(ABC):
    """
    Interface for resolving a proxy configuration for a given run.
    The Infrastructure layer must implement this (e.g. TwoCaptchaProxyAdapter).

    The session_key derives a stable session identifier so a single run
    (including a human-review resume of the same search) keeps the same
    sticky exit IP, while a new run rotates to a fresh IP.
    """

    @abstractmethod
    def get_proxy_for_run(self, user_id: str, session_key: str) -> Optional[ProxyConfig]:
        """
        Returns a Playwright-compatible proxy config for this run,
        or None if no proxy should be used (e.g. local development).

        Args:
            user_id: The user's UUID as a string (used for logging).
            session_key: A per-run identifier (e.g. the JobSearch id as a
                string). Same key -> same sticky exit IP; different key ->
                a different exit IP.

        Returns:
            A ProxyConfig dict ready to be passed to Playwright,
            or None if no proxy is configured.
        """
        pass