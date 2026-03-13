from abc import ABC, abstractmethod

class PasswordServicePort(ABC):
    """
    Defines the contract for password security.
    """

    @abstractmethod
    def get_password_hash(self, password: str) -> str:
        """Hashes a plain-text password."""
        pass

    @abstractmethod
    def verify(self, plain_password: str, hashed_password: str) -> bool:
        """Verifies a plain-text password against a hash."""
        pass