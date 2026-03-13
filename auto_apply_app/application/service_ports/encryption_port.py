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