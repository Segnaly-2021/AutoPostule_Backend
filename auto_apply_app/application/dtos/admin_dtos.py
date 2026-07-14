"""
DTOs for the admin observability dashboard.

All figures are platform-wide (not per-user) and all timestamps are UTC.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class UsersMetrics:
    total: int
    verified: int
    signups_this_month: int
    signups_last_month: int

    @property
    def signups_delta_pct(self) -> Optional[float]:
        """
        Month-over-month growth, or None when last month was zero — a percentage
        change from zero is undefined, and reporting it as 100% or infinity would be
        a lie the dashboard then renders as fact.
        """
        if self.signups_last_month == 0:
            return None
        delta = self.signups_this_month - self.signups_last_month
        return round((delta / self.signups_last_month) * 100, 1)


@dataclass(frozen=True)
class VisitorsMetrics:
    """
    Three different answers to "how many visitors", because it is three questions.

    One person browsing 20 pages in the morning and again in the evening is
    1 unique visitor, 2 sessions, and 20 page views. Reporting only one of these would
    quietly answer a question nobody asked.
    """

    unique_today: int        # distinct browsers/devices
    sessions_today: int      # visits, split by a 30-minute inactivity gap
    page_views_today: int    # raw views — traffic volume, not people
    signed_in_today: int     # unique visitors we could attribute to an account
    anonymous_today: int     # unique_today - signed_in_today (see the use case)


@dataclass(frozen=True)
class ApplicationsMetrics:
    sent_total: int
    sent_this_month: int
    sent_last_month: int


@dataclass(frozen=True)
class AiCreditsMetrics:
    consumed_total: int
    consumed_this_month: int
    consumed_last_month: int
    consumed_current_period_estimate: int
    ledger_started_at: Optional[datetime]

    @property
    def is_ledger_complete_for_month(self) -> bool:
        """
        True once the ledger covers the whole current month.

        Until then `consumed_this_month` undercounts (the ledger starts at deploy time
        and is deliberately not backfilled), and the UI should show
        `consumed_current_period_estimate` instead.
        """
        if self.ledger_started_at is None:
            return False
        now = datetime.now(self.ledger_started_at.tzinfo)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return self.ledger_started_at <= month_start


@dataclass(frozen=True)
class AdminOverviewResponse:
    generated_at: datetime
    users: UsersMetrics
    visitors: VisitorsMetrics
    applications: ApplicationsMetrics
    ai_credits: AiCreditsMetrics
