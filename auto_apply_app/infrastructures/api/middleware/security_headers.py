# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/middleware/security_headers.py
#
# WHAT IT DOES:
#   HTTP middleware that adds standard browser security headers to responses:
#   force HTTPS, no MIME sniffing, no referrer, no framing and a deny-all CSP.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/app.py: installed on the FastAPI app
#
# EXTERNAL DEPENDENCIES:
#   - fastapi: the Request type
# ==============================================================================
from fastapi import Request

# Static security headers applied to every response. This is a JSON/SSE API
# (no HTML is served), so a deny-all CSP is safe and acts as the backstop that
# limits what a stolen token could reach.
_SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        # setdefault keeps a header if the route already set its own value.
        response.headers.setdefault(header, value)
    return response
