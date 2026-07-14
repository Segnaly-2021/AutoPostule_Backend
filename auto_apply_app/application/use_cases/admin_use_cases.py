import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.dtos.admin_dtos import (
    AdminOverviewResponse,
    AiCreditsMetrics,
    ApplicationsMetrics,
    UsersMetrics,
    VisitorsMetrics,
)
from auto_apply_app.application.repositories.unit_of_work import UnitOfWorkFactory
from auto_apply_app.domain.value_objects import ApplicationStatus

logger = logging.getLogger(__name__)


def _month_bounds(now: datetime) -> tuple[datetime, datetime, datetime]:
    """
    Return (last_month_start, this_month_start, next_month_start) in UTC.

    Windows are half-open [start, end) so a view at exactly midnight on the 1st is
    counted once, in the month it belongs to.
    """
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)

    if this_month_start.month == 12:
        next_month_start = this_month_start.replace(year=this_month_start.year + 1, month=1)
    else:
        next_month_start = this_month_start.replace(month=this_month_start.month + 1)

    return last_month_start, this_month_start, next_month_start


@dataclass
class CheckAdminAccessUseCase:
    """
    Authoritative admin check, run on EVERY admin request.

    The flag is read from the database rather than from a JWT claim on purpose: a claim
    is frozen at login and would keep granting access until the token expired, so
    revoking admin would not take effect for hours. Reading it costs one indexed lookup.
    """

    uow_factory: UnitOfWorkFactory

    async def execute(self, user_id: str) -> Result[bool]:
        try:
            user_uuid = UUID(str(user_id))
        except (ValueError, AttributeError, TypeError):
            # A malformed `sub` cannot belong to an admin. Not an error worth raising.
            logger.warning("CheckAdminAccessUseCase got a malformed user id")
            return Result.success(False)

        try:
            async with self.uow_factory() as uow:
                auth_user = await uow.auth_repo.get_by_id(user_uuid)

            granted = bool(auth_user and auth_user.is_active and auth_user.is_admin)
            return Result.success(granted)

        except Exception:
            logger.exception("CheckAdminAccessUseCase failed for user %s", user_id)
            # Fail closed: the caller treats anything other than success(True) as denied.
            return Result.failure(Error.system_error("Could not verify admin access."))


@dataclass
class GetAdminOverviewMetricsUseCase:
    """Platform-wide observability figures for the admin dashboard."""

    uow_factory: UnitOfWorkFactory

    async def execute(self) -> Result[AdminOverviewResponse]:
        try:
            now = datetime.now(timezone.utc)
            last_month_start, this_month_start, next_month_start = _month_bounds(now)
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            next_day_start = today_start + timedelta(days=1)

            # One UoW, awaited sequentially. Do NOT asyncio.gather these: every repo here
            # shares a single AsyncSession, and concurrent statements on one session is
            # the exact race this codebase was recently hardened against.
            async with self.uow_factory() as uow:
                total_users = await uow.user_repo.count_all()
                verified_users = await uow.auth_repo.count_verified()
                signups_this_month = await uow.auth_repo.count_created_between(
                    this_month_start, next_month_start
                )
                signups_last_month = await uow.auth_repo.count_created_between(
                    last_month_start, this_month_start
                )

                unique_visitors = await uow.page_view_repo.count_unique_visitors_between(
                    today_start, next_day_start
                )
                sessions = await uow.page_view_repo.count_sessions_between(
                    today_start, next_day_start
                )
                page_views = await uow.page_view_repo.count_page_views_between(
                    today_start, next_day_start
                )
                signed_in_visitors = await uow.page_view_repo.count_signed_in_visitors_between(
                    today_start, next_day_start
                )

                sent_total = await uow.job_repo.count_by_status(ApplicationStatus.SUBMITTED)
                sent_this_month = await uow.job_repo.count_by_status(
                    ApplicationStatus.SUBMITTED, this_month_start, next_month_start
                )
                sent_last_month = await uow.job_repo.count_by_status(
                    ApplicationStatus.SUBMITTED, last_month_start, this_month_start
                )

                credits_total = await uow.credit_tx_repo.sum_consumed_between()
                credits_this_month = await uow.credit_tx_repo.sum_consumed_between(
                    this_month_start, next_month_start
                )
                credits_last_month = await uow.credit_tx_repo.sum_consumed_between(
                    last_month_start, this_month_start
                )
                ledger_started_at = await uow.credit_tx_repo.earliest_created_at()

                active_subs = await uow.subscription_repo.list_active()

            # Transitional figure for the window before the ledger has history. Computed
            # on the entity so the per-plan allocation rule stays in the domain.
            estimate = sum(
                max(sub.allocated_ai_credits - sub.ai_credits_balance, 0)
                for sub in active_subs
            )

            response = AdminOverviewResponse(
                generated_at=now,
                users=UsersMetrics(
                    total=total_users,
                    verified=verified_users,
                    signups_this_month=signups_this_month,
                    signups_last_month=signups_last_month,
                ),
                visitors=VisitorsMetrics(
                    unique_today=unique_visitors,
                    sessions_today=sessions,
                    page_views_today=page_views,
                    signed_in_today=signed_in_visitors,
                    # Derived, not queried. A visitor who browses anonymously and THEN
                    # logs in leaves rows both with and without a user_id, so counting
                    # "anonymous" independently in SQL would count them twice and the
                    # split would not add up to the total. Subtracting guarantees it does.
                    anonymous_today=max(unique_visitors - signed_in_visitors, 0),
                ),
                applications=ApplicationsMetrics(
                    sent_total=sent_total,
                    sent_this_month=sent_this_month,
                    sent_last_month=sent_last_month,
                ),
                ai_credits=AiCreditsMetrics(
                    consumed_total=credits_total,
                    consumed_this_month=credits_this_month,
                    consumed_last_month=credits_last_month,
                    consumed_current_period_estimate=estimate,
                    ledger_started_at=ledger_started_at,
                ),
            )
            return Result.success(response)

        except Exception:
            logger.exception("GetAdminOverviewMetricsUseCase failed")
            return Result.failure(
                Error.system_error("An unexpected error occurred while retrieving admin metrics.")
            )
