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