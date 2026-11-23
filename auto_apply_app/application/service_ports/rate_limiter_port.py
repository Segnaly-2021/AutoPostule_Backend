# ==============================================================================
# FILE: auto_apply_app/application/service_ports/rate_limiter_port.py
#
# WHAT IT DOES:
#   Interface for a simple cooldown: one action per key per time window. Used to
#   limit verification-code resends and email-change requests.
#
# USED BY:
#   - auto_apply_app/application/use_cases/user_use_cases.py: resend and email-change cooldowns
#   - auto_apply_app/infrastructures/configuration/container.py: wires the limiter
#   - auto_apply_app/infrastructures/redis_rate_limit/redis_rate_limiter.py: implementation
#
# EXTERNAL DEPENDENCIES:
#   - Redis: stores the cooldown keys in the implementation (no import here)
# ==============================================================================
from abc import ABC, abstractmethod


class RateLimiterPort(ABC):
    """
    Generic single-shot rate limiter.

    Implementations should atomically:
      1. Check whether `key` has a live record.
      2. If yes, return (False, seconds_remaining).
      3. If no, set the record with TTL = `window_seconds` and return (True, 0).

    This is the "fixed window of 1 request" pattern — enough for resend cooldowns,
    not enough for sliding-window or token-bucket use cases.
    """

    @abstractmethod
    async def try_acquire(self, key: str, window_seconds: int) -> tuple[bool, int]:
        """
        Returns (allowed, retry_after_seconds).
        - allowed=True  -> caller may proceed; retry_after is 0.
        - allowed=False -> caller must wait; retry_after is the seconds left on the window.
        """
        ...