"""Shared FastStream Redis message bus resource."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any
from pydantic import BaseModel
from faststream.redis import RedisBroker

from app.core.config import settings

logger = logging.getLogger(__name__)


class FastStreamRedisMessageBus:
    """Message bus implementation backed by FastStream Redis broker."""

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._broker: RedisBroker | None = None
        self._lock = asyncio.Lock()

    async def _get_broker(self) -> RedisBroker:
        if self._broker:
            return self._broker

        async with self._lock:
            if not self._broker:
                broker = RedisBroker(self._redis_url)
                await broker.connect()
                self._broker = broker
        return self._broker

    async def connect(self) -> RedisBroker:
        """Eagerly initialize the shared broker connection."""
        return await self._get_broker()

    async def publish(
        self, stream: str, event: BaseModel | Mapping[str, Any]
    ) -> None:
        broker = await self._get_broker()
        payload = (
            event.model_dump(mode="json")
            if isinstance(event, BaseModel)
            else dict(event)
        )
        await broker.publish(payload, stream=stream)

    async def close(self) -> None:
        if not self._broker:
            return

        broker = self._broker
        self._broker = None
        try:
            await asyncio.wait_for(broker.stop(), timeout=5.0)
        except TimeoutError:
            logger.warning("Timed out closing FastStream Redis message bus")


_message_bus: FastStreamRedisMessageBus | None = None


def get_message_bus() -> FastStreamRedisMessageBus:
    """Return shared message bus instance."""
    global _message_bus
    if _message_bus is None or _message_bus._redis_url != settings.redis_url:
        _message_bus = FastStreamRedisMessageBus(settings.redis_url)
    return _message_bus


async def close_message_bus() -> None:
    """Close shared message bus connection."""
    global _message_bus
    if _message_bus is None:
        return
    bus = _message_bus
    _message_bus = None
    await bus.close()
