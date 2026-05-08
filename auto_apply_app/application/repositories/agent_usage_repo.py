# auto_apply_app/application/repositories/agent_usage_repo.py
from abc import ABC, abstractmethod
from uuid import UUID

from auto_apply_app.domain.entities.agent_usage import AgentUsage


class AgentUsageRepository(ABC):
    """
    Repository for tracking daily agent run usage.
    
    One row per (user_id, usage_date) — UNIQUE constraint enforced at DB level.
    """

    @abstractmethod
    async def get_or_create_for_today(self, user_id: UUID) -> AgentUsage:
        """
        Returns today's usage row for the given user (UTC date).
        If no row exists for today, creates one with runs_count=0.
        Atomic UPSERT — safe under concurrent calls.
        """
        pass

    @abstractmethod
    async def save(self, usage: AgentUsage) -> None:
        """Persist changes to an AgentUsage row."""
        pass