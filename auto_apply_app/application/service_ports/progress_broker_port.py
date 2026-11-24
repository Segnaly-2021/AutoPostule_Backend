# ==============================================================================
# FILE: auto_apply_app/application/service_ports/progress_broker_port.py
#
# WHAT IT DOES:
#   Interface for carrying live agent progress from the process running the agent
#   to the API's SSE (Server-Sent Events) stream that updates the browser.
#
# USED BY:
#   - auto_apply_app/infrastructures/agent/runner.py: publishes progress
#   - auto_apply_app/infrastructures/api/routers/agent.py: subscribes for SSE
#   - auto_apply_app/infrastructures/configuration/container.py: selects the broker
#   - auto_apply_app/infrastructures/progress/in_memory_progress_broker.py: in-process implementation
#   - auto_apply_app/infrastructures/progress/redis_progress_broker.py: Redis implementation
#
# EXTERNAL DEPENDENCIES:
#   - Redis Pub/Sub: transport for the Redis broker (no import here)
# ==============================================================================
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


class ProgressBrokerPort(ABC):
    """
    Transport for agent progress frames between the process running the agent
    (publisher) and the SSE handler relaying to the browser (subscriber).

    Phase A: same process. Phase B: the Cloud Run Job publishes, the API subscribes.
    The contract is identical in both phases.
    """

    @abstractmethod
    async def publish(self, search_id: str, event: dict) -> None:
        """Publish one progress frame (the raw _emit dict) for a search."""
        ...

    @abstractmethod
    async def publish_end(self, search_id: str) -> None:
        """Publish the end-of-transmission sentinel. Call exactly once, last, per run."""
        ...

    @abstractmethod
    def stream(self, search_id: str) -> AsyncIterator[Optional[dict]]:
        """
        Async-iterate progress for a search.

        Yields:
          - a `dict` for each real frame, as it arrives;
          - `None` on an idle tick (~1s) so the caller can emit an SSE heartbeat.

        Terminates (StopAsyncIteration) right after the sentinel is seen; the
        sentinel dict itself is NOT yielded. MUST be subscribed BEFORE the run is
        dispatched, or early frames are lost (Pub/Sub has no replay).
        """
        ...
