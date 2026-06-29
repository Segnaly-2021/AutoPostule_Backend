import json
from typing import AsyncIterator, Optional

from redis.asyncio import Redis

from auto_apply_app.application.service_ports.progress_broker_port import ProgressBrokerPort

_EOT = "_eot"


def _channel(search_id: str) -> str:
    return f"progress:{search_id}"


class RedisProgressBroker(ProgressBrokerPort):
    """Redis Pub/Sub implementation. Used in DATABASE mode (production)."""

    def __init__(self, redis_client: Redis):
        # NOTE: the shared client is created with decode_responses=True,
        # so Pub/Sub message payloads arrive as `str` (no .decode()).
        self._redis = redis_client

    async def publish(self, search_id: str, event: dict) -> None:
        await self._redis.publish(_channel(search_id), json.dumps(event))

    async def publish_end(self, search_id: str) -> None:
        await self._redis.publish(_channel(search_id), json.dumps({_EOT: True}))

    async def stream(self, search_id: str) -> AsyncIterator[Optional[dict]]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(_channel(search_id))
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None:
                    yield None  # idle tick -> caller emits a heartbeat
                    continue
                frame = json.loads(msg["data"])  # data is str
                if frame.get(_EOT):
                    return
                yield frame
        finally:
            await pubsub.unsubscribe(_channel(search_id))
            # redis-py 7 (this repo pins redis==7.3.0) uses aclose().
            await pubsub.aclose()
