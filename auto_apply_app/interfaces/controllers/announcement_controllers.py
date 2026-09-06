# auto_apply_app/interfaces/controllers/announcement_controllers.py

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from auto_apply_app.application.dtos.announcement_dtos import SaveAnnouncementRequest
from auto_apply_app.application.use_cases.announcement_use_cases import (
    DeleteAnnouncementUseCase,
    DismissAnnouncementUseCase,
    GetActiveAnnouncementsUseCase,
    ListAnnouncementsUseCase,
    SaveAnnouncementUseCase,
)
from auto_apply_app.interfaces.presenters.base_presenter import AnnouncementPresenter
from auto_apply_app.interfaces.viewmodels.base import OperationResult


@dataclass
class AnnouncementController:
    """Both audiences on one controller.

    They share every use case dependency's shape and all of the error handling;
    what separates them is the route guard, which is the router's job. Splitting
    them here would duplicate _handle_error and hide that the read and admin paths
    return the same entity in two shapes.
    """

    get_active_use_case: GetActiveAnnouncementsUseCase
    dismiss_use_case: DismissAnnouncementUseCase
    list_use_case: ListAnnouncementsUseCase
    save_use_case: SaveAnnouncementUseCase
    delete_use_case: DeleteAnnouncementUseCase
    presenter: AnnouncementPresenter

    # --- Read path ---

    async def handle_get_active(self, user_id: str | UUID) -> OperationResult:
        try:
            result = await self.get_active_use_case.execute(_as_uuid(user_id))
            if result.is_success:
                return OperationResult.succeed(result.value)
            return self._handle_error(result)
        except ValueError as e:
            return self._validation_failure(e)

    async def handle_dismiss(
        self, user_id: str | UUID, announcement_id: str | UUID
    ) -> OperationResult:
        try:
            result = await self.dismiss_use_case.execute(
                _as_uuid(user_id), _as_uuid(announcement_id)
            )
            if result.is_success:
                return OperationResult.succeed(result.value)
            return self._handle_error(result)
        except ValueError as e:
            return self._validation_failure(e)

    # --- Admin path ---

    async def handle_list(self, limit: int = 100) -> OperationResult:
        result = await self.list_use_case.execute(limit=limit)
        if result.is_success:
            return OperationResult.succeed(result.value)
        return self._handle_error(result)

    async def handle_save(
        self,
        title_fr: str,
        title_en: str,
        body_fr: str,
        body_en: str,
        cta_label_fr: Optional[str] = None,
        cta_label_en: Optional[str] = None,
        cta_url: Optional[str] = None,
        icon: Optional[str] = None,
        is_published: bool = False,
        starts_at: Optional[datetime] = None,
        ends_at: Optional[datetime] = None,
        announcement_id: Optional[str | UUID] = None,
    ) -> OperationResult:
        try:
            request = SaveAnnouncementRequest(
                title_fr=title_fr,
                title_en=title_en,
                body_fr=body_fr,
                body_en=body_en,
                cta_label_fr=cta_label_fr,
                cta_label_en=cta_label_en,
                cta_url=cta_url,
                icon=icon,
                is_published=is_published,
                starts_at=starts_at,
                ends_at=ends_at,
                announcement_id=_as_uuid(announcement_id) if announcement_id else None,
            )
            result = await self.save_use_case.execute(request)
            if result.is_success:
                return OperationResult.succeed(result.value)
            return self._handle_error(result)
        except ValueError as e:
            return self._validation_failure(e)

    async def handle_delete(self, announcement_id: str | UUID) -> OperationResult:
        try:
            result = await self.delete_use_case.execute(_as_uuid(announcement_id))
            if result.is_success:
                return OperationResult.succeed(result.value)
            return self._handle_error(result)
        except ValueError as e:
            return self._validation_failure(e)

    # --- Errors ---

    def _handle_error(self, result) -> OperationResult:
        error_vm = self.presenter.present_error(
            result.error.message, str(result.error.code.name)
        )
        return OperationResult.fail(error_vm.message, error_vm.code)

    def _validation_failure(self, exc: ValueError) -> OperationResult:
        error_vm = self.presenter.present_error(str(exc), "VALIDATION_ERROR")
        return OperationResult.fail(error_vm.message, error_vm.code)


def _as_uuid(value: str | UUID) -> UUID:
    """Callers pass either -- normalize, don't assume. A malformed id raises
    ValueError, which the handlers turn into a 400 rather than a 500."""
    return value if isinstance(value, UUID) else UUID(str(value))
