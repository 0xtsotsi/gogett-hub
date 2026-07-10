"""Redis adapter for transient realtime Pub/Sub channels."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, AsyncIterator, Sequence
from contextlib import asynccontextmanager

from redis.asyncio import Redis

from app.core.config import settings
from app.core.domain.realtime import RealtimeChannel


class RedisChannelAdapter:
    """Shared Redis client with one leased Pub/Sub connection per subscriber."""

    def __init__(self, redis_url: str | None = None, *, client: Redis | None = None):
        self.redis_url = redis_url
        self._redis = client
        self._owns_client = client is None
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Create the process-wide Redis client/pool without leasing a connection."""
        if self._redis is not None:
            return
        async with self._connect_lock:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self.redis_url or settings.redis_url,
                    decode_responses=True,
                    health_check_interval=30,
                    socket_keepalive=True,
                    max_connections=settings.redis_max_connections,
                )
                self._owns_client = True

    async def disconnect(self) -> None:
        """Close the owned Redis pool during application shutdown."""
        redis_client = self._redis
        self._redis = None
        if redis_client is not None and self._owns_client:
            await redis_client.aclose()

    async def _client(self) -> Redis:
        await self.connect()
        assert self._redis is not None
        return self._redis

    async def publish(self, channel: str, message: object) -> None:
        """Publish once; redis-py reconnects pooled sockets when possible."""
        payload: str | bytes
        if isinstance(message, (str, bytes)):
            payload = message
        else:
            payload = json.dumps(message, default=str)
        await (await self._client()).publish(channel, payload)

    @asynccontextmanager
    async def subscribe(
        self, channels: Sequence[str]
    ) -> AsyncGenerator[AsyncIterator[str | bytes], None]:
        """Lease a dedicated Pub/Sub connection and always return it to the pool."""
        if not channels:
            raise ValueError("At least one realtime channel is required")

        pubsub = (await self._client()).pubsub(ignore_subscribe_messages=True)
        try:
            await pubsub.subscribe(*channels)

            async def iterator() -> AsyncIterator[str | bytes]:
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        data = message.get("data")
                        if isinstance(data, (str, bytes)):
                            yield data

            yield iterator()
        finally:
            with contextlib.suppress(Exception):
                await pubsub.unsubscribe(*channels)
            with contextlib.suppress(Exception):
                await pubsub.aclose()


channel_service = RedisChannelAdapter()


async def get_channel_service() -> RealtimeChannel:
    """FastAPI dependency returning the process-wide realtime channel port."""
    await channel_service.connect()
    return channel_service
