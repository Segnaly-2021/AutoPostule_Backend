# auto_apply_app/infrastructures/api/routers/agent.py
from fastapi import APIRouter, status, Depends
from fastapi.responses import StreamingResponse
from typing import Annotated, List
import json
import logging

from auto_apply_app.infrastructures.api.dependencies.result import handle_result
from auto_apply_app.infrastructures.api.dependencies.auth_deps import CurrentUserId
from auto_apply_app.infrastructures.api.dependencies.container_dep import get_container
from auto_apply_app.infrastructures.configuration.container import Application
from auto_apply_app.application.service_ports.progress_broker_port import ProgressBrokerPort
from auto_apply_app.interfaces.controllers.agent_controllers import AgentController
from auto_apply_app.interfaces.controllers.agent_state_controllers import AgentStateController
from auto_apply_app.infrastructures.api.schema.agent_schema import (
    StartAgentRequest, 
    ResumeAgentRequest,
    AgentViewModel,
)
from auto_apply_app.interfaces.viewmodels.job_offer_vm import JobReviewViewModel
from auto_apply_app.interfaces.viewmodels.job_search_vm import JobSearchSummaryViewModel, SearchStatusViewModel
from auto_apply_app.interfaces.viewmodels.agent_state_vm import AgentLivenessViewModel


logger = logging.getLogger(__name__)

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


def get_progress_broker(
    container: Annotated[Application, Depends(get_container)]
) -> ProgressBrokerPort:
    return container.progress_broker

BrokerDep = Annotated[ProgressBrokerPort, Depends(get_progress_broker)]


def _sse_error(op_result) -> str:
    """Build an SSE error frame. Used when prep/dispatch fails — we cannot call
    handle_result mid-stream, it raises HTTPException."""
    err = op_result.error  # ErrorViewModel-like with .message/.code
    msg = getattr(err, "message", None) or "An unexpected error occurred."
    code = getattr(err, "code", None) or "SYSTEMERROR"
    payload = {"source": "MASTER", "stage": "Failed", "status": "error",
               "error": msg, "error_code": code}
    return f"data: {json.dumps(payload)}\n\n"


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
    prep = await controller.prepare_start_agent(
        user_id=current_user_id,
        job_title=data.job_title,
        job_boards=data.job_boards, # 🚨 V2 UPDATE: Plural List
        resume_path=data.resume_path,
        contract_types=data.contract_types,
        location=data.location,
        min_salary=data.min_salary,
    )
    if not prep.is_success:
        return handle_result(prep)
    await controller.dispatch_start_agent(
        user_id=current_user_id, search_id=prep.success.search_id
    )
    return handle_result(prep)   # AgentViewModel, status "prepared"


@router.post("/start/stream")
async def start_job_search_agent_stream(
    data: StartAgentRequest,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep,
    broker: BrokerDep,
):
    """
    Start the parallel job search agent and stream real-time progress.
    """
    async def event_generator():
        # 1. PREP — creates JobSearch + ALIVE AgentState, returns search_id.
        prep = await controller.prepare_start_agent(
            user_id=current_user_id,
            job_title=data.job_title,
            job_boards=data.job_boards,
            resume_path=data.resume_path,
            contract_types=data.contract_types,
            location=data.location,
            min_salary=data.min_salary,
        )
        if not prep.is_success:
            yield _sse_error(prep)
            return
        search_id = prep.success.search_id

        print(f"DEBUG: API Router: search_id type: {type(search_id)}; value: {search_id}")

        # 2. SUBSCRIBE BEFORE DISPATCH — no lost frames.
        frames = broker.stream(search_id)

        # 3. DISPATCH the background run.
        disp = await controller.dispatch_start_agent(
            user_id=current_user_id, search_id=search_id
        )
        if not disp.is_success:
            yield _sse_error(disp)
            return

        # 4. RELAY until the sentinel ends the iterator.
        async for frame in frames:
            if frame is None:
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(frame)}\n\n"

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
    prep = await controller.prepare_resume_agent(
        user_id=current_user_id,
        search_id=search_id,
        apply_all=data.apply_all,
    )
    if not prep.is_success:
        return handle_result(prep)
    await controller.dispatch_resume_agent(
        user_id=current_user_id, search_id=search_id, apply_all=data.apply_all
    )
    return handle_result(prep)   # AgentViewModel, status "prepared"


@router.post("/{search_id}/resume/stream")
async def resume_job_search_agent_stream(
    search_id: str,
    data: ResumeAgentRequest,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep,
    broker: BrokerDep,
):
    async def event_generator():
        # 1. PREP — approves drafts, returns search_id.
        prep = await controller.prepare_resume_agent(
            user_id=current_user_id,
            search_id=search_id,
            apply_all=data.apply_all,
        )
        if not prep.is_success:
            yield _sse_error(prep)
            return
        sid = prep.success.search_id

        # 2. SUBSCRIBE BEFORE DISPATCH — no lost frames.
        frames = broker.stream(sid)

        # 3. DISPATCH the background run.
        disp = await controller.dispatch_resume_agent(
            user_id=current_user_id, search_id=sid, apply_all=data.apply_all
        )
        if not disp.is_success:
            yield _sse_error(disp)
            return

        # 4. RELAY until the sentinel ends the iterator.
        async for frame in frames:
            if frame is None:
                yield ": heartbeat\n\n"
                continue
            yield f"data: {json.dumps(frame)}\n\n"

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


@router.get(
    "/{search_id}/status",
    response_model=SearchStatusViewModel,
    status_code=status.HTTP_200_OK,
    summary="Get the terminal status of a search (complete vs failed)",
)
async def get_search_status(
    search_id: str,
    current_user_id: CurrentUserId,
    controller: AgentControllerDep,
):
    result = await controller.handle_get_search_status(
        user_id=current_user_id,
        search_id=search_id,
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



@router.get(
    "/recent-searches",
    response_model=List[JobSearchSummaryViewModel],
    status_code=status.HTTP_200_OK,
    summary="List recent active (SEARCHING) job searches",
    description="Returns the authenticated user's 5 most recent searches with SEARCHING status.",
)
async def list_recent_searches(
    current_user_id: CurrentUserId,
    controller: AgentControllerDep,
    limit: int = 5,
):
    result = await controller.handle_list_recent_searches(
        user_id=current_user_id,
        limit=limit,
    )
    return handle_result(result)


# ============================================================================
# AGENT LIVENESS (SSE reconnection poll)
# ============================================================================

@router.get(
    "/{search_id}/liveness",
    response_model=AgentLivenessViewModel,
    status_code=status.HTTP_200_OK,
    summary="Poll whether the agent for this search is still alive",
)
async def get_agent_liveness(
    search_id: str,
    current_user_id: CurrentUserId,
    container: Annotated[Application, Depends(get_container)],
):
    controller = container.agent_state_controller
    result = await controller.handle_get_liveness(
        user_id=current_user_id,
        search_id=search_id,
    )
    return handle_result(result)