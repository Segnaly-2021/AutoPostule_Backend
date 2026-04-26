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
    Interface for resolving a proxy configuration for a given user.
    The Infrastructure layer must implement this (e.g. IPRoyalProxyAdapter).
    
    The user_id is used to derive a stable session identifier so the same
    user always gets the same sticky exit IP across runs.
    """
    
    @abstractmethod
    def get_proxy_for_user(self, user_id: str) -> Optional[ProxyConfig]:
        """
        Returns a Playwright-compatible proxy config for this user,
        or None if no proxy should be used (e.g. local development).
        
        Args:
            user_id: The user's UUID as a string.
            
        Returns:
            A ProxyConfig dict ready to be passed to Playwright,
            or None if no proxy is configured.
        """
        pass