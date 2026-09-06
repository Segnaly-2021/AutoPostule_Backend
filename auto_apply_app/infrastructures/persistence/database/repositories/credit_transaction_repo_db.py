# =============================================================================
# credit_transaction_repo_db.py
# =============================================================================
from datetime import datetime
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.application.repositories.credit_transaction_repo import (
    CreditTransactionRepository,
)
from auto_apply_app.domain.entities.credit_transaction import CreditTransaction
from auto_apply_app.domain.value_objects import CreditTxKind
from auto_apply_app.infrastructures.persistence.database.models.schema import (
    CreditTransactionDB,
)


class CreditTransactionRepoDB(CreditTransactionRepository):

    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, transaction: CreditTransaction) -> None:
        # Append-only: add(), not merge(). CONSUME rows do not come through here — see
        # the port docstring; they are written inside try_consume_credits' single
        # atomic statement so that they cannot drift from the decrement.
        self.session.add(CreditTransactionDB(
            id=transaction.id,
            user_id=transaction.user_id,
            delta=transaction.delta,
            balance_after=transaction.balance_after,
            kind=transaction.kind,
            created_at=transaction.created_at,
        ))

    async def sum_consumed_between(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> int:
        # delta is negative for consumption; negate it so callers get a positive total.
        stmt = (
            select(func.coalesce(func.sum(-CreditTransactionDB.delta), 0))
            .where(CreditTransactionDB.kind == CreditTxKind.CONSUME)
        )
        if start is not None:
            stmt = stmt.where(CreditTransactionDB.created_at >= start)
        if end is not None:
            stmt = stmt.where(CreditTransactionDB.created_at < end)

        return int((await self.session.execute(stmt)).scalar_one())

    async def first_purchase_between(self, start, end) -> list:
        """Group to each user's first REPLENISH, then keep those inside the window."""
        first = (
            select(
                CreditTransactionDB.user_id,
                func.min(CreditTransactionDB.created_at).label("first_at"),
            )
            .where(CreditTransactionDB.kind == CreditTxKind.REPLENISH)
            .group_by(CreditTransactionDB.user_id)
            .subquery()
        )
        stmt = (
            select(first.c.user_id)
            .where(first.c.first_at >= start)
            .where(first.c.first_at < end)
        )
        return list((await self.session.execute(stmt)).scalars().all())

    async def earliest_created_at(self) -> Optional[datetime]:
        stmt = select(func.min(CreditTransactionDB.created_at))
        return (await self.session.execute(stmt)).scalar_one_or_none()

    def _map_to_entity(self, tx_db: CreditTransactionDB) -> CreditTransaction:
        transaction = CreditTransaction(
            user_id=tx_db.user_id,
            delta=tx_db.delta,
            balance_after=tx_db.balance_after,
            kind=tx_db.kind,
            created_at=tx_db.created_at,
        )
        transaction.id = tx_db.id
        return transaction
