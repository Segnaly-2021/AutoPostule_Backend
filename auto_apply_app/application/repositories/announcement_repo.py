# auto_apply_app/application/repositories/announcement_repo.py
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional
from uuid import UUID

from auto_apply_app.domain.entities.announcement import Announcement


class AnnouncementRepository(ABC):
    """Release notices, and which of them each user has dismissed.

    Two audiences with opposite needs share this port. `list_active_for_user` is
    on the hot path -- every authenticated page load calls it -- and must return
    only what that user has not already seen. The admin methods are cold, called
    by one person, and must return drafts and expired rows too, because an admin
    who cannot see a draft cannot finish writing it.
    """

    # --- Read path (every logged-in user) ---

    @abstractmethod
    async def list_active_for_user(
        self, user_id: UUID, moment: datetime
    ) -> list[Announcement]:
        """Published, inside its window at `moment`, and not dismissed by this user.

        The dismissal filter belongs in the query rather than in the caller: the
        alternative is fetching every live announcement and every view row on
        each page load to subtract them in Python.
        """
        pass

    @abstractmethod
    async def dismiss(self, user_id: UUID, announcement_id: UUID) -> bool:
        """Record that this user closed this announcement. Idempotent.

        Returns False when it was already dismissed. Relies on the UNIQUE on
        (user_id, announcement_id) rather than a read-then-write, so a
        double-click or a dismiss racing a reload cannot insert twice.
        """
        pass

    # --- Admin path ---

    @abstractmethod
    async def get(self, announcement_id: UUID) -> Optional[Announcement]:
        pass

    @abstractmethod
    async def list_all(self, limit: int = 100) -> list[Announcement]:
        """Everything, newest first, drafts and expired included."""
        pass

    @abstractmethod
    async def save(self, announcement: Announcement) -> Announcement:
        """Create or update. The entity validates itself; this only persists."""
        pass

    @abstractmethod
    async def delete(self, announcement_id: UUID) -> bool:
        """Hard delete, cascading to its dismissals.

        Exists for a mistake caught before anyone saw it. Retiring a notice people
        HAVE seen is `unpublish`, which keeps the dismissals so re-publishing does
        not re-show it to them.
        """
        pass
