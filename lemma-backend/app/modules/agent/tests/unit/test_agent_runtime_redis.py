from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from app.modules.agent.infrastructure import agent_runtime_redis


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None]] = []
        self.deleted: list[str] = []
        self.closed = False

    async def set(self, key: str, value: str, *, ex: int | None = None) -> None:
        self.values[key] = value
        self.set_calls.append((key, value, ex))

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_runtime_redis():
    agent_runtime_redis._redis_client = None
    yield
    agent_runtime_redis._redis_client = None


def test_runtime_redis_is_lazy_and_reused(monkeypatch):
    fake = _FakeRedis()
    calls: list[tuple[str, dict[str, Any]]] = []

    def from_url(url: str, **kwargs: Any) -> _FakeRedis:
        calls.append((url, kwargs))
        return fake

    monkeypatch.setattr(agent_runtime_redis.Redis, "from_url", from_url)

    first = agent_runtime_redis.get_agent_runtime_redis()
    second = agent_runtime_redis.get_agent_runtime_redis()

    assert first is fake
    assert second is first
    daemon_id = uuid4()
    run_id = uuid4()
    assert agent_runtime_redis.daemon_command_channel(daemon_id).endswith(":commands")
    assert agent_runtime_redis.run_event_channel(run_id).endswith(":events")
    assert calls == [
        (
            agent_runtime_redis.settings.redis_url,
            {
                "decode_responses": True,
                "health_check_interval": 30,
                "socket_keepalive": True,
                "max_connections": agent_runtime_redis.settings.redis_max_connections,
            },
        )
    ]


@pytest.mark.asyncio
async def test_capacity_round_trip_online_check_and_cleanup():
    fake = _FakeRedis()
    agent_runtime_redis._redis_client = fake  # type: ignore[assignment]
    daemon_id = uuid4()
    user_id = uuid4()

    await agent_runtime_redis.set_daemon_capacity(
        daemon_id=daemon_id,
        active_run_count=2,
        max_concurrent_runs=5,
    )

    assert await agent_runtime_redis.get_daemon_capacity(daemon_id=daemon_id) == {
        "active_run_count": 2,
        "max_concurrent_runs": 5,
    }
    assert fake.set_calls[0][2] == 120

    online_key = agent_runtime_redis.daemon_online_key(daemon_id)
    fake.values[online_key] = str(user_id)
    assert await agent_runtime_redis.is_daemon_online(
        daemon_id=daemon_id, user_id=user_id
    )
    assert not await agent_runtime_redis.is_daemon_online(
        daemon_id=daemon_id, user_id=uuid4()
    )

    await agent_runtime_redis.clear_daemon_capacity(daemon_id=daemon_id)
    assert fake.deleted == [f"agent-runtime:daemon:{daemon_id}:capacity"]

    await agent_runtime_redis.close_agent_runtime_redis()
    assert fake.closed
    assert agent_runtime_redis._redis_client is None
    await agent_runtime_redis.close_agent_runtime_redis()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [None, "not-json", "[]"])
async def test_capacity_rejects_missing_or_malformed_state(raw: str | None):
    fake = _FakeRedis()
    agent_runtime_redis._redis_client = fake  # type: ignore[assignment]
    daemon_id = uuid4()
    if raw is not None:
        fake.values[f"agent-runtime:daemon:{daemon_id}:capacity"] = raw

    assert await agent_runtime_redis.get_daemon_capacity(daemon_id=daemon_id) is None


@pytest.mark.asyncio
async def test_publish_json_uses_shared_realtime_channel(monkeypatch):
    published: list[tuple[str, dict[str, object]]] = []

    class _Channel:
        async def publish(self, channel: str, payload: dict[str, object]) -> None:
            published.append((channel, payload))

    async def get_channel() -> _Channel:
        return _Channel()

    monkeypatch.setattr(agent_runtime_redis, "get_channel_service", get_channel)

    await agent_runtime_redis.publish_json("agent-runtime:test", {"ready": True})

    assert published == [("agent-runtime:test", {"ready": True})]


class _ScriptedRedis:
    """Minimal Redis stub that emulates the slot-reservation Lua script.

    The real implementation atomically compares current count to limit and
    increments, all-or-nothing. Replicate that here so the test exercises the
    same ``try_reserve_user_run_slot`` / ``release_user_run_slot`` contract
    without spinning up a Redis container.
    """

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def eval(
        self,
        script: str,
        num_keys: int,
        key: str,
        *args: object,
    ) -> int:
        assert num_keys == 1
        current = int(self.values.get(key, "0"))
        if "INCR" in script:
            limit = int(args[0])  # type: ignore[arg-type]
            if current >= limit:
                return -1
            new = current + 1
            self.values[key] = str(new)
            return new
        if current <= 0:
            self.values.pop(key, None)
            return 0
        new = current - 1
        self.values[key] = str(new)
        return new

    async def get(self, key: str) -> str | None:
        return self.values.get(key)


@pytest.mark.asyncio
async def test_user_run_slot_respects_per_user_limit():
    agent_runtime_redis._redis_client = _ScriptedRedis()  # type: ignore[assignment]
    daemon_id = uuid4()
    user_id = uuid4()

    assert await agent_runtime_redis.try_reserve_user_run_slot(
        daemon_id=daemon_id, user_id=user_id, per_user_limit=2
    )
    assert await agent_runtime_redis.try_reserve_user_run_slot(
        daemon_id=daemon_id, user_id=user_id, per_user_limit=2
    )
    # Third reservation by the same user must fail atomically.
    assert not await agent_runtime_redis.try_reserve_user_run_slot(
        daemon_id=daemon_id, user_id=user_id, per_user_limit=2
    )
    # A different user on the same daemon has their own bucket.
    other_user_id = uuid4()
    assert await agent_runtime_redis.try_reserve_user_run_slot(
        daemon_id=daemon_id, user_id=other_user_id, per_user_limit=2
    )


@pytest.mark.asyncio
async def test_user_run_slot_release_is_clamped_at_zero():
    agent_runtime_redis._redis_client = _ScriptedRedis()  # type: ignore[assignment]
    daemon_id = uuid4()
    user_id = uuid4()

    await agent_runtime_redis.release_user_run_slot(daemon_id=daemon_id, user_id=user_id)
    await agent_runtime_redis.release_user_run_slot(daemon_id=daemon_id, user_id=user_id)
    assert await agent_runtime_redis.get_user_run_count(
        daemon_id=daemon_id, user_id=user_id
    ) == 0

    # Reserve 2, release 3 -> counter clamped at 0, not -1.
    for _ in range(2):
        assert await agent_runtime_redis.try_reserve_user_run_slot(
            daemon_id=daemon_id, user_id=user_id, per_user_limit=5
        )
    await agent_runtime_redis.release_user_run_slot(daemon_id=daemon_id, user_id=user_id)
    await agent_runtime_redis.release_user_run_slot(daemon_id=daemon_id, user_id=user_id)
    await agent_runtime_redis.release_user_run_slot(daemon_id=daemon_id, user_id=user_id)
    assert await agent_runtime_redis.get_user_run_count(
        daemon_id=daemon_id, user_id=user_id
    ) == 0


@pytest.mark.asyncio
async def test_user_run_slot_zero_limit_always_rejects():
    agent_runtime_redis._redis_client = _ScriptedRedis()  # type: ignore[assignment]
    daemon_id = uuid4()
    user_id = uuid4()
    assert not await agent_runtime_redis.try_reserve_user_run_slot(
        daemon_id=daemon_id, user_id=user_id, per_user_limit=0
    )
