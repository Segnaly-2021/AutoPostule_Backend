# ==============================================================================
# FILE: auto_apply_app/application/service_ports/password_service_port.py
#
# WHAT IT DOES:
#   Interface for one-way hashing and verification. Used for passwords and also
#   for the 6-digit email verification codes.
#
# USED BY:
#   - auto_apply_app/application/use_cases/user_use_cases.py: hashes and verifies
#   - auto_apply_app/infrastructures/authentication/password_service.py: implementation
#   - auto_apply_app/infrastructures/configuration/container.py: wires the service
#
# EXTERNAL DEPENDENCIES:
#   - passlib: hashing library used by the implementation (no import here)
# ==============================================================================
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