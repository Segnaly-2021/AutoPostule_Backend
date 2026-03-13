"""
This module defines the repository interface for User entity persistence.
"""

from abc import ABC, abstractmethod
from uuid import UUID

from auto_apply_app.domain.entities.user import User


class UserRepository(ABC):
    """Repository interface for User entity persistence."""

    @abstractmethod
    async def get(self, user_id: UUID) -> User:
        """
        Retrieve a user by its ID.

        Args:
            user_id: The unique identifier of the user

        Returns:
            The requested User entity

        Raises:
            UserNotFoundError: If no user exists with the given ID
        """
        pass
    async def get_by_email(self, email: str) -> User:
        """
        Retrieve a user by their email address.

        Args:
            email: The email address of the user

        Returns:
            The requested User entity

        Raises:
            UserNotFoundError: If no user exists with the given email
        """
        pass

    @abstractmethod
    async def get_all(self) -> list[User]:
        """
        Retrieve all users.
        """
        pass

    @abstractmethod
    async def save(self, user: User) -> None:
        """
        Save a user to the repository.

        Args:
            user: The User entity to save
        """
        pass

    @abstractmethod
    async def delete(self, user_id: UUID) -> None:
        """
        Delete a user from the repository.

        Args:
            user_id: The unique identifier of the user to delete
        """
        pass

    @abstractmethod
    async def update(self, user_id: UUID, data: dict) -> None:
        """
        Update a user in the repository.

        Args:
            user_id: The unique identifier of the user to update
            user: The User entity to update
        """
        pass