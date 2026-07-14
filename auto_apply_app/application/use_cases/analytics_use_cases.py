import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.dtos.analytics_dtos import RecordPageViewRequest
from auto_apply_app.application.repositories.unit_of_work import UnitOfWorkFactory
from auto_apply_app.domain.entities.page_view import PageView

logger = logging.getLogger(__name__)


@dataclass
class RecordPageViewUseCase:
    uow_factory: UnitOfWorkFactory

    async def execute(self, request: RecordPageViewRequest) -> Result[dict]:
        try:
            page_view = PageView(
                visitor_id=request.visitor_id,
                path=request.path,
                referrer=request.referrer,
                user_id=UUID(request.user_id) if request.user_id else None,
            )

            async with self.uow_factory() as uow:
                await uow.page_view_repo.save(page_view)

            return Result.success({"recorded": True})

        except Exception:
            # Tracking is best-effort: it must never break a page load. The caller
            # returns 204 regardless, so this only ever surfaces in the logs.
            logger.exception("RecordPageViewUseCase failed")
            return Result.failure(Error.system_error("Could not record the page view."))


@dataclass
class PruneOldPageViewsUseCase:
    """
    Retention for the page_views table, which otherwise grows without bound.

    Not exposed as an endpoint — v1's admin surface is strictly read-only, and a
    destructive operation does not belong behind a GET-only dashboard. Retention runs
    as a scheduled database job; this use case exists for the day a scheduled worker
    should own it instead.
    """

    uow_factory: UnitOfWorkFactory

    async def execute(self, retention_days: int) -> Result[dict]:
        try:
            if retention_days < 1:
                return Result.failure(
                    Error.validation_error("retention_days must be at least 1.")
                )

            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

            async with self.uow_factory() as uow:
                deleted = await uow.page_view_repo.delete_older_than(cutoff)

            logger.info("Pruned %s page views older than %s", deleted, cutoff.isoformat())
            return Result.success({"deleted_count": deleted})

        except Exception:
            logger.exception("PruneOldPageViewsUseCase failed")
            return Result.failure(Error.system_error("Could not prune page views."))
