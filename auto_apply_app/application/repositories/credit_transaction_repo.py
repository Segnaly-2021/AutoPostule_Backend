"""
This module defines the repository interface for CreditTransaction persistence.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from auto_apply_app.domain.entities.credit_transaction import CreditTransaction


class CreditTransactionRepository(ABC):
    """
    Append-only ledger of AI credit movements.

    Note what is NOT here: recording a CONSUME movement. Consumption is written by
    SubscriptionRepository.try_consume_credits, inside the same single guarded UPDATE
    that performs the decrement. Exposing a `record_consumption()` here and calling it
    after the decrement would split one atomic statement into two, which is exactly the
    lost-update race that try_consume_credits was hardened against. The ledger row and
    the decrement must succeed or fail together, so they stay in one statement.

    REPLENISH movements are safe to record through this port: they happen in the Stripe
    webhook, where the subscription row is already being written in the same transaction.
    """

    @abstractmethod
    async def record(self, transaction: CreditTransaction) -> None:
        """Append a movement. Used for REPLENISH — see the class docstring for CONSUME."""
        pass

    @abstractmethod
    async def sum_consumed_between(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        """
        Total credits consumed in the window, as a positive number.

        Both bounds are optional: omit them for an all-time total.
        """
        pass

    @abstractmethod
    async def earliest_created_at(self) -> Optional[datetime]:
        """
        Timestamp of the oldest ledger row, or None if the ledger is empty.

        The dashboard uses this to know how far back its exact figures reach — the
        ledger starts at deploy time and is deliberately not backfilled.
        """
        pass
