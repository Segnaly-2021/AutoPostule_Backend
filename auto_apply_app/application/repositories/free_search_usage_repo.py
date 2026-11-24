# ==============================================================================
# FILE: auto_apply_app/application/repositories/free_search_usage_repo.py
#
# WHAT IT DOES:
#   Interface for the daily free-search counter, one row per user and UTC date.
#
# USED BY:
#   - auto_apply_app/application/repositories/unit_of_work.py: exposes it as free_search_usage_repo
#   - auto_apply_app/infrastructures/persistence/database/repositories/free_search_usage_repo_db.py: implementation
#
# EXTERNAL DEPENDENCIES:
#   - None (standard library only)
# ==============================================================================
# auto_apply_app/application/repositories/free_search_usage_repo.py
from abc import ABC, abstractmethod
from uuid import UUID

from auto_apply_app.domain.entities.free_search_usage import FreeSearchUsage


class FreeSearchUsageRepository(ABC):
    """
    Repository for tracking daily free-search usage.
    
    One row per (user_id, usage_date) — UNIQUE constraint enforced at DB level.
    """

    @abstractmethod
    async def get_or_create_for_today(self, user_id: UUID) -> FreeSearchUsage:
        """
        Returns today's usage row for the given user (UTC date).
        If no row exists for today, creates one with searches_count=0.
        Atomic UPSERT — safe under concurrent calls.
        """
        pass

    @abstractmethod
    async def save(self, usage: FreeSearchUsage) -> None:
        """Persist changes to a FreeSearchUsage row."""
        pass