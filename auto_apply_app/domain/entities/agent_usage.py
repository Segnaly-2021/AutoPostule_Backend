# auto_apply_app/domain/entities/agent_usage.py
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity


def _today_utc() -> date:
    """Returns today's date in UTC."""
    return datetime.now(timezone.utc).date()


@dataclass
class AgentUsage(Entity):
    """
    Tracks agent run usage for a single user on a single UTC date.
    
    One row per (user_id, usage_date). The daily reset is natural:
    yesterday's row is left untouched, today's row starts at 0.
    
    DB constraint required: UNIQUE (user_id, usage_date).
    Repo provides: get_or_create_for_today(user_id) — atomic UPSERT.
    """
    user_id: UUID = None
    usage_date: date = field(default_factory=_today_utc)
    runs_count: int = 0
    last_completed_at: datetime | None = None

    # ── Limit + cooldown check ──────────────────────────────────
    def can_start_run(
        self,
        daily_limit: int,
        base_cooldown_minutes: int,
    ) -> tuple[bool, str | None]:
        """
        Returns (allowed, reason_if_blocked).
        
        Rules:
        - daily_limit <= 0 means the tier doesn't include agent runs.
        - Hit daily_limit → blocked until midnight UTC (new row tomorrow).
        - Otherwise check exponential cooldown since last_completed_at.
        """
        if daily_limit <= 0:
            return False, "Your current plan does not include agent runs."

        if self.runs_count >= daily_limit:
            return False, (
                f"Daily limit reached ({daily_limit} runs). "
                "Resets at midnight UTC."
            )

        if self.last_completed_at is not None:
            cooldown = self._cooldown_for_next_run(base_cooldown_minutes)
            ready_at = self.last_completed_at + cooldown
            now = datetime.now(timezone.utc)
            if now < ready_at:
                wait_min = int((ready_at - now).total_seconds() / 60) + 1
                return False, (
                    f"Cooldown active. Try again in {wait_min} minute(s)."
                )

        return True, None

    def record_completed_run(self) -> None:
        """
        Called only when a search transitions to COMPLETED.
        Increments the daily counter and stamps the cooldown anchor.
        """
        self.runs_count += 1
        self.last_completed_at = datetime.now(timezone.utc)

    # ── Cooldown formula ────────────────────────────────────────
    def _cooldown_for_next_run(self, base_minutes: int) -> timedelta:
        """
        Exponential backoff: base * 2^(runs_count - 1), capped at 4x base.
        
        Basic (base=30, daily_limit=5):
          after run 1 → 30 min wait
          after run 2 → 60 min wait
          after run 3 → 120 min wait
          after run 4 → 120 min wait (capped)
          (run 5 done → daily limit hit, no more today)
        
        Premium (base=15, daily_limit=10):
          same formula with shorter base.
        """
        n = max(self.runs_count - 1, 0)
        multiplier = min(2 ** n, 4)
        return timedelta(minutes=base_minutes * multiplier)