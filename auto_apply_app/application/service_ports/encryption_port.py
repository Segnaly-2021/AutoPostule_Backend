# ==============================================================================
# FILE: auto_apply_app/application/service_ports/encryption_port.py
#
# WHAT IT DOES:
#   Interface for reversible encryption of job board credentials. Preferences
#   encrypt them on save; workers decrypt them just before logging in to a board.
#
# USED BY:
#   - auto_apply_app/application/use_cases/preferences_use_cases.py: encrypts on save
#   - auto_apply_app/infrastructures/board_credentials_encryption/encryption.py: implementation
#   - auto_apply_app/infrastructures/agent/create_agent.py: passes it to workers
#   - auto_apply_app/infrastructures/agent/workers/apec/apec_worker.py: decrypts login
#   - auto_apply_app/infrastructures/agent/workers/hellowork/hw_worker.py: decrypts login
#   - auto_apply_app/infrastructures/agent/workers/wttj/wttj_worker.py: decrypts login
#   - auto_apply_app/infrastructures/agent/workers/teaser/teaser_worker.py: decrypts login
#   - auto_apply_app/infrastructures/configuration/container.py: wires the service
#   - ... and 1 more
#
# EXTERNAL DEPENDENCIES:
#   - cryptography (Fernet): used by the implementation (no import here)
# ==============================================================================
from abc import ABC, abstractmethod

class EncryptionServicePort(ABC):
    """
    Defines the contract for symmetric encryption/decryption.
    Used for storing sensitive data that must be retrievable (like job board credentials).
    
    Unlike password hashing (which is one-way), encryption is two-way:
    - encrypt: plaintext → ciphertext
    - decrypt: ciphertext → plaintext
    """

    @abstractmethod
    async def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext data.
        Returns base64-encoded ciphertext.
        """
        pass

    @abstractmethod
    async def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt ciphertext back to plaintext.
        Raises ValueError if decryption fails.
        """
        pass