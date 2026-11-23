# ==============================================================================
# FILE: auto_apply_app/infrastructures/api/routers/free_search.py
#
# WHAT IT DOES:
#   The free-tier job search route, mounted at /api/v1/free-search. It runs only in
#   the separate APP_ROLE=free_search service, whose image includes a browser. It
#   requires login and has two limits: a per-user daily quota and a per-IP hourly limit.
#
# USED BY:
#   - auto_apply_app/infrastructures/api/app.py: mounts this router in the "free_search" role
#
# EXTERNAL DEPENDENCIES:
#   - fastapi: routing and dependency injection
#   - slowapi: per-IP rate limit
#   - Playwright / Chromium and job boards: used by the fake agent behind the controller
#   - Cloud Run: hosts the dedicated autopostule-free-search service
# ==============================================================================
# auto_apply_app/infrastructures/api/routes/free_search.py

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
from auto_apply_app.infrastructures.api.rate_limit import limiter

router = APIRouter()


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
        "Searches the active job boards (currently HelloWork) and returns job snippets. "
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
    
    Searches HelloWork (currently the only active board) and returns job snippets.
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