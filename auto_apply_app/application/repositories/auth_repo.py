# auto_apply_app/application/ports/auth_repository.py
from abc import ABC, abstractmethod
from typing import Optional
from uuid import UUID

from auto_apply_app.domain.entities.auth_user import AuthUser

class AuthRepository(ABC):
    """
    Interface for AuthUser persistence.
    Infrastructure layer will implement this (e.g., SqlAlchemyAuthRepository).
    """

    @abstractmethod
    async def save(self, auth_user: AuthUser) -> AuthUser:
        """Persist a new or updated AuthUser."""
        pass

    @abstractmethod
    async def get_by_email(self, email: str) -> Optional[AuthUser]:
        """Find user by email for login."""
        pass

    @abstractmethod
    async def get_by_id(self, user_id: UUID) -> Optional[AuthUser]:
        """Find user by ID (for token validation)."""
        pass