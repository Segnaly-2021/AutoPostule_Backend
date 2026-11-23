# ==============================================================================
# FILE: auto_apply_app/infrastructures/redis_rate_limit/redis_rate_limiter.py
#
# WHAT IT DOES:
#   Business-level cooldown lock, such as "one verification email per window". The
#   first caller for a key succeeds; later callers are refused until the key expires.
#   This is separate from slowapi's per-IP HTTP limits in api/rate_limit.py.
#
# USED BY:
#   - auto_apply_app/infrastructures/configuration/container.py: _build_rate_limiter()
#
# EXTERNAL DEPENDENCIES:
#   - redis: atomic SET NX EX and TTL commands
# ==============================================================================
from redis.asyncio import Redis

from auto_apply_app.application.service_ports.rate_limiter_port import RateLimiterPort


class RedisRateLimiter(RateLimiterPort):
    """
    Atomic single-shot rate limiter using SET NX EX.
    Returns (False, ttl_remaining) if the key is already set.
    """

    def __init__(self, redis_client: Redis):
        self.client = redis_client

    async def try_acquire(self, key: str, window_seconds: int) -> tuple[bool, int]:
        # SET NX EX is atomic: either we set the key (allowed), or it already exists (denied).
        # `nx=True` -> only set if not exists. `ex=window_seconds` -> TTL in seconds.
        was_set = await self.client.set(name=key, value="1", ex=window_seconds, nx=True)
        if was_set:
            return True, 0

        # Already locked — return remaining TTL so the client can show "retry in N seconds".
        ttl = await self.client.ttl(key)
        # ttl can be -2 (no key) or -1 (no expiry) in edge cases; clamp to a safe minimum.
        if ttl < 0:
            ttl = window_seconds
        return False, ttl