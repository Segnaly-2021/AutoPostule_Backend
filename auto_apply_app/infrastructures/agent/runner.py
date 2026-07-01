import logging
from uuid import UUID

from auto_apply_app.application.service_ports.agent_port import AgentServicePort
from auto_apply_app.application.service_ports.progress_broker_port import ProgressBrokerPort
from auto_apply_app.application.use_cases.run_context_use_cases import (
    LoadStartRunContextUseCase,
    LoadResumeRunContextUseCase,
)

logger = logging.getLogger(__name__)


def _system_error_frame(search_id: str, detail: str = "Something went wrong") -> dict:
    """
    Generic terminal error frame, published when the run fails before (or instead
    of) the agent emitting its own error. `detail` carries the real reason to the
    browser inspector so failures are never silent — read it in the SSE `error`
    field. Keep `error_code` = SYSTEMERROR so the frontend treats it as fatal.
    """
    return {
        "source": "MASTER",
        "stage": "Failed",
        "node": "master",
        "status": "error",
        "error": str(detail),
        "error_code": "SYSTEMERROR",
        "search_id": search_id,
    }


def _reason(result) -> str:
    """
    Best-effort extraction of a human reason from a failed Result, tolerant of
    whatever the Error object exposes (.message / .msg / str). Never raises — a
    diagnostic helper must not itself crash the error path.
    """
    err = getattr(result, "error", None)
    if err is None:
        return "unknown error"
    for attr in ("message", "msg", "detail", "description"):
        val = getattr(err, attr, None)
        if val:
            return str(val)
    return str(err)


class AgentRunner:
    """
    The actual load -> run -> publish -> sentinel sequence for ONE agent execution.

    Process-agnostic: invoked in-process by LocalDispatcher (dev/MEMORY) and as a
    one-shot by worker_main.py inside a Cloud Run Job (prod). SINGLE source of truth
    for how a run executes — never duplicate this logic anywhere else.

    run_start / run_resume return True on a clean agent run, False on any failure.
    Either way they ALWAYS publish exactly one _eot sentinel (finally).
    """

    def __init__(
        self,
        agent_service: AgentServicePort,
        broker: ProgressBrokerPort,
        load_start_ctx: LoadStartRunContextUseCase,
        load_resume_ctx: LoadResumeRunContextUseCase,
    ):
        self._agent = agent_service
        self._broker = broker
        self._load_start = load_start_ctx
        self._load_resume = load_resume_ctx

    def _make_callback(self, sid: str):
        broker = self._broker

        async def _callback(event: dict) -> None:
            await broker.publish(sid, event)

        return _callback

    async def run_start(self, search_id: UUID, user_id: UUID) -> bool:
        sid = str(search_id)
        try:
            res = await self._load_start.execute(user_id=user_id, search_id=search_id)
            if not res.is_success:                       # Result accessor — is_success/value/error
                reason = _reason(res)
                logger.error("AgentRunner start: load context failed for %s: %s", sid, reason)
                await self._broker.publish(sid, _system_error_frame(sid, f"load_ctx: {reason}"))
                return False

            ctx = res.value
            await self._agent.run_job_search(
                user=ctx.user,
                search=ctx.search,
                subscription=ctx.subscription,
                preferences=ctx.preferences,
                credentials=ctx.credentials,
                progress_callback=self._make_callback(sid),
            )
            return True
        except Exception as e:
            logger.exception("AgentRunner start run crashed for %s", sid)
            await self._broker.publish(sid, _system_error_frame(sid, f"run crashed: {e!r}"))
            return False
        finally:
            await self._broker.publish_end(sid)  # exactly one sentinel, always

    async def run_resume(self, search_id: UUID, user_id: UUID, apply_all: bool) -> bool:
        sid = str(search_id)
        try:
            res = await self._load_resume.execute(user_id, search_id, apply_all)
            if not res.is_success:
                reason = _reason(res)
                logger.error("AgentRunner resume: load context failed for %s: %s", sid, reason)
                await self._broker.publish(sid, _system_error_frame(sid, f"load_ctx: {reason}"))
                return False

            ctx = res.value
            await self._agent.resume_job_search(
                user=ctx.user,
                search=ctx.search,
                subscription=ctx.subscription,
                preferences=ctx.preferences,
                approved_jobs=ctx.approved_jobs,
                credentials=ctx.credentials,
                progress_callback=self._make_callback(sid),
            )
            return True
        except Exception as e:
            logger.exception("AgentRunner resume run crashed for %s", sid)
            await self._broker.publish(sid, _system_error_frame(sid, f"run crashed: {e!r}"))
            return False
        finally:
            await self._broker.publish_end(sid)
