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