from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity
from auto_apply_app.domain.value_objects import CreditTxKind


@dataclass
class CreditTransaction(Entity):
    """
    An append-only movement of AI credits.

    This exists because `UserSubscription.replenish_credits()` *resets* the balance on
    every billing cycle, which destroys any record of what was consumed. Without this
    ledger, "AI credits consumed" is unanswerable beyond the current period.

    `delta` is signed: negative when credits are consumed, positive when replenished.
    `balance_after` is the balance the movement landed on, so the ledger can be
    reconciled against `user_subscriptions.ai_credits_balance`.
    """

    user_id: UUID
    delta: int
    balance_after: int
    kind: CreditTxKind
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
