# auto_apply_app/application/use_cases/agent_state_use_cases.py
import logging
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.domain.entities.agent_state import AgentState

logger = logging.getLogger(__name__)


@dataclass
class GetAgentStateUseCase:
    """
    Returns the kill-switch state for a user. Creates a default empty state
    if none exists (so callers never have to handle 'no state yet').
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_user_id(user_id)
                if state is None:
                    state = AgentState(user_id=user_id)
                return Result.success(state)
        except Exception:
            logger.exception("GetAgentStateUseCase failed for user_id=%s", user_id)
            return Result.failure(
                Error.system_error("Could not retrieve agent state.")
            )


@dataclass
class BindAgentToSearchUseCase:
    """
    Called at the start of a new agent run.
    Binds the user's kill-switch state to a specific search_id and clears
    any stale shutdown flag from a previous run.
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_user_id(user_id)
                if state is None:
                    state = AgentState(user_id=user_id)
                state.bind_to_search(search_id)
                await uow.agent_state_repo.save(state)
                return Result.success(state)
        except Exception:
            logger.exception(
                "BindAgentToSearchUseCase failed for user_id=%s search_id=%s",
                user_id, search_id,
            )
            return Result.failure(
                Error.system_error("Could not bind agent state.")
            )


@dataclass
class RequestAgentShutdownUseCase:
    """
    Called when a user clicks 'Stop' on a specific running search.
    The shutdown is rejected (no-op) if the bound search_id doesn't match —
    this prevents a stale shutdown signal from killing a fresh run.
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_user_id(user_id)
                if state is None:
                    return Result.failure(
                        Error.not_found("AgentState", str(user_id))
                    )
                applied = state.request_shutdown(search_id)
                if not applied:
                    return Result.failure(
                        Error.conflict(
                            "Shutdown rejected: this search is no longer active."
                        )
                    )
                await uow.agent_state_repo.save(state)
                return Result.success(state)
        except Exception:
            logger.exception(
                "RequestAgentShutdownUseCase failed for user_id=%s search_id=%s",
                user_id, search_id,
            )
            return Result.failure(
                Error.system_error("Could not request agent shutdown.")
            )


@dataclass
class IsAgentKilledForSearchUseCase:
    """
    Workers call this to check whether they should stop.
    Only returns True if the shutdown is bound to THIS specific search.
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_user_id(user_id)
                if state is None:
                    return Result.success(False)
                return Result.success(state.is_killed_for(search_id))
        except Exception:
            logger.exception(
                "IsAgentKilledForSearchUseCase failed for user_id=%s search_id=%s",
                user_id, search_id,
            )
            # Fail-closed: if we can't check, assume NOT killed and let the run continue.
            # Failing-open (assume killed) would create more outages than it prevents.
            return Result.success(False)