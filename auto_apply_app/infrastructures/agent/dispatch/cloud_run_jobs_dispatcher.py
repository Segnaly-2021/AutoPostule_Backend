import logging
import asyncio
from uuid import UUID
from typing import Optional

from google.cloud import run_v2

from auto_apply_app.application.service_ports.dispatch_port import DispatchPort

logger = logging.getLogger(__name__)


class CloudRunJobsDispatcher(DispatchPort):
    """
    Prod dispatcher: triggers the heavy-agent Cloud Run Job, passing the run's ids
    as per-execution env overrides. Auth via ADC = the API's runtime SA, which holds
    run.jobs.run (granted during the SA setup).

    Fire-and-forget: it triggers the Job and returns immediately. It does NOT await
    the Job to finish — progress flows back over the Redis relay (RedisProgressBroker
    shared via REDIS_URL), and liveness is tracked via the AgentState heartbeat.
    """

    def __init__(self, project: str, region: str, job_name: str, client=None):
        self._job_path = f"projects/{project}/locations/{region}/jobs/{job_name}"
        # Async client so dispatch_* stays non-blocking on the API event loop.
        self._client = client or run_v2.JobsClient()

    async def dispatch_start(self, search_id: UUID, user_id: UUID) -> None:
        await self._trigger(action="start", search_id=search_id, user_id=user_id)

    async def dispatch_resume(self, search_id: UUID, user_id: UUID, apply_all: bool) -> None:
        await self._trigger(action="resume", search_id=search_id, user_id=user_id,
                            apply_all=apply_all)

    async def _trigger(self, action: str, search_id: UUID, user_id: UUID,
                       apply_all: Optional[bool] = None) -> None:
        # Env var NAMES here MUST match worker_main.py exactly.
        env = [
            run_v2.EnvVar(name="AGENT_ACTION", value=action),
            run_v2.EnvVar(name="AGENT_SEARCH_ID", value=str(search_id)),
            run_v2.EnvVar(name="AGENT_USER_ID", value=str(user_id)),
        ]
        if apply_all is not None:
            env.append(run_v2.EnvVar(name="AGENT_APPLY_ALL", value=str(apply_all).lower()))

        overrides = run_v2.RunJobRequest.Overrides(
            container_overrides=[
                run_v2.RunJobRequest.Overrides.ContainerOverride(env=env)
            ]
        )
        request = run_v2.RunJobRequest(name=self._job_path, overrides=overrides)

        # Trigger only. Do NOT await the returned operation's .result() — the run is
        # tracked via Redis progress + the AgentState heartbeat, not this call.
        await asyncio.to_thread(self._client.run_job, request=request)
        logger.info("Dispatched agent Job: action=%s search=%s", action, search_id)
