# auto_apply_app/application/use_cases/free_search_use_cases.py
import logging
from dataclasses import dataclass
from typing import Any

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.dtos.free_search_dtos import FreeSearchRequest

logger = logging.getLogger(__name__)


@dataclass
class FreeSearchUseCase:
    """
    Authenticated free-tier job search.

    Flow:
    1. Pre-check: can this user run a free search today?
    2. Run the scraping (outside UoW — slow, no DB needed)
    3. On success: increment the daily counter (separate UoW)

    Note on the count-after-success pattern: between step 1 and step 3
    a user could fire many requests in parallel and all pass the check
    before any get recorded. This is acceptable because:
    - The frontend overlay prevents concurrent submissions per user
    - IP-level rate limiting (slowapi) catches bot abuse
    Failed scrapes don't count against the user's quota.
    """
    uow: UnitOfWork
    fake_agent: Any  # FakeMasterAgent — typed as Any to keep the application
                     # layer free of infrastructure imports

    async def execute(self, request: FreeSearchRequest) -> Result:
        try:
            params = request.to_execution_params()
            user_id = params["user_id"]
            query = params["query"]
            target_count = params["target_count"]

            # 1. Pre-check daily quota
            async with self.uow as uow:
                usage = await uow.free_search_usage_repo.get_or_create_for_today(user_id)
                allowed, reason = usage.can_run()
                if not allowed:
                    return Result.failure(Error.too_many_requests(reason))

            # 2. Run the scraping (outside UoW — long-running)
            search_output = await self.fake_agent.search_all_boards(query, target_count)

            # 3. If scraping itself failed at the agent level, don't count it
            if search_output.get("status") == "error":
                return Result.failure(Error.system_error(
                    "Search failed. Please try again."
                ))

            # 4. Record usage on success (separate UoW)
            async with self.uow as uow:
                usage = await uow.free_search_usage_repo.get_or_create_for_today(user_id)
                usage.record()
                await uow.free_search_usage_repo.save(usage)

            return Result.success(search_output)

        except Exception:
            logger.exception("FreeSearchUseCase failed")
            return Result.failure(
                Error.system_error("Could not complete the free search.")
            )