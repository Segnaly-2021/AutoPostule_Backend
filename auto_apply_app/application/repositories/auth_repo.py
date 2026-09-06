# auto_apply_app/application/ports/auth_repository.py
from abc import ABC, abstractmethod
from datetime import datetime
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

    @abstractmethod
    async def list_created_between(
        self, start: datetime, end: datetime, exclude_opted_out: bool = False
    ) -> list:
        """Accounts created in [start, end). The list sibling of the count below.

        `exclude_opted_out` drops anyone who unsubscribed -- the marketing
        reminder must filter, the transactional check-in must not. Making it an
        argument rather than two methods keeps the consent decision at the call
        site, where it is visible.
        """
        pass

    @abstractmethod
    async def count_created_between(self, start: datetime, end: datetime) -> int:
        """
        Count accounts created in [start, end).

        auth_users.created_at is the only real signup timestamp in the schema — the
        users table has no created_at — so all signup metrics are derived from here.
        """
        pass

    @abstractmethod
    async def count_verified(self) -> int:
        """Count accounts that completed email verification."""
        pass