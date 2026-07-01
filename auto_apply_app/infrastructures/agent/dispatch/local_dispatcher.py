import asyncio
import logging
from typing import Set
from uuid import UUID

from auto_apply_app.application.service_ports.dispatch_port import DispatchPort
from auto_apply_app.infrastructures.agent.runner import AgentRunner

logger = logging.getLogger(__name__)


class LocalDispatcher(DispatchPort):
    """
    Phase A / dev dispatcher: runs the agent IN THIS PROCESS as a background
    asyncio task, via the shared AgentRunner. Used for MEMORY mode and local
    development where triggering a real Cloud Run Job is undesirable. In prod,
    CloudRunJobsDispatcher (same DispatchPort) runs the agent in a separate
    Cloud Run Job.

    "Local" = local to this process (in-process), NOT "local/dev environment".

    MUST be a singleton (see container). Background tasks it spawns outlive the
    HTTP request that triggered them, so the dispatcher must not be request-scoped.
    The _tasks set keeps strong references so a running task is never
    garbage-collected mid-flight.
    """

    def __init__(self, runner: AgentRunner):
        self._runner = runner
        self._tasks: Set[asyncio.Task] = set()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def dispatch_start(self, search_id: UUID, user_id: UUID) -> None:
        self._spawn(self._runner.run_start(search_id, user_id))

    async def dispatch_resume(self, search_id: UUID, user_id: UUID, apply_all: bool) -> None:
        self._spawn(self._runner.run_resume(search_id, user_id, apply_all))
