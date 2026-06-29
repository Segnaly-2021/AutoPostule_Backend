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
