# auto_apply_app/application/repositories/user_fingerprint_repo.py
from abc import ABC, abstractmethod
from uuid import UUID
from typing import List, Optional


from auto_apply_app.domain.entities.user_fingerprint import UserFingerprint


class UserFingerprintRepository(ABC):
    """
    Repository for a user's pool of browser device personas.

    A user owns SEVERAL fingerprints, not one — the interface is plural
    throughout. `save` upserts on (user_id, slot), which is what lets a persona
    be updated in place; keying on the entity id alone cannot, because a
    regenerated entity carries a fresh uuid4.
    """

    @abstractmethod
    async def list_by_user(self, user_id: UUID, include_retired: bool = False) -> List[UserFingerprint]:
        """All personas for a user, ordered by slot. Retired ones excluded by default."""
        pass

    @abstractmethod
    async def get_by_user_and_board(self, user_id: UUID, board: str) -> Optional[UserFingerprint]:
        """The active persona pinned to this board, if one has been assigned."""
        pass

    @abstractmethod
    async def save(self, fingerprint: UserFingerprint) -> UserFingerprint:
        """Insert or update, keyed on (user_id, slot).

        Returns the persisted entity carrying the row's real id — callers need
        it, because the cookie jar and the sticky proxy session are both keyed
        on the persona id.
        """
        pass

    @abstractmethod
    async def touch_used(self, fingerprint_id: UUID) -> None:
        """Bump last_used_at and session_count. Field-scoped on purpose: a run
        marking a persona used must never clobber concurrent edits to the rest
        of the row."""
        pass

    @abstractmethod
    async def retire(self, fingerprint_id: UUID) -> None:
        """Mark a persona retired. It stops being selected but stays addressable
        so its cookie jar can be cleaned up."""
        pass

    @abstractmethod
    async def purge_retired(self, user_id: UUID, keep_last: int) -> None:
        """Hard-delete all but the `keep_last` most recent retired rows.

        Retirement is a soft delete kept only long enough to clean up the cookie
        jar keyed on that persona id; once that is done the row is dead weight.
        Under FINGERPRINT_MODE=per_run a row is retired on every single run, so
        without this the table grows without bound.
        """
        pass

    @abstractmethod
    async def delete(self, user_id: UUID) -> None:
        """Delete every persona for a user (e.g. when the user is deleted)."""
        pass
