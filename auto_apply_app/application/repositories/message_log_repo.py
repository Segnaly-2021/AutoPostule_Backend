# auto_apply_app/application/repositories/message_log_repo.py
from abc import ABC, abstractmethod
from uuid import UUID

from auto_apply_app.domain.value_objects import MessageKind


class MessageLogRepository(ABC):
    """Which lifecycle messages a user has already received.

    Exists so a scheduled job can be run twice — after a failure, on a backfill,
    or by two overlapping executions — without emailing anyone twice. The
    uniqueness lives in the database, not here: `record` relies on the UNIQUE on
    (user_id, kind) and reports the conflict rather than raising, so a duplicate
    is an ordinary outcome instead of a crashed job.
    """

    @abstractmethod
    async def record(self, user_id: UUID, kind: MessageKind) -> bool:
        """Mark a message delivered. Returns False if it was already recorded.

        Call AFTER the send is accepted. A row written on a failed send would
        suppress that message permanently, since this table is also what
        suppresses a retry.
        """
        pass

    @abstractmethod
    async def already_sent(self, user_id: UUID, kind: MessageKind) -> bool:
        """Cheap pre-filter, so a cohort of N does not attempt N sends that the
        insert would reject anyway. `record` is still the authority."""
        pass
