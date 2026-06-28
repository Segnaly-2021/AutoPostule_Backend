# auto_apply_app/application/use_cases/agent_state_use_cases.py
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from uuid import UUID

from auto_apply_app.application.common.result import Result, Error
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.domain.entities.agent_state import AgentState

logger = logging.getLogger(__name__)

# Longest single worker node (~120s WTTJ nav) + margin. See HANDOFF rationale:
# the cutoff MUST exceed the longest gap between two heartbeat writes, or a
# healthy agent mid-form-fill would be declared dead. Do NOT lower to 60s.
AGENT_HEARTBEAT_STALE_SECONDS = 180


@dataclass
class GetAgentStateUseCase:
    """
    Returns the kill-switch state for a specific search.
    Returns failure if no state exists for that search.
    """
    uow: UnitOfWork

    async def execute(self, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_search_id(search_id)
                if state is None:
                    return Result.failure(
                        Error.not_found("AgentState", str(search_id))
                    )
                return Result.success(state)
        except Exception:
            logger.exception("GetAgentStateUseCase failed for search_id=%s", search_id)
            return Result.failure(
                Error.system_error("Could not retrieve agent state.")
            )



@dataclass
class CreateAgentStateForSearchUseCase:
    """
    Called at the start of a new agent run.
    Creates a fresh kill-switch row for this specific (user, search),
    or retrieves it if it already exists (Get-or-Create pattern).
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                # 1. Try to get existing state first
                existing_state = await uow.agent_state_repo.get_by_search_id(search_id)
                
                if existing_state:                        
                    return Result.success(existing_state)

                # 2. If it doesn't exist, create it.
                # Seed an initial heartbeat so the row is "alive" from t=0;
                # otherwise last_heartbeat is NULL until the first node beats and
                # a fast first liveness poll would read "dead".
                state = AgentState(user_id=user_id, search_id=search_id)
                state.beat()
                await uow.agent_state_repo.save(state)
                return Result.success(state)
                
        except Exception:
            logger.exception(
                "CreateAgentStateForSearchUseCase failed for user_id=%s search_id=%s",
                user_id, search_id,
            )
            return Result.failure(
                Error.system_error("Could not create agent state.")
            )

@dataclass
class RequestAgentShutdownUseCase:
    """
    Called when a user clicks 'Stop' on a specific running search.
    Returns 404 if the search has no agent state row (search never started
    or was already cleaned up).
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_search_id(search_id)
                if state is None:
                    return Result.failure(
                        Error.not_found("AgentState", str(search_id))
                    )
                # Authorization: user can only stop their own searches
                if state.user_id != user_id:
                    return Result.failure(
                        Error.unauthorized("You do not own this search.")
                    )
                state.shutdown()
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
    Workers call this on every node exit to check whether to abort.
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_search_id(search_id)
                if state is None:
                    # No state row → not killed, but also weird. Log it.
                    logger.warning(
                        "IsAgentKilledForSearchUseCase: no state row for search_id=%s",
                        search_id,
                    )
                    return Result.success(False)
                return Result.success(state.is_shutdown)
        except Exception:
            logger.exception(
                "IsAgentKilledForSearchUseCase failed for user_id=%s search_id=%s",
                user_id, search_id,
            )
            # Fail-closed: if we can't check, assume NOT killed.
            return Result.success(False)


@dataclass
class HeartbeatAgentForSearchUseCase:
    """
    Called by workers/master at node entry and inside long loops to mark the
    agent as alive. Fail-soft: never raises, never aborts the run.
    """
    uow: UnitOfWork

    async def execute(self, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_search_id(search_id)
                if state is None:
                    # No row yet (shouldn't happen mid-run) — don't error the run.
                    return Result.success(False)
                state.beat()
                await uow.agent_state_repo.save(state)
                return Result.success(True)
        except Exception:
            logger.exception(
                "HeartbeatAgentForSearchUseCase failed for search_id=%s", search_id
            )
            # Fail-soft: a missed heartbeat is not fatal.
            return Result.success(False)


@dataclass
class GetAgentLivenessForSearchUseCase:
    """
    Returns liveness for a specific search, for the reconnection poll.
    'alive' = a heartbeat exists AND is newer than the stale threshold.
    Includes is_shutdown so the frontend can distinguish a user-stop.
    """
    uow: UnitOfWork

    async def execute(self, user_id: UUID, search_id: UUID) -> Result:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_search_id(search_id)
                if state is None:
                    return Result.failure(
                        Error.not_found("AgentState", str(search_id))
                    )
                if state.user_id != user_id:
                    return Result.failure(
                        Error.unauthorized("You do not own this search.")
                    )

                now = datetime.now(timezone.utc)
                hb = state.last_heartbeat
                # Guard against naive datetimes coming back from the driver.
                if hb is not None and hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)

                is_alive = (
                    hb is not None
                    and (now - hb) <= timedelta(seconds=AGENT_HEARTBEAT_STALE_SECONDS)
                )

                return Result.success({
                    "search_id": str(search_id),
                    "is_shutdown": state.is_shutdown,
                    "is_alive": is_alive,
                    "last_heartbeat": hb.isoformat() if hb else None,
                })
        except Exception:
            logger.exception(
                "GetAgentLivenessForSearchUseCase failed for search_id=%s", search_id
            )
            return Result.failure(
                Error.system_error("Could not retrieve agent liveness.")
            )