"""Redis keys and adapters for cross-process agent runtime coordination."""

from __future__ import annotations

import json
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.core.infrastructure.channels.channel_service import get_channel_service
from app.modules.agent.domain.value_objects import JsonObject

_DAEMON_CAPACITY_TTL_SECONDS = 120
# Per-user concurrent-run counter for shared (ORGANIZATION-scoped) daemon
# profiles. Keyed by (daemon_id, user_id) so each org member gets their own
# independent quota slice of the daemon's max_concurrent_runs. TTL is renewed
# on every increment so an idle user's counter drops back to 0 once they stop
# running runs for a while, instead of leaking forever after a process crash.
_USER_RUN_COUNT_TTL_SECONDS = 600
_redis_client: Redis | None = None


def daemon_command_channel(daemon_id: UUID) -> str:
    return f"agent-runtime:daemon:{daemon_id}:commands"


def run_event_channel(agent_run_id: UUID) -> str:
    return f"agent-runtime:run:{agent_run_id}:events"


def daemon_online_key(daemon_id: UUID) -> str:
    return f"agent-runtime:daemon:{daemon_id}:online"


def _daemon_capacity_key(daemon_id: UUID) -> str:
    return f"agent-runtime:daemon:{daemon_id}:capacity"


def _user_run_count_key(*, daemon_id: UUID, user_id: UUID) -> str:
    return f"agent-runtime:daemon:{daemon_id}:user:{user_id}:active_runs"


def get_agent_runtime_redis() -> Redis:
    global _redis_client  # noqa: PLW0603
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            health_check_interval=30,
            socket_keepalive=True,
            max_connections=settings.redis_max_connections,
        )
    return _redis_client


async def set_daemon_capacity(
    *, daemon_id: UUID, active_run_count: int, max_concurrent_runs: int
) -> None:
    await get_agent_runtime_redis().set(
        _daemon_capacity_key(daemon_id),
        json.dumps(
            {
                "active_run_count": active_run_count,
                "max_concurrent_runs": max_concurrent_runs,
            }
        ),
        ex=_DAEMON_CAPACITY_TTL_SECONDS,
    )


async def get_daemon_capacity(*, daemon_id: UUID) -> JsonObject | None:
    raw = await get_agent_runtime_redis().get(_daemon_capacity_key(daemon_id))
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def clear_daemon_capacity(*, daemon_id: UUID) -> None:
    await get_agent_runtime_redis().delete(_daemon_capacity_key(daemon_id))


async def try_reserve_user_run_slot(
    *, daemon_id: UUID, user_id: UUID, per_user_limit: int
) -> bool:
    """Atomically reserve a run slot for ``user_id`` against a shared daemon.

    Returns ``True`` when the user's current active-run count is below
    ``per_user_limit`` and the counter has been incremented. Returns ``False``
    when the user is at their personal cap. Uses a Lua script so the
    INCR-and-compare is atomic across processes (the daemon hub can run in
    multiple workers).
    """
    if per_user_limit <= 0:
        return False
    redis = get_agent_runtime_redis()
    key = _user_run_count_key(daemon_id=daemon_id, user_id=user_id)
    # KEYS[1] = key, ARGV[1] = limit, ARGV[2] = ttl seconds.
    # Returns the new counter value on success, -1 when at limit.
    script = """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    local limit = tonumber(ARGV[1])
    if current >= limit then
      return -1
    end
    local new = redis.call('INCR', KEYS[1])
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
    return new
    """
    result = await redis.eval(script, 1, key, per_user_limit, _USER_RUN_COUNT_TTL_SECONDS)
    return int(result) != -1


async def release_user_run_slot(*, daemon_id: UUID, user_id: UUID) -> None:
    """Decrement the per-user run counter, clamping at 0 to avoid negative drift."""
    redis = get_agent_runtime_redis()
    key = _user_run_count_key(daemon_id=daemon_id, user_id=user_id)
    script = """
    local current = tonumber(redis.call('GET', KEYS[1]) or '0')
    if current <= 0 then
      redis.call('DEL', KEYS[1])
      return 0
    end
    return redis.call('DECR', KEYS[1])
    """
    await redis.eval(script, 1, key)


async def get_user_run_count(*, daemon_id: UUID, user_id: UUID) -> int:
    raw = await get_agent_runtime_redis().get(_user_run_count_key(daemon_id=daemon_id, user_id=user_id))
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


async def publish_json(channel: str, payload: JsonObject) -> None:
    await (await get_channel_service()).publish(channel, payload)


async def is_daemon_online(*, daemon_id: UUID, user_id: UUID) -> bool:
    value = await get_agent_runtime_redis().get(daemon_online_key(daemon_id))
    return value == str(user_id)


async def close_agent_runtime_redis() -> None:
    global _redis_client  # noqa: PLW0603
    redis_client = _redis_client
    _redis_client = None
    if redis_client is not None:
        await redis_client.aclose()
