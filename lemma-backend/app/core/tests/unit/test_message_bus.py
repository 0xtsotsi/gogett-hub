from __future__ import annotations

import pytest

from app.core.infrastructure.events import message_bus


@pytest.mark.asyncio
async def test_partially_connected_broker_is_stopped(monkeypatch):
    stopped = False

    class _FailingBroker:
        def __init__(self, redis_url: str) -> None:
            assert redis_url == "redis://message-bus-test"

        async def connect(self) -> None:
            raise ConnectionError("redis unavailable")

        async def stop(self) -> None:
            nonlocal stopped
            stopped = True

    monkeypatch.setattr(message_bus, "RedisBroker", _FailingBroker)
    bus = message_bus.FastStreamRedisMessageBus("redis://message-bus-test")

    with pytest.raises(ConnectionError, match="redis unavailable"):
        await bus.connect()

    assert stopped
    assert bus._broker is None
