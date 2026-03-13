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