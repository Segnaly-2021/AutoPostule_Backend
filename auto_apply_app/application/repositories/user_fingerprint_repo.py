# auto_apply_app/application/repositories/user_fingerprint_repo.py
from abc import ABC, abstractmethod
from uuid import UUID
from typing import Optional


from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint

class UserFingerprintRepository(ABC):
    """
    Repository for managing user browser fingerprints.
    """
    
    @abstractmethod
    async def get_by_user_id(self, user_id: UUID) -> Optional[UserFingerprint]:
        """Retrieve the fingerprint for a specific user"""
        pass
    
    @abstractmethod
    async def save(self, fingerprint: UserFingerprint) -> None:
        """Save or update a user fingerprint"""
        pass
    
    @abstractmethod
    async def delete(self, user_id: UUID) -> None:
        """Delete a user fingerprint (e.g., when user is deleted)"""
        pass