# auto_apply_app/domain/entities/board_credentials.py

from dataclasses import dataclass
from datetime import datetime, timezone
from auto_apply_app.domain.entities.entity import Entity


from uuid import UUID
from typing import Optional

@dataclass
class BoardCredential(Entity):
    """
    Represents encrypted credentials for a specific job board.
    NEVER store plaintext passwords in this entity.
    
    Security Notes:
    - login_encrypted and password_encrypted should be encrypted by infrastructure layer
    - This entity should never contain plaintext passwords
    - Decryption should only happen at the point of use (in workers)
    """
    user_id: UUID
    job_board: str  # 'hellowork', 'wttj', 'apec'
    
    
    # Encrypted fields (handled by infrastructure layer)
    login_encrypted: Optional[str] = None
    password_encrypted: Optional[str] = None
    
    # Metadata
    is_verified: bool = False  # Did we successfully log in with these credentials?
    last_verified_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    def __post_init__(self):
        """Initialize timestamps and ID"""
        
        now = datetime.now(timezone.utc)
        if self.created_at is None:
            self.created_at = now
        if self.updated_at is None:
            self.updated_at = now
    
    def mark_as_verified(self) -> None:
        """Mark credentials as successfully verified"""
        self.is_verified = True
        self.last_verified_at = datetime.now(timezone.utc)
    
    def mark_as_invalid(self) -> None:
        """Mark credentials as invalid (failed login)"""
        self.is_verified = False
    
    def update_credentials(self, login_encrypted: str, password_encrypted: str) -> None:
        """Update encrypted credentials"""
        self.login_encrypted = login_encrypted
        self.password_encrypted = password_encrypted
        self.updated_at = datetime.now(timezone.utc)
        # Reset verification status when credentials change
        self.is_verified = False
        self.last_verified_at = None