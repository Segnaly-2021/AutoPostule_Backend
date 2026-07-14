# interfaces/api/dependencies/auth.py
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Annotated, Optional

from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.domain.exceptions import InvalidTokenException
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container


# HTTPBearer scheme for extracting Bearer tokens from Authorization header
security = HTTPBearer()

# Same scheme, but tolerant of a missing header. auto_error=False is the whole point:
# the default HTTPBearer raises 403 when there is no Authorization header, which would
# make any route using it unusable by anonymous callers.
optional_security = HTTPBearer(auto_error=False)


async def get_current_user_id(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
    container: Annotated[Application, Depends(get_container)]
) -> str:
    """
    Extracts and validates the JWT token from the Authorization header.
    Returns the authenticated user's ID.
    
    Raises:
        HTTPException 401: If token is invalid, expired, or blacklisted
    """
    token = credentials.credentials
    
    try:
        # 1. Decode and validate the token
        payload = container.token_provider.decode_token(token)
        
        # 2. Extract user_id from the 'sub' claim
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user identifier",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        # 3. Check if token has been blacklisted (user logged out)
        token_id = container.token_provider.get_token_id(token)
        is_blacklisted = await container.token_repo.is_blacklisted(token_id)
        
        if is_blacklisted:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has been revoked. Please login again.",
                headers={"WWW-Authenticate": "Bearer"}
            )
        
        return user_id
        
    except InvalidTokenException as e:
        # Domain exception from token provider (expired, invalid signature, etc.)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        # Catch-all for unexpected errors
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {e}",
            headers={"WWW-Authenticate": "Bearer"}
        )


def authorize_user_access(resource_user_id: str, current_user_id: str) -> None:
    """
    Verifies that the authenticated user is authorized to access the resource.
    
    Args:
        resource_user_id: The user_id that owns the resource
        current_user_id: The authenticated user's ID from the token
        
    Raises:
        HTTPException 403: If user is not authorized
    """
    if resource_user_id != current_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: You can only access your own resources"
        )



async def get_current_token(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]
) -> str:
    """
    Extracts the raw JWT token from the Authorization header.
    Does NOT validate the token - use this only when you need the raw token string.

    For logout operations where you need to blacklist the token itself.
    """
    return credentials.credentials


async def get_optional_user_id(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(optional_security)],
    container: Annotated[Application, Depends(get_container)],
) -> Optional[str]:
    """
    Resolves the current user if a usable token happens to be present, else None.

    For PUBLIC endpoints that want to attribute a request to an account when they can,
    but must keep working for anonymous callers — the visitor tracker being the case
    that matters. It NEVER raises: a missing, expired, forged or malformed token simply
    means "anonymous", not "rejected". An endpoint that 401s an anonymous visitor would
    measure nothing at all.

    Deliberately does NOT check the Redis logout blacklist, unlike get_current_user_id.
    This is attribution for a metric, not an authorization decision — nothing is exposed
    on the strength of it — and a Redis round-trip on every page view is latency we
    shouldn't pay. Worst case, an already-logged-out but not-yet-expired token attributes
    a few page views to its owner. Do not "fix" this by adding the blacklist check.
    """
    if credentials is None:
        return None

    try:
        payload = container.token_provider.decode_token(credentials.credentials)
        return payload.get("sub")
    except Exception:
        return None


# Type aliases for cleaner endpoint signatures
CurrentUserId = Annotated[str, Depends(get_current_user_id)]
CurrentToken = Annotated[str, Depends(get_current_token)]
OptionalUserId = Annotated[Optional[str], Depends(get_optional_user_id)]