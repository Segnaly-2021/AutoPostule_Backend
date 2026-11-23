# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/dependencies/captcha_dep.py
#
# WHAT IT DOES:
#   FastAPI dependency that blocks a request unless it carries a valid Cloudflare
#   Turnstile token in the `cf-turnstile-response` header. It protects public
#   account routes such as register, login and forgot-password from bots.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/routers/user.py: register, login, forgot-password, resend-verification
#
# EXTERNAL DEPENDENCIES:
#   - fastapi: dependency injection, header parsing and HTTP errors
#   - Cloudflare Turnstile: verifies the token (through container.captcha_port)
# ==============================================================================
from typing import Annotated
from fastapi import Depends, HTTPException, Header, Request, status

from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application


async def verify_captcha(
    request: Request,
    container: Annotated[Application, Depends(get_container)],
    cf_turnstile_response: Annotated[str | None, Header(alias="cf-turnstile-response")] = None,
) -> None:
    """
    FastAPI dependency: rejects the request with 403 if the Turnstile token
    is missing or invalid. Frontend must include the token from the widget
    in the `cf-turnstile-response` header.

    Strict mode: ANY failure (missing, network error, invalid) → 403.
    """
    client_ip = request.client.host if request.client else None
    valid = await container.captcha_port.verify(
        token=cf_turnstile_response or "",
        remote_ip=client_ip,
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CAPTCHA verification failed.",
        )