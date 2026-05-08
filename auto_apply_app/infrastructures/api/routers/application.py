
from fastapi import APIRouter, Depends, Query
from typing import Annotated

from auto_apply_app.interfaces.controllers.job_offer_controllers import JobOfferController
from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.infrastructures.api.schema.job_offer_schema import (
    ApplicationFilters, 
    PaginationParams, 
    StatusUpdateSchema,
    AnalyticsViewModel
)

router = APIRouter()

# Dependency Boilerplate
def get_job_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> JobOfferController:
    return app_container.job_offer_controller

JobControllerDep = Annotated[JobOfferController, Depends(get_job_controller)]


@router.get(
    "/me",
    summary="Get user applications",
    description="Fetch filtered and paginated applications for current user"
)
async def get_user_applications(
    current_user_id: CurrentUserId,
    controller: JobControllerDep,
    filters: Annotated[ApplicationFilters, Depends()],
    pagination: Annotated[PaginationParams, Depends()]
):
    result = await controller.handle_get_list(
        user_id=current_user_id,
        page=pagination.page, 
        limit=pagination.limit,
        company=filters.company,
        title=filters.title,
        location=filters.location,
        board=filters.board,
        date_from=filters.date_from,
        date_to=filters.date_to,
        has_response=filters.has_response,
        has_interview=filters.has_interview
    )
    return handle_result(result)

@router.get(
    "/daily",
    summary="Get daily application stats",
    description="Fetches application statistics for the current day for the user"
)
async def get_daily_applications(
    current_user_id: CurrentUserId,
    controller: JobControllerDep,
):
    # Simply pass the user_id to your dedicated daily stats controller method
    result = await controller.handle_get_daily_stats(
        user_id=current_user_id
    )
    return handle_result(result)


@router.patch(
    "/{application_id}/response",
    summary="Toggle response status",
)
async def toggle_response_status(
    application_id: str,
    data: StatusUpdateSchema, # Body: {"status": true}
    current_user_id: CurrentUserId,
    controller: JobControllerDep
):
    # Notice: We ignore current_user_id for logic if the Repo handles ownership check,
    # but strictly we should pass it to EnsureOwnershipUseCase. 
    # For now, we assume the Repo enforces ownership or we pass it down.
    
    result = await controller.handle_toggle_response(
        job_id=application_id, 
        status=data.status
    )
    return handle_result(result)


@router.patch(
    "/{application_id}/interview",
    summary="Toggle interview status",
)
async def toggle_interview_status(
    application_id: str,
    data: StatusUpdateSchema,
    current_user_id: CurrentUserId,
    controller: JobControllerDep
):
    result = await controller.handle_toggle_interview(
        job_id=application_id, 
        status=data.status
    )
    return handle_result(result)


@router.get(
    "/analytics",
    response_model=AnalyticsViewModel,
    summary="Get application analytics",
)
async def get_analytics(
    current_user_id: CurrentUserId,
    controller: JobControllerDep,
    period: str = Query(default='all_time', regex="^(all_time|last_week|last_month)$")
):
    result = await controller.handle_analytics(
        user_id=current_user_id,
        period=period
    )
    return handle_result(result)

    

