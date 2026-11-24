# ==============================================================================
# FILE: auto_apply_app/application/service_ports/token_provider_port.py
#
# WHAT IT DOES:
#   Interface for creating and reading signed tokens: login sessions, password
#   reset links and unsubscribe links. Also exposes token id and remaining life.
#
# USED BY:
#   - auto_apply_app/application/use_cases/user_use_cases.py: issues and reads tokens
#   - auto_apply_app/infrastructures/authentication/token_provider.py: implementation
#   - auto_apply_app/infrastructures/configuration/container.py: wires the provider
#
# EXTERNAL DEPENDENCIES:
#   - PyJWT: JWT library used by the implementation (no import here)
# ==============================================================================
from abc import ABC, abstractmethod
from typing import Dict, Optional
from uuid import UUID
from datetime import timedelta

class TokenProviderPort(ABC):
    """
    Defines the contract for creating and parsing access tokens (e.g., JWT).
    """
    
    @abstractmethod
    def encode_token(self, user_id: UUID, claims: Optional[Dict] = None, expires_delta: Optional[timedelta] = None) -> str:
        """Generates a secure token string for a given user."""
        pass

    @abstractmethod
    def decode_token(self, token: str) -> Dict:
        """Parses a token string back into data."""
        pass    

    @abstractmethod
    def get_token_id(self, token: str) -> str:
        """Extracts the JTI (unique ID) from a token string."""
        pass

    @abstractmethod
    def get_token_ttl(self, token: str) -> int:
        """Returns remaining time-to-live in seconds."""
        pass