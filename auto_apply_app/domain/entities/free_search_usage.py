# auto_apply_app/domain/entities/free_search_usage.py
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity
from auto_apply_app.domain.entities.agent_usage import _today_utc


@dataclass
class FreeSearchUsage(Entity):
    """
    Tracks free-search usage for a single user on a single UTC date.
    
    Same pattern as AgentUsage: one row per (user_id, usage_date),
    natural daily reset via the date column.
    
    DB constraint required: UNIQUE (user_id, usage_date).
    Repo provides: get_or_create_for_today(user_id).
    """
    DAILY_LIMIT: int = 10  # All tiers, including FREE

    user_id: UUID = None
    usage_date: date = field(default_factory=_today_utc)
    searches_count: int = 0

    def can_run(self) -> tuple[bool, str | None]:
        """Returns (allowed, reason_if_blocked)."""
        if self.searches_count >= self.DAILY_LIMIT:
            return False, (
                f"Daily free-search limit reached ({self.DAILY_LIMIT}). "
                "Resets at midnight UTC."
            )
        return True, None

    def record(self) -> None:
        """Increment the counter. Call after a successful free-search."""
        self.searches_count += 1