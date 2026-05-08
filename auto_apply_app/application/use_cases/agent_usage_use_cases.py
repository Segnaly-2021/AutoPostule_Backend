# auto_apply_app/application/use_cases/agent_usage_use_cases.py
import logging
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)


@dataclass
class CompleteAgentRunUseCase:
    """
    Called by the MasterAgent when a search transitions to its successful
    terminal state (NOT for kills, NOT for 'no jobs found').
    
    Performs two writes in a single transaction:
    1. JobSearch.complete_search() + save  → fixes the bug where searches
       were stuck in SEARCHING status forever.
    2. AgentUsage.record_completed_run() + save  → drives the daily quota
       and exponential cooldown.
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                # 1. Mark the search complete
                search = await uow.search_repo.get(search_id)
                if search is None:
                    return Result.failure(
                        Error.not_found("JobSearch", str(search_id))
                    )

                # Defensive: only transition if it makes sense.
                # complete_search() will raise if status isn't SEARCHING.
                try:
                    search.complete_search()
                    await uow.search_repo.save(search)
                except ValueError:
                    # Already completed/cancelled — log but don't fail the use case.
                    # We still want to record the usage (the agent ran).
                    logger.warning(
                        "Search %s was not in SEARCHING state when "
                        "CompleteAgentRunUseCase fired; skipping status transition.",
                        search_id,
                    )

                # 2. Record the usage
                usage = await uow.agent_usage_repo.get_or_create_for_today(user_id)
                usage.record_completed_run()
                await uow.agent_usage_repo.save(usage)

                return Result.success({
                    "search_id": str(search_id),
                    "runs_today": usage.runs_count,
                })
        except Exception:
            logger.exception(
                "CompleteAgentRunUseCase failed for user_id=%s search_id=%s",
                user_id, search_id,
            )
            return Result.failure(
                Error.system_error("Could not finalize agent run.")
            )