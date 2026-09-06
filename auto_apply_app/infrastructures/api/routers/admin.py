# auto_apply_app/infrastructures/api/routers/admin.py
#
# Admin surface. Everything about USERS is read-only: no route here can mutate a
# user, a subscription, or a credit balance, so a compromise of this router cannot
# touch anyone's account.
#
# The announcement routes below are the exception, and the only write surface an
# admin has. They are narrow on purpose -- they write one table whose entire
# contents are published prose, they take no user id, and the worst a bad actor
# does with them is show everyone a message, which is visible and reversible in
# one click. Any future admin write should have to argue for itself the same way.
#
# Two independent things guard it:
#   1. CurrentAdminId — re-reads is_admin from the database on every request. This is the
#      control that actually matters, and it holds even if the URL below is public.
#   2. The mount prefix comes from ADMIN_ROUTE_PREFIX and the routes are excluded from
#      the OpenAPI schema. That is defense-in-depth, not a security boundary.

import logging
from typing import Annotated, List

from fastapi import APIRouter, Depends, Request, status

from auto_apply_app.infrastructures.api.dependencies.admin_deps import CurrentAdminId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.rate_limit import limiter
from auto_apply_app.infrastructures.api.schema.admin_schema import AdminOverviewSchema
from auto_apply_app.infrastructures.api.schema.announcement_schema import (
    AdminAnnouncementSchema,
    AnnouncementWriteSchema,
)
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.admin_controllers import AdminController
from auto_apply_app.interfaces.controllers.announcement_controllers import (
    AnnouncementController,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# Dependency Boilerplate
def get_admin_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> AdminController:
    return app_container.admin_controller


def get_announcement_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> AnnouncementController:
    return app_container.announcement_controller


AdminControllerDep = Annotated[AdminController, Depends(get_admin_controller)]
AnnouncementControllerDep = Annotated[
    AnnouncementController, Depends(get_announcement_controller)
]


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


# ----------------------------------------------------------------------
# Announcements -- the admin write surface
#
# Every write is logged with the acting admin id. There is no audit table yet and
# building one for a single feature would be premature, but a published notice is
# seen by every user at once, so "who published this" has to be answerable from
# the logs at minimum.
# ----------------------------------------------------------------------

@router.get(
    "/announcements",
    response_model=List[AdminAnnouncementSchema],
    include_in_schema=False,
    summary="All announcements, drafts and expired included",
)
@limiter.limit("60/minute")
async def list_announcements(
    request: Request,
    admin_id: CurrentAdminId,
    controller: AnnouncementControllerDep,
):
    result = await controller.handle_list()
    return handle_result(result)


@router.post(
    "/announcements",
    response_model=AdminAnnouncementSchema,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
    summary="Create an announcement",
)
@limiter.limit("30/minute")
async def create_announcement(
    request: Request,
    payload: AnnouncementWriteSchema,
    admin_id: CurrentAdminId,
    controller: AnnouncementControllerDep,
):
    result = await controller.handle_save(**payload.model_dump())
    logger.info("Admin %s created an announcement", admin_id)
    return handle_result(result)


@router.patch(
    "/announcements/{announcement_id}",
    response_model=AdminAnnouncementSchema,
    include_in_schema=False,
    summary="Update an announcement, including its publish toggle",
)
@limiter.limit("30/minute")
async def update_announcement(
    request: Request,
    announcement_id: str,
    payload: AnnouncementWriteSchema,
    admin_id: CurrentAdminId,
    controller: AnnouncementControllerDep,
):
    # A full replace, not a partial merge, even though the verb is PATCH: the
    # composer always posts the whole form back, and a field-by-field merge would
    # make "clear this CTA" impossible to express -- omitted and null would mean
    # the same thing.
    result = await controller.handle_save(
        announcement_id=announcement_id, **payload.model_dump()
    )
    logger.info(
        "Admin %s updated announcement %s (published=%s)",
        admin_id, announcement_id, payload.is_published,
    )
    return handle_result(result)


@router.delete(
    "/announcements/{announcement_id}",
    include_in_schema=False,
    summary="Delete an announcement and its dismissals",
)
@limiter.limit("30/minute")
async def delete_announcement(
    request: Request,
    announcement_id: str,
    admin_id: CurrentAdminId,
    controller: AnnouncementControllerDep,
):
    # For a mistake caught before anyone saw it. Retiring a notice people HAVE
    # seen is is_published=false, which keeps the dismissals so re-publishing does
    # not re-show it to them.
    result = await controller.handle_delete(announcement_id)
    logger.warning("Admin %s deleted announcement %s", admin_id, announcement_id)
    return handle_result(result)
