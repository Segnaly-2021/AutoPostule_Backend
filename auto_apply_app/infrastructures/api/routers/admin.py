# auto_apply_app/infrastructures/api/routers/admin.py
#
# Admin observability surface. Read-only by design: there are no write actions here, so
# even a full compromise of this router cannot mutate a user, a subscription, or a
# credit balance.
#
# Two independent things guard it:
#   1. CurrentAdminId — re-reads is_admin from the database on every request. This is the
#      control that actually matters, and it holds even if the URL below is public.
#   2. The mount prefix comes from ADMIN_ROUTE_PREFIX and the routes are excluded from
#      the OpenAPI schema. That is defense-in-depth, not a security boundary.

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from auto_apply_app.infrastructures.api.dependencies.admin_deps import CurrentAdminId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.rate_limit import limiter
from auto_apply_app.infrastructures.api.schema.admin_schema import AdminOverviewSchema
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.admin_controllers import AdminController

router = APIRouter()


# Dependency Boilerplate
def get_admin_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> AdminController:
    return app_container.admin_controller


AdminControllerDep = Annotated[AdminController, Depends(get_admin_controller)]


@router.get(
    "/metrics/overview",
    response_model=AdminOverviewSchema,
    include_in_schema=False,
    summary="Platform-wide observability metrics",
)
@limiter.limit("60/minute")
async def get_overview_metrics(
    request: Request,
    admin_id: CurrentAdminId,
    controller: AdminControllerDep,
):
    result = await controller.handle_get_overview()
    return handle_result(result)
