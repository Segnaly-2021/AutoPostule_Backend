# auto_apply_app/application/use_cases/agent_state_use_cases.py
from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.domain.entities.agent_state import AgentState
from auto_apply_app.application.repositories.unit_of_work import UnitOfWork
from auto_apply_app.application.common.result import Result, Error


@dataclass
class GetAgentStateUseCase:
    uow: UnitOfWork

    async def execute(self, user_id: UUID) -> Result[AgentState]:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_user_id(user_id)
                if not state:
                    state = AgentState(user_id=user_id)
                return Result.success(state)

        except Exception as e:
            return Result.failure(Error.system_error(str(e)))


@dataclass
class ShutdownAgentUseCase:
    uow: UnitOfWork

    async def execute(self, user_id: UUID) -> Result[dict]:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_user_id(user_id)
                if not state:
                    state = AgentState(user_id=user_id)

                state.shutdown()

                await uow.agent_state_repo.save(state)
                await uow.commit()
                return Result.success({"message": "Agent shutdown successfully."})

        except Exception as e:
            return Result.failure(Error.system_error(str(e)))


@dataclass
class ResetAgentUseCase:
    uow: UnitOfWork

    async def execute(self, user_id: UUID) -> Result[dict]:
        try:
            async with self.uow as uow:
                state = await uow.agent_state_repo.get_by_user_id(user_id)
                if not state:
                    state = AgentState(user_id=user_id)

                state.reset()

                await uow.agent_state_repo.save(state)
                await uow.commit()
                return Result.success({"message": "Agent reset successfully."})

        except Exception as e:
            return Result.failure(Error.system_error(str(e)))