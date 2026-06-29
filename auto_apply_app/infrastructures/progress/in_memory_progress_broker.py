import asyncio
from collections import defaultdict
from typing import AsyncIterator, Dict, Optional

from auto_apply_app.application.service_ports.progress_broker_port import ProgressBrokerPort

_EOT = "_eot"


class InMemoryProgressBroker(ProgressBrokerPort):
    """
    Single-process Pub/Sub for MEMORY mode and tests.

    MUST be used as a singleton (see container wiring) so the publisher (agent
    task) and the subscriber (SSE generator) share the same queue map. One
    subscriber per search_id (the SSE request) is assumed.
    """

    def __init__(self):
        self._queues: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

    async def publish(self, search_id: str, event: dict) -> None:
        await self._queues[search_id].put(event)

    async def publish_end(self, search_id: str) -> None:
        await self._queues[search_id].put({_EOT: True})

    async def stream(self, search_id: str) -> AsyncIterator[Optional[dict]]:
        q = self._queues[search_id]
        try:
            while True:
                try:
                    frame = await asyncio.wait_for(q.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    yield None
                    continue
                if frame.get(_EOT):
                    return
                yield frame
        finally:
            self._queues.pop(search_id, None)
