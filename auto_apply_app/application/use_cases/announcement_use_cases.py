"""
Use cases for in-app announcements.

Split in two by audience, not by verb. The read pair runs on every authenticated
page load and must be cheap and unfailing -- an announcement that cannot be
fetched should never block the app. The admin trio is cold, runs for one person,
and is the first place in this codebase where an admin *writes* anything, so it
validates loudly rather than degrading quietly.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from auto_apply_app.application.common.result import Error, ErrorReason, Result
from auto_apply_app.application.dtos.announcement_dtos import (
    AdminAnnouncementResponse,
    AnnouncementResponse,
    SaveAnnouncementRequest,
)
from auto_apply_app.application.repositories.unit_of_work import UnitOfWorkFactory
from auto_apply_app.domain.entities.announcement import Announcement
from auto_apply_app.domain.exceptions import ValidationError

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Read path
# ----------------------------------------------------------------------

@dataclass
class GetActiveAnnouncementsUseCase:
    uow_factory: UnitOfWorkFactory

    async def execute(self, user_id: UUID) -> Result[list]:
        try:
            async with self.uow_factory() as uow:
                rows = await uow.announcement_repo.list_active_for_user(
                    user_id, datetime.now(timezone.utc)
                )
            return Result.success([AnnouncementResponse.from_entity(a) for a in rows])

        except Exception:
            # An empty list is the honest degraded answer: the modal simply does
            # not appear. Failing the request would put a release note in the way
            # of someone trying to use the product.
            logger.exception("GetActiveAnnouncementsUseCase failed")
            return Result.success([])


@dataclass
class DismissAnnouncementUseCase:
    uow_factory: UnitOfWorkFactory

    async def execute(self, user_id: UUID, announcement_id: UUID) -> Result[dict]:
        try:
            async with self.uow_factory() as uow:
                # Dismissing something that does not exist is a 404, not a silent
                # success: it means the client is holding a stale id, and writing
                # the view row anyway would leave a dangling FK.
                if await uow.announcement_repo.get(announcement_id) is None:
                    return Result.failure(Error.not_found(
                        entity="Announcement", entity_id=str(announcement_id),
                    ))
                await uow.announcement_repo.dismiss(user_id, announcement_id)

            # Deliberately not reporting whether the row was new. A second dismiss
            # is the same outcome for the user, and the client has nothing to do
            # differently.
            return Result.success({"dismissed": True})

        except Exception:
            logger.exception("DismissAnnouncementUseCase failed")
            return Result.failure(Error.system_error("Could not dismiss the announcement."))


# ----------------------------------------------------------------------
# Admin path
# ----------------------------------------------------------------------

@dataclass
class ListAnnouncementsUseCase:
    """Everything, drafts included -- an admin who cannot see a draft cannot
    finish writing it."""

    uow_factory: UnitOfWorkFactory

    async def execute(self, limit: int = 100) -> Result[list]:
        try:
            async with self.uow_factory() as uow:
                rows = await uow.announcement_repo.list_all(limit=limit)
            return Result.success(
                [AdminAnnouncementResponse.from_entity(a) for a in rows]
            )
        except Exception:
            logger.exception("ListAnnouncementsUseCase failed")
            return Result.failure(Error.system_error("Could not list announcements."))


@dataclass
class SaveAnnouncementUseCase:
    """Create or update, chosen by whether the request carries an id.

    One use case rather than two because the validation, the field mapping and the
    failure modes are identical, and the only branch is which entity gets mutated.
    """

    uow_factory: UnitOfWorkFactory

    async def execute(self, request: SaveAnnouncementRequest) -> Result[AdminAnnouncementResponse]:
        try:
            data = request.cleaned()

            async with self.uow_factory() as uow:
                if data.announcement_id is not None:
                    announcement = await uow.announcement_repo.get(data.announcement_id)
                    if announcement is None:
                        return Result.failure(Error.not_found(
                            entity="Announcement",
                            entity_id=str(data.announcement_id),
                        ))
                    self._apply(announcement, data)
                    # touch() re-validates: an edit can break rules a valid row
                    # satisfied, e.g. clearing a CTA label but not its URL.
                    announcement.touch()
                else:
                    announcement = Announcement(
                        title_fr=data.title_fr,
                        title_en=data.title_en,
                        body_fr=data.body_fr,
                        body_en=data.body_en,
                        cta_label_fr=data.cta_label_fr,
                        cta_label_en=data.cta_label_en,
                        cta_url=data.cta_url,
                        icon=data.icon,
                        is_published=data.is_published,
                        **({"starts_at": data.starts_at} if data.starts_at else {}),
                        ends_at=data.ends_at,
                    )

                await uow.announcement_repo.save(announcement)

            logger.info(
                "Announcement saved: id=%s published=%s",
                announcement.id, announcement.is_published,
            )
            return Result.success(AdminAnnouncementResponse.from_entity(announcement))

        except ValidationError as e:
            # The composer shows this text, so it has to say what is wrong rather
            # than that something is.
            return Result.failure(Error.validation_error(str(e)))
        except Exception:
            logger.exception("SaveAnnouncementUseCase failed")
            return Result.failure(Error.system_error("Could not save the announcement."))

    @staticmethod
    def _apply(announcement: Announcement, data: SaveAnnouncementRequest) -> None:
        announcement.title_fr = data.title_fr
        announcement.title_en = data.title_en
        announcement.body_fr = data.body_fr
        announcement.body_en = data.body_en
        announcement.cta_label_fr = data.cta_label_fr
        announcement.cta_label_en = data.cta_label_en
        announcement.cta_url = data.cta_url
        announcement.icon = data.icon
        announcement.is_published = data.is_published
        if data.starts_at is not None:
            announcement.starts_at = data.starts_at
        announcement.ends_at = data.ends_at


@dataclass
class DeleteAnnouncementUseCase:
    """For a mistake caught before anyone saw it.

    Retiring a notice people HAVE seen is an unpublish (is_published=False through
    SaveAnnouncementUseCase), which keeps the dismissals so re-publishing later
    does not re-show it to them. This erases both.
    """

    uow_factory: UnitOfWorkFactory

    async def execute(self, announcement_id: UUID) -> Result[dict]:
        try:
            async with self.uow_factory() as uow:
                deleted = await uow.announcement_repo.delete(announcement_id)

            if not deleted:
                return Result.failure(Error.not_found(
                    entity="Announcement", entity_id=str(announcement_id),
                ))
            logger.info("Announcement deleted: id=%s", announcement_id)
            return Result.success({"deleted": True})

        except Exception:
            logger.exception("DeleteAnnouncementUseCase failed")
            return Result.failure(Error.system_error("Could not delete the announcement."))
