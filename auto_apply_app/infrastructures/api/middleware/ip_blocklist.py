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