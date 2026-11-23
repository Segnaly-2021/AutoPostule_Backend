# ==============================================================================
# FILE: auto_apply_app/application/repositories/token_blacklist.py
#
# WHAT IT DOES:
#   Interface for revoked JWT ids. JWTs cannot be un-issued, so logout records the
#   token id here until the token would have expired anyway. Not part of the UoW.
#
# USED BY:
#   - auto_apply_app/application/use_cases/user_use_cases.py: LogoutUseCase writes to it
#   - auto_apply_app/infrastructures/configuration/container.py: wires the implementation
#   - auto_apply_app/infrastructures/persistence/database/repositories/token_blacklist_repo_db.py: implementation
#   - auto_apply_app/infrastructures/persistence/in_memory/memory.py: in-memory implementation
#   - auto_apply_app/infrastructures/repository_factory.py: selects the implementation
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
from abc import ABC, abstractmethod

class TokenBlacklistRepository(ABC):
    
    @abstractmethod
    async def blacklist_token(self, token_id: str, ttl_seconds: int) -> None:
        """
        Adds a token ID to the blacklist.
        :param token_id: The unique JTI of the token.
        :param ttl_seconds: How long (in seconds) to keep it blacklisted.
        """
        pass

    @abstractmethod
    async def is_blacklisted(self, token_id: str) -> bool:
        """
        Checks if a token ID is currently blacklisted.
        """
        pass