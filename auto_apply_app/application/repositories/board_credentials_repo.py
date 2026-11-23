# ==============================================================================
# FILE: auto_apply_app/application/repositories/board_credentials_repo.py
#
# WHAT IT DOES:
#   Interface for each user's encrypted job board logins, one per board.
#   It stores ciphertext only; encryption happens through EncryptionServicePort.
#
# USED BY:
#   - auto_apply_app/application/repositories/unit_of_work.py: exposes it as board_cred_repo
#   - auto_apply_app/infrastructures/persistence/database/repositories/board_credentials_repo_db.py: implementation
#   - auto_apply_app/infrastructures/persistence/in_memory/memory.py: in-memory implementation
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
# AutoApply\auto_apply_app\application\repositories\board_credentials_repo.py
from abc import ABC, abstractmethod
from uuid import UUID
from typing import Optional, List
from auto_apply_app.domain.entities.board_credentials import BoardCredential

class BoardCredentialsRepository(ABC):
    """
    Repository for managing encrypted job board credentials.
    """
    
    @abstractmethod
    async def get_by_user_and_board(
        self, 
        user_id: UUID, 
        board_name: str
    ) -> Optional[BoardCredential]:
        """Get credentials for a specific user and board"""
        pass
    
    @abstractmethod
    async def get_all_by_user(self, user_id: UUID) -> List[BoardCredential]:
        """Get all credentials for a user (all boards)"""
        pass
    
    @abstractmethod
    async def save(self, credential: BoardCredential) -> None:
        """Save or update board credentials"""
        pass
    
    @abstractmethod
    async def delete(self, user_id: UUID, board_name: str) -> None:
        """Delete credentials for a specific board"""
        pass
    
    @abstractmethod
    async def delete_all_by_user(self, user_id: UUID) -> None:
        """Delete all credentials for a user"""
        pass