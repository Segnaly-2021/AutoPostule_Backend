# auto_apply_app/infrastructures/persistence/database/repositories/message_log_repo_db.py
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auto_apply_app.application.repositories.message_log_repo import MessageLogRepository
from auto_apply_app.domain.value_objects import MessageKind
from auto_apply_app.infrastructures.persistence.database.models.schema import MessageLogDB


class MessageLogRepoDB(MessageLogRepository):
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record(self, user_id: UUID, kind: MessageKind) -> bool:
        # ON CONFLICT DO NOTHING rather than a SELECT-then-INSERT: two executions
        # of the job racing each other would both pass a prior check and both
        # insert. Here the loser simply gets rowcount 0 and reports it.
        stmt = (
            pg_insert(MessageLogDB)
            .values(user_id=user_id, kind=kind)
            .on_conflict_do_nothing(constraint="uq_message_log_user_kind")
        )
        result = await self.session.execute(stmt)
        return (result.rowcount or 0) > 0

    async def already_sent(self, user_id: UUID, kind: MessageKind) -> bool:
        stmt = (
            select(MessageLogDB.id)
            .where(MessageLogDB.user_id == user_id)
            .where(MessageLogDB.kind == kind)
            .limit(1)
        )
        return (await self.session.execute(stmt)).first() is not None
