import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Single shared limiter instance used by BOTH the app (app.state.limiter +
# exception handler) and the route decorators. Keeping one instance ensures the
# decorators, header injection and the RateLimitExceeded handler all operate on
# the same storage.
#
# storage_uri defaults to in-memory when RATE_LIMIT_STORAGE_URI is unset. In
# production set it to a Redis URI (e.g. redis://host:6379) so limits are shared
# across Cloud Run instances/workers instead of being per-process.
_storage_uri = os.getenv("RATE_LIMIT_STORAGE_URI")

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_storage_uri if _storage_uri else "memory://",
)
