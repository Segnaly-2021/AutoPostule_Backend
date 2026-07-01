"""
Cloud Run Job entrypoint for the heavy agent (Phase B-1).

Wakes per run, reads the run's ids from env, builds the worker container (the SAME
AgentRunner wiring the API uses, via Application.agent_runner), executes ONE agent
run, and exits with a status code. Progress flows back to the API over the Redis
relay (RedisProgressBroker) — see PHASE_B1_HANDOFF.md §3.

Launched as:  python -m auto_apply_app.worker_main
"""

import asyncio
import logging
import os
import signal
import sys
from uuid import UUID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker_main")


async def _amain() -> int:
    action = os.environ["AGENT_ACTION"]
    search_id = UUID(os.environ["AGENT_SEARCH_ID"])
    user_id = UUID(os.environ["AGENT_USER_ID"])
    apply_all = os.environ.get("AGENT_APPLY_ALL", "true").lower() == "true"

    logger.info("Worker start: action=%s search=%s user=%s", action, search_id, user_id)

    # Reuse the container's runner wiring. create_worker_application is SYNC and
    # builds only the agent-relevant deps (the full create_application requires
    # web-only adapters that raise without JWT_SECRET / TURNSTILE_SECRET_KEY,
    # which the Job intentionally omits — see container.create_worker_application).
    from auto_apply_app.infrastructures.configuration.container import (
        create_worker_application,
    )
    app = create_worker_application()
    runner = app.agent_runner

    # Pick the run coroutine up front so the SIGTERM handler can be installed
    # BEFORE the run starts (a preemption can land at any moment).
    if action == "start":
        run_coro = runner.run_start(search_id, user_id)
    elif action == "resume":
        run_coro = runner.run_resume(search_id, user_id, apply_all)
    else:
        logger.error("Unknown AGENT_ACTION=%s", action)
        return 2

    run_task = asyncio.create_task(run_coro)

    # Phase C-1 SIGTERM hardening: Cloud Run sends SIGTERM (then SIGKILL after a
    # short grace window) when it stops/preempts the task. The agent runs IN THIS
    # process, so the in-process force-cleanup actually closes the live Chromium —
    # preventing a stopped run from leaking browsers. Then cancel the run task so
    # the process exits promptly within the grace window.
    #
    # runner.run_start/run_resume publish _eot in their `finally`, so even a
    # cancelled run still closes the SSE stream cleanly.
    async def _graceful_stop() -> None:
        logger.warning("SIGTERM: forcing browser cleanup for %s", search_id)
        try:
            await app._agent_service.kill_job_search(search_id)
        except Exception:
            logger.exception("cleanup on SIGTERM failed for %s", search_id)
        run_task.cancel()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(
            signal.SIGTERM, lambda: asyncio.create_task(_graceful_stop())
        )
    except (NotImplementedError, RuntimeError):
        pass  # signal handlers may be unavailable in some environments

    try:
        ok = await run_task
    except asyncio.CancelledError:
        # Preempted: cleanup already ran in _graceful_stop; report failure so the
        # execution shows Failed (not a clean success).
        ok = False

    return 0 if ok else 1


def main() -> None:
    code = asyncio.run(_amain())
    sys.exit(code)


if __name__ == "__main__":
    main()
