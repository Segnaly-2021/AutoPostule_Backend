# auto_apply_app/infrastructures/api/routers/agent_state.py
from fastapi import APIRouter, status, Depends
from typing import Annotated

from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.agent_state_controllers import AgentStateController

router = APIRouter()


def get_agent_state_controller(
    container: Annotated[Application, Depends(get_container)]
) -> AgentStateController:
    return container.agent_state_controller


AgentStateControllerDep = Annotated[AgentStateController, Depends(get_agent_state_controller)]


@router.get(
    "/",
    status_code=status.HTTP_200_OK,
    summary="Get current agent state",
)
async def get_agent_state(
    current_user_id: CurrentUserId,
    controller: AgentStateControllerDep
):
    result = await controller.handle_get(user_id=current_user_id)
    return handle_result(result)


@router.post(
    "/shutdown",
    status_code=status.HTTP_200_OK,
    summary="Shutdown agent — sets is_shutdown to True",
)
async def shutdown_agent(
    current_user_id: CurrentUserId,
    controller: AgentStateControllerDep
):
    result = await controller.handle_shutdown(user_id=current_user_id)
    return handle_result(result)


@router.post(
    "/reset",
    status_code=status.HTTP_200_OK,
    summary="Reset agent state — sets is_shutdown to False",
)
async def reset_agent(
    current_user_id: CurrentUserId,
    controller: AgentStateControllerDep
):
    result = await controller.handle_reset(user_id=current_user_id)
    return handle_result(result)