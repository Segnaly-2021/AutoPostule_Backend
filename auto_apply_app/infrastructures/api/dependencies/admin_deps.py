# auto_apply_app/infrastructures/api/dependencies/admin_deps.py
import logging
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application

logger = logging.getLogger(__name__)


async def get_current_admin_id(
    request: Request,
    current_user_id: CurrentUserId,
    container: Annotated[Application, Depends(get_container)],
) -> str:
    """
    Authorize an admin request.

    Chains onto CurrentUserId, so the JWT is validated and checked against the logout
    blacklist first. Then the admin flag is read FROM THE DATABASE — not from a token
    claim. A claim is frozen at login, so revoking someone's admin rights would not take
    effect until their token expired; this way it takes effect on their next request.

    Denials are deliberately indistinguishable from one another: a valid non-admin user,
    a deactivated admin, and a failed lookup all get the same bare 403. Nothing in the
    response tells a caller whether they merely lack the flag, which would turn this
    endpoint into an oracle for probing who is an admin.
    """
    result = await container.check_admin_use_case.execute(current_user_id)

    if not (result.is_success and result.value is True):
        logger.warning(
            "admin_access_denied user_id=%s path=%s ip=%s",
            current_user_id,
            request.url.path,
            request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    return current_user_id


CurrentAdminId = Annotated[str, Depends(get_current_admin_id)]
