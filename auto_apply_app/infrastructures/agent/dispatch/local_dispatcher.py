import asyncio
import logging
from typing import Set
from uuid import UUID

from auto_apply_app.application.service_ports.dispatch_port import DispatchPort
from auto_apply_app.application.service_ports.agent_port import AgentServicePort
from auto_apply_app.application.service_ports.progress_broker_port import ProgressBrokerPort
from auto_apply_app.application.use_cases.run_context_use_cases import (
    LoadStartRunContextUseCase,
    LoadResumeRunContextUseCase,
)

logger = logging.getLogger(__name__)


def _system_error_frame(search_id: str) -> dict:
    # Mirrors the generic crash frame the old router emitted from agent_task.result().
    return {
        "source": "MASTER",
        "stage": "Failed",
        "node": "master",
        "status": "error",
        "error": "Something went wrong",
        "error_code": "SYSTEMERROR",
        "search_id": search_id,
    }


class LocalDispatcher(DispatchPort):
    """
    Phase A dispatcher: runs the agent in-process as a background task, publishing
    progress to the broker and exactly one _eot sentinel when the run ends.

    MUST be a singleton (see container). Background tasks it spawns outlive the HTTP
    request that triggered them, so the dispatcher (and the agent_service it holds)
    must not be request-scoped. The _tasks set keeps strong references so a running
    task is never garbage-collected mid-flight.
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
        self._tasks: Set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _make_callback(self, sid: str):
        broker = self._broker

        async def _callback(event: dict) -> None:
            await broker.publish(sid, event)

        return _callback

    async def dispatch_start(self, search_id: UUID, user_id: UUID) -> None:
        self._spawn(self._run_start(search_id, user_id))

    async def dispatch_resume(self, search_id: UUID, user_id: UUID, apply_all: bool) -> None:
        self._spawn(self._run_resume(search_id, user_id, apply_all))

    async def _run_start(self, search_id: UUID, user_id: UUID) -> None:
        sid = str(search_id)
        try:
            res = await self._load_start.execute(user_id, search_id)
            if not res.is_success:
                await self._broker.publish(sid, _system_error_frame(sid))
                return
            ctx = res.value
            await self._agent.run_job_search(
                user=ctx.user,
                search=ctx.search,
                subscription=ctx.subscription,
                preferences=ctx.preferences,
                credentials=ctx.credentials,
                progress_callback=self._make_callback(sid),
            )
        except Exception:
            logger.exception("LocalDispatcher start run crashed for %s", sid)
            await self._broker.publish(sid, _system_error_frame(sid))
        finally:
            await self._broker.publish_end(sid)  # exactly one sentinel, always

    async def _run_resume(self, search_id: UUID, user_id: UUID, apply_all: bool) -> None:
        sid = str(search_id)
        try:
            res = await self._load_resume.execute(user_id, search_id, apply_all)
            if not res.is_success:
                await self._broker.publish(sid, _system_error_frame(sid))
                return
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
        except Exception:
            logger.exception("LocalDispatcher resume run crashed for %s", sid)
            await self._broker.publish(sid, _system_error_frame(sid))
        finally:
            await self._broker.publish_end(sid)
