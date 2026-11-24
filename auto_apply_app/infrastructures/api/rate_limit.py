# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/rate_limit.py
#
# WHAT IT DOES:
#   Creates the single slowapi Limiter used by route decorators such as
#   @limiter.limit("5/hour"). Limits are counted per client IP address and stored
#   in memory or in Redis, depending on RATE_LIMIT_STORAGE_URI.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/app.py: registers it on app.state
#   - auto_apply_app/infrastructures/api/routers/user.py: login, register and similar limits
#   - auto_apply_app/infrastructures/api/routers/admin.py: admin route limits
#   - auto_apply_app/infrastructures/api/routers/analytics.py: page-view limit
#   - auto_apply_app/infrastructures/api/routers/announcement.py: announcement limits
#   - auto_apply_app/infrastructures/api/routers/free_search.py: per-IP backstop
#   - auto_apply_app/infrastructures/api/routers/messaging.py: unsubscribe limits
#
# EXTERNAL DEPENDENCIES:
#   - slowapi: rate-limiting library
#   - Redis: optional shared counter storage
# ==============================================================================
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

# NOTE: possible issue: get_remote_address uses the connection's peer IP. The Dockerfiles
# start uvicorn without --forwarded-allow-ips, so behind Cloud Run this may be the proxy's
# address, which would make all users share one bucket. Check this in production.
limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=_storage_uri if _storage_uri else "memory://",
)
