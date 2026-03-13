from abc import ABC, abstractmethod
from typing import Dict, Optional
from uuid import UUID



class TokenProviderPort(ABC):
    """
    Defines the contract for creating and parsing access tokens (e.g., JWT).
    The Application layer knows *that* it needs a token, but not *how* (JWT, OAuth, etc).
    """
    
    @abstractmethod
    def encode_token(self, user_id: UUID, claims: Optional[Dict] = None) -> str:
        """Generates a secure token string for a given user."""
        pass

    @abstractmethod
    def decode_token(self, token: str) -> Dict:
        """
        Parses a token string back into data.
        Should raise a domain-specific error (e.g., InvalidTokenError) if it fails.
        """
        pass    

    
    @abstractmethod
    def get_token_id(self, token: str) -> str:
        """Extracts the JTI (unique ID) from a token string."""
        pass

    @abstractmethod
    def get_token_ttl(self, token: str) -> int:
        """Returns remaining time-to-live in seconds."""
        pass