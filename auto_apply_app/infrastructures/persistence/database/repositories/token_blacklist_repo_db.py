from redis.asyncio import Redis
from auto_apply_app.application.repositories.token_blacklist import TokenBlacklistRepository

class RedisTokenBlacklistRepository(TokenBlacklistRepository):
    def __init__(self, redis_client: Redis):
        self.client = redis_client

    async def blacklist_token(self, token_id: str, ttl_seconds: int) -> None:
        # 'ex' is the expiration time in seconds
        await self.client.set(name=f"blacklist:{token_id}", value="1", ex=ttl_seconds)

    async def is_blacklisted(self, token_id: str) -> bool:
        exists = await self.client.exists(f"blacklist:{token_id}")
        return exists > 0