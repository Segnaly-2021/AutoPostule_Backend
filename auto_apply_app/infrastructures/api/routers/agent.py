# auto_apply_app/infrastructures/api/routers/agent.py
from fastapi import APIRouter, status, Depends
from fastapi.responses import StreamingResponse
from typing import Annotated, List
import asyncio
import json
import dataclasses # Added for safe SSE serialization

from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.interfaces.controllers.agent_controllers import AgentController
from auto_apply_app.interfaces.controllers.agent_state_controllers import AgentStateController
from auto_apply_app.infrastructures.api.schema.agent_schema import (
    StartAgentRequest, 
    ResumeAgentRequest,
    AgentViewModel,
)
from auto_apply_app.interfaces.viewmodels.job_offer_vm import JobReviewViewModel

router = APIRouter()


def get_agent_controller(
    container: Annotated[Application, Depends(get_container)]
) -> AgentController:
    """Extract AgentController from the application container."""
    return container.agent_controller

def get_agent_state_controller(
    container: Annotated[Application, Depends(get_container)]
) -> AgentStateController:
    return container.agent_state_controller

AgentControllerDep = Annotated[AgentController, Depends(get_agent_controller)]


# ============================================================================
# START AGENT ENDPOINTS
# ============================================================================

@router.post(
    "/start",
    response_model=AgentViewModel,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start parallel job search agent (non-streaming)",
    description="Launch the automated job search agent across multiple platforms"
)
async def start_job_search_agent(
    data: StartAgentRequest,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep
):
    """
    Start the parallel job search agent for the authenticated user.
    """
    result = await controller.handle_start_agent(
        user_id=current_user_id,
        job_title=data.job_title,
        job_boards=data.job_boards, # 🚨 V2 UPDATE: Plural List
        resume_path=data.resume_path,
        contract_types=data.contract_types,
        location=data.location,
        min_salary=data.min_salary,
    )
    return handle_result(result)


@router.post("/start/stream")
async def start_job_search_agent_stream(
    data: StartAgentRequest,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep
):
    """
    Start the parallel job search agent and stream real-time progress.
    """
    queue = asyncio.Queue()

    async def send_progress(progress_data):
        # 🚨 V2 Safety: Safely convert ViewModel dataclasses to dicts for JSON
        if dataclasses.is_dataclass(progress_data):
            progress_data = dataclasses.asdict(progress_data)
        await queue.put(progress_data)

    async def event_generator():
        agent_task = asyncio.create_task(
            controller.handle_start_agent(
                user_id=current_user_id,
                job_title=data.job_title,
                job_boards=data.job_boards,
                resume_path=data.resume_path,
                contract_types=data.contract_types,
                location=data.location,
                min_salary=data.min_salary,
                progress_callback=send_progress
            )
        )

        while not agent_task.done():
            try:
                progress = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield f"data: {json.dumps(progress)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

        # Drain any remaining events before closing
        while not queue.empty():
            progress = queue.get_nowait()
            yield f"data: {json.dumps(progress)}\n\n"

        # ← Safe exception handling — never let agent_task.result() crash the generator
        try:
            result = agent_task.result()
            if result.is_success:
                yield f"data: {json.dumps({'source': 'MASTER', 'stage': 'Complete', 'status': 'success'})}\n\n"
            else:
                yield f"data: {json.dumps({'source': 'MASTER', 'stage': 'Failed', 'status': 'error', 'error': str(result.error)})}\n\n"
        except Exception as e:
            # Task raised an unhandled exception — send error event instead of crashing
            print(f"🚨 Agent task raised exception: {e}")
            yield f"data: {json.dumps({'source': 'MASTER', 'stage': 'Failed', 'status': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    )


# ============================================================================
# RESUME AGENT ENDPOINTS (Premium Users)
# ============================================================================

@router.post(
    "/{search_id}/resume",
    response_model=AgentViewModel,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resume paused workflow (non-streaming)",
    description="Resume a paused job search to submit approved applications"
)
async def resume_job_search_agent(
    search_id: str,
    data: ResumeAgentRequest,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep
):
    result = await controller.handle_resume_agent(
        user_id=current_user_id,
        search_id=search_id,
        apply_all=data.apply_all
    )
    return handle_result(result)


@router.post("/{search_id}/resume/stream")
async def resume_job_search_agent_stream(
    search_id: str,
    data: ResumeAgentRequest,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep
):
    queue = asyncio.Queue()

    async def send_progress(progress_data):
        if dataclasses.is_dataclass(progress_data):
            progress_data = dataclasses.asdict(progress_data)
        await queue.put(progress_data)

    async def event_generator():
        agent_task = asyncio.create_task(
            controller.handle_resume_agent(
                user_id=current_user_id,
                search_id=search_id,
                apply_all=data.apply_all,
                progress_callback=send_progress
            )
        )

        while not agent_task.done():
            try:
                progress = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield f"data: {json.dumps(progress)}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"

        while not queue.empty():
            progress = queue.get_nowait()
            yield f"data: {json.dumps(progress)}\n\n"

        result = agent_task.result()
        if result.is_success:
            yield f"data: {json.dumps({'source': 'MASTER', 'stage': 'Complete', 'status': 'success', 'search_id': search_id})}\n\n"
        else:
            yield f"data: {json.dumps({'source': 'MASTER', 'stage': 'Failed', 'status': 'error', 'error': str(result.error)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"}
    )

# ============================================================================
# PREMIUM REVIEW ENDPOINTS
# ============================================================================

@router.get(
    "/{search_id}/jobs",
    response_model=List[JobReviewViewModel],
    status_code=status.HTTP_200_OK,
    summary="Get jobs pending review (Premium)",
)
async def get_jobs_for_review(
    search_id: str,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep
):
    result = await controller.handle_get_jobs_for_review(
        user_id=current_user_id,
        search_id=search_id
    )
    return handle_result(result)


@router.patch(
    "/jobs/{job_id}/cover-letter",
    status_code=status.HTTP_200_OK,
    summary="Update cover letter",
)
async def update_cover_letter(
    job_id: str,
    data: dict,  
    current_user_id: CurrentUserId,
    controller: AgentControllerDep
):
    result = await controller.handle_update_cover_letter(
        user_id=current_user_id,
        job_id=job_id,
        cover_letter=data.get("cover_letter")
    )
    return handle_result(result)


@router.post(
    "/jobs/{job_id}/approve",
    status_code=status.HTTP_200_OK,
    summary="Approve single job",
)
async def approve_job(
    job_id: str,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep
):
    result = await controller.handle_approve_job(
        user_id=current_user_id,
        job_id=job_id
    )
    return handle_result(result)


@router.delete(
    "/jobs/{job_id}",
    status_code=status.HTTP_200_OK,
    summary="Discard job",
)
async def discard_job(
    job_id: str,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep
):
    result = await controller.handle_discard_job(
        user_id=current_user_id,
        job_id=job_id
    )
    return handle_result(result)