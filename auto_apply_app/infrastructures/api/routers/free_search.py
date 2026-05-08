# auto_apply_app/infrastructures/api/routes/free_search_routes.py

from fastapi import APIRouter, Depends, Request
from typing import Annotated

from auto_apply_app.interfaces.controllers.free_search_controller import FreeSearchController
from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.infrastructures.api.schema.free_search_schema import (
    FreeSearchRequestSchema,
    FreeSearchResponseSchema,
)

# slowapi: per-IP backstop in case auth tokens get leaked or shared
from slowapi import Limiter
from slowapi.util import get_remote_address

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)


def get_free_search_controller(
    app_container: Annotated[Application, Depends(get_container)],
) -> FreeSearchController:
    return app_container.free_search_controller


FreeSearchControllerDep = Annotated[FreeSearchController, Depends(get_free_search_controller)]


@router.post(
    "/start",
    response_model=FreeSearchResponseSchema,
    summary="Free tier job search (authenticated)",
    description=(
        "Searches multiple job boards in parallel and returns job snippets. "
        "Requires authentication. "
        "Limited to 10 searches per user per day (resets at midnight UTC) "
        "and 20 requests per IP per hour."
    ),
)
@limiter.limit("20/hour")
async def search_jobs_free(
    request: Request,                 # required by slowapi
    data: FreeSearchRequestSchema,
    current_user_id: CurrentUserId,   # auth requirement — endpoint returns 401 without JWT
    controller: FreeSearchControllerDep,
):
    """
    Free tier job search.
    
    Searches WTTJ, HelloWork, and APEC in parallel and returns job snippets.
    Requires authentication. No AI processing, no saved history.
    
    Rate limits:
    - 10 searches per user per day (resets at midnight UTC)
    - 20 requests per IP per hour (anti-bot backstop)
    """
    result = await controller.handle_search(
        user_id=current_user_id,
        query=data.query,
        target_count=data.targetCount,
    )
    return handle_result(result)