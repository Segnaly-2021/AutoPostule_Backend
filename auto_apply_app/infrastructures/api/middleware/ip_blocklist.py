# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/middleware/ip_blocklist.py
#
# WHAT IT DOES:
#   HTTP middleware that answers 403 to a small hard-coded set of banned IP
#   addresses before the request reaches any route.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/app.py: installed on the FastAPI app
#
# EXTERNAL DEPENDENCIES:
#   - fastapi: request and JSON response types
#   - Cloud Run: its front end supplies the X-Forwarded-For header
# ==============================================================================
import logging
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

BLOCKED_IPS = {
    "80.239.186.176",
    "80.239.186.177",
    "80.239.186.178",
}


async def ip_blocklist_middleware(request: Request, call_next):
    # Cloud Run sits behind a load balancer; the real IP is in this header
    # NOTE: possible issue: the FIRST X-Forwarded-For entry is supplied by the client
    # and proxies append to the list, so a banned IP can bypass this by sending a fake
    # value. The trustworthy entry is the one added by Cloud Run's own front end.
    forwarded_for = request.headers.get("x-forwarded-for", "")
    client_ip = forwarded_for.split(",")[0].strip() if forwarded_for else (
        request.client.host if request.client else ""
    )

    if client_ip in BLOCKED_IPS:
        logger.warning("Blocked request from banned IP: %s", client_ip)
        return JSONResponse(
            status_code=403,
            content={"detail": "Forbidden"},
        )

    return await call_next(request)