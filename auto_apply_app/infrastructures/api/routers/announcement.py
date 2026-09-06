# auto_apply_app/infrastructures/api/routers/announcement.py
#
# The user-facing half of announcements. Both routes are authenticated: a
# dismissal is recorded per user, so there is nobody to record it against
# without a session, and an anonymous visitor has no release notes to catch up on.
#
# The admin half lives in routers/admin.py, behind CurrentAdminId and the secret
# mount prefix. Keeping them apart means a change to the public shape cannot
# accidentally widen what an admin route exposes, and vice versa.

from typing import Annotated, List

from fastapi import APIRouter, Depends, Request, status

from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.rate_limit import limiter
from auto_apply_app.infrastructures.api.schema.announcement_schema import AnnouncementSchema
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.announcement_controllers import (
    AnnouncementController,
)

router = APIRouter()


# Dependency Boilerplate
def get_announcement_controller(
    app_container: Annotated[Application, Depends(get_container)]
) -> AnnouncementController:
    return app_container.announcement_controller


AnnouncementControllerDep = Annotated[
    AnnouncementController, Depends(get_announcement_controller)
]


@router.get(
    "/active",
    response_model=List[AnnouncementSchema],
    summary="Announcements this user has not dismissed",
)
# Generous: this fires on app mount, and a user with several tabs open should not
# trip a limit doing nothing wrong.
@limiter.limit("120/minute")
async def get_active_announcements(
    request: Request,
    user_id: CurrentUserId,
    controller: AnnouncementControllerDep,
):
    result = await controller.handle_get_active(user_id)
    return handle_result(result)


@router.post(
    "/{announcement_id}/dismiss",
    status_code=status.HTTP_200_OK,
    summary="Close an announcement for this user, on every device",
)
@limiter.limit("60/minute")
async def dismiss_announcement(
    request: Request,
    announcement_id: str,
    user_id: CurrentUserId,
    controller: AnnouncementControllerDep,
):
    result = await controller.handle_dismiss(user_id, announcement_id)
    return handle_result(result)
