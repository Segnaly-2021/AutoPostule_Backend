# auto_apply_app/infrastructures/api/routes/free_search_routes.py

from fastapi import APIRouter, Depends
from typing import Annotated

from auto_apply_app.interfaces.controllers.free_search_controller import FreeSearchController
from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.infrastructures.api.schema.free_search_schema import (
    FreeSearchRequestSchema,
    FreeSearchResponseSchema
)

router = APIRouter()

# Dependency to get controller from container
def get_free_search_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> FreeSearchController:
    return app_container.free_search_controller

FreeSearchControllerDep = Annotated[FreeSearchController, Depends(get_free_search_controller)]


@router.post(
    "/start",
    response_model=FreeSearchResponseSchema,
    summary="Free tier job search",
    description="Search multiple job boards in parallel without authentication"
)
async def search_jobs_free(
    data: FreeSearchRequestSchema,
    controller: FreeSearchControllerDep
):
    """
    **Free Tier Job Search - No Authentication Required**
    
    Searches multiple job boards (WTTJ, HelloWork, APEC) in parallel and returns 
    job snippets for preview purposes.
    
    **Features:**
    - Parallel scraping across 3 job boards
    - No login required (public listings only)
    - Fast preview of available jobs
    
    **Limitations:**
    - No AI-generated cover letters
    - No job applications
    - Results limited to 10, 20, or 50 jobs
    - No saved history or tracking
    
    **For full automation, upgrade to a paid plan.**
    
    **Returns:**
    - List of job snippets with basic info (title, company, location, description)
    - Total count of jobs found
    - Which boards were searched
    """
    
    result = await controller.handle_search(
        query=data.query,
        target_count=data.targetCount
    )
    
    return handle_result(result)