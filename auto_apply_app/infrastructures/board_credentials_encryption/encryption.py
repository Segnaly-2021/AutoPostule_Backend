# ==============================================================================
# FILE: auto_apply_app/infrastructures/board_credentials_encryption/encryption.py
#
# WHAT IT DOES:
#   Encrypts users' job-board logins and passwords before they are stored, and
#   decrypts them when a worker must sign in. Unlike password hashing, this is
#   reversible because the agent needs the original values. Losing ENCRYPTION_KEY
#   makes stored credentials unreadable; leaking it exposes all of them.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/app.py: injected into the web container
#   - auto_apply_app/infrastructures/configuration/container.py: built for worker jobs
#
# EXTERNAL DEPENDENCIES:
#   - cryptography (Fernet): authenticated symmetric encryption
# ==============================================================================
# AutoApply\auto_apply_app\infrastructures\board_crendentilas_encryption\encryption.py
import os
from cryptography.fernet import Fernet, InvalidToken
from auto_apply_app.application.service_ports.encryption_port import EncryptionServicePort

class EncryptionService(EncryptionServicePort):
    """
    Concrete implementation using Fernet (symmetric encryption).
    
    Fernet uses:
    - AES-128 in CBC mode
    - HMAC for authentication
    - Automatic key derivation
    
    This is safe for storing credentials that need to be decrypted later.
    """
    
    def __init__(self, encryption_key: str = None):
        """
        Initialize with encryption key from environment or generate one.
        
        IMPORTANT: The encryption key must be:
        1. Stored securely (e.g., in environment variables or secrets manager)
        2. The SAME across application restarts (or you can't decrypt old data)
        3. 32 url-safe base64-encoded bytes
        """
        if encryption_key:
            self._key = encryption_key.encode()
        else:
            # Try to get from environment
            env_key = os.getenv("ENCRYPTION_KEY")
            if env_key:
                self._key = env_key.encode()
            else:
                # Generate a new key (WARNING: This should only happen in dev/testing)
                # In production, you MUST set ENCRYPTION_KEY in your environment
                print("⚠️  WARNING: Generating new encryption key. Set ENCRYPTION_KEY in .env for production!")
                # NOTE: a random key lasts only for this process, so
                # anything encrypted with it cannot be decrypted after a restart. The
                # current wiring avoids this path because Config raises on a missing key.
                self._key = Fernet.generate_key()
        
        try:
            self._fernet = Fernet(self._key)
        except Exception as e:
            raise ValueError(f"Invalid encryption key: {e}")
    
    async def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext and return base64-encoded ciphertext.
        """
        if not plaintext:
            return ""
        
        try:
            # Encode to bytes
            plaintext_bytes = plaintext.encode('utf-8')
            
            # Encrypt
            encrypted_bytes = self._fernet.encrypt(plaintext_bytes)
            
            # Return as base64 string for storage
            return encrypted_bytes.decode('utf-8')
        
        except Exception as e:
            raise ValueError(f"Encryption failed: {e}")
    
    async def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt base64-encoded ciphertext back to plaintext.
        """
        if not ciphertext:
            return ""
        
        try:
            # String to Bytes
            encrypted_bytes = ciphertext.encode('utf-8')
            
            # Decrypt
            decrypted_bytes = self._fernet.decrypt(encrypted_bytes)
            
            # Return as string
            return decrypted_bytes.decode('utf-8')
        
        # Fernet checks an HMAC before decrypting, so tampered data or a wrong key
        # raises InvalidToken instead of returning garbage.
        except InvalidToken:
            raise ValueError("Decryption failed: Invalid token or wrong encryption key")
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}")
    
    @staticmethod
    def generate_key() -> str:
        """
        Generate a new Fernet key for initial setup.
        Save this to your .env file as ENCRYPTION_KEY.
        """
        return Fernet.generate_key().decode('utf-8')