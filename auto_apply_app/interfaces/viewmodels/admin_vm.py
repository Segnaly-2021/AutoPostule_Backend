from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class UsersMetricsViewModel:
    total: int
    verified: int
    signups_this_month: int
    signups_last_month: int
    signups_delta_pct: Optional[float]


@dataclass(frozen=True)
class VisitorsMetricsViewModel:
    unique_today: int
    sessions_today: int
    page_views_today: int
    signed_in_today: int
    anonymous_today: int


@dataclass(frozen=True)
class ApplicationsMetricsViewModel:
    sent_total: int
    sent_this_month: int
    sent_last_month: int


@dataclass(frozen=True)
class AiCreditsMetricsViewModel:
    consumed_total: int
    consumed_this_month: int
    consumed_last_month: int
    consumed_current_period_estimate: int
    ledger_started_at: Optional[str]
    # Tells the UI whether consumed_this_month can be trusted or whether it should
    # fall back to the estimate. The ledger only starts at deploy time.
    is_ledger_complete_for_month: bool


@dataclass(frozen=True)
class AdminOverviewViewModel:
    generated_at: str
    users: UsersMetricsViewModel
    visitors: VisitorsMetricsViewModel
    applications: ApplicationsMetricsViewModel
    ai_credits: AiCreditsMetricsViewModel
