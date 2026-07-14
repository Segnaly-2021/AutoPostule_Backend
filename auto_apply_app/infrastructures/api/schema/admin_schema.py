from typing import Optional

from pydantic import BaseModel, Field


class UsersMetricsSchema(BaseModel):
    total: int
    verified: int
    signups_this_month: int
    signups_last_month: int
    signups_delta_pct: Optional[float] = Field(
        None,
        description="Month-over-month growth. Null when last month was zero, "
                    "where a percentage change is undefined.",
    )


class VisitorsMetricsSchema(BaseModel):
    unique_today: int = Field(
        ...,
        description="Distinct browsers/devices since UTC midnight. The headline number.",
    )
    sessions_today: int = Field(
        ...,
        description="Visits since UTC midnight. A visit ends after 30 minutes of "
                    "inactivity, so one person can have several in a day.",
    )
    page_views_today: int = Field(
        ...,
        description="Raw page views since UTC midnight. Traffic volume, not people.",
    )
    signed_in_today: int = Field(
        ...,
        description="Unique visitors we could attribute to an account.",
    )
    anonymous_today: int = Field(
        ...,
        description="Unique visitors with no account attached. "
                    "signed_in_today + anonymous_today == unique_today, always.",
    )


class ApplicationsMetricsSchema(BaseModel):
    sent_total: int
    sent_this_month: int
    sent_last_month: int


class AiCreditsMetricsSchema(BaseModel):
    consumed_total: int
    consumed_this_month: int
    consumed_last_month: int
    consumed_current_period_estimate: int = Field(
        ...,
        description="Fallback estimate (allocated - balance, summed over active "
                    "subscriptions). Use this while is_ledger_complete_for_month is false.",
    )
    ledger_started_at: Optional[str] = Field(
        None,
        description="When credit tracking began. Null if no credits have moved yet. "
                    "The ledger is not backfilled, so figures before this are unknown.",
    )
    is_ledger_complete_for_month: bool = Field(
        ...,
        description="False while the ledger started mid-month, meaning consumed_this_month "
                    "undercounts and the estimate should be shown instead.",
    )


class AdminOverviewSchema(BaseModel):
    generated_at: str
    users: UsersMetricsSchema
    visitors: VisitorsMetricsSchema
    applications: ApplicationsMetricsSchema
    ai_credits: AiCreditsMetricsSchema
