"""Redis-backed idempotency + failure tracking for schedule fires.

Two concerns, both keyed by schedule, both best-effort (a Redis blip must never
drop or wrongly duplicate a legitimate fire):

- Agent-target fire dedup: agent schedules have no DB uniqueness constraint to gate
  duplicate side effects (unlike workflow runs, gated by
  ``(flow_id, user_id, schedule_event_id)``), so a redelivered ``schedule.fired``
  could start a second conversation. A ``SET NX EX`` claim keyed on the fire's event
  id is the durable guard, layered under streaq's in-flight ``_job_id`` dedup.
- Consecutive-failure counter for the circuit breaker: a schedule whose runs keep
  erroring is auto-deactivated after N consecutive failures so it stops firing and
  filling the DB with failed runs.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

from redis.asyncio import Redis

from app.core.config import settings
from app.core.log.log import get_logger

logger = get_logger(__name__)

# Just needs to comfortably outlast the at-least-once redelivery window for a single
# fire; schedule fires are low-volume so a generous window is cheap.
_AGENT_FIRE_DEDUP_TTL_SECONDS = 6 * 60 * 60  # 6h

# Refreshed on each failure, so a schedule that stops firing entirely eventually
# drops its counter instead of leaking a key forever. Any schedule firing more
# often than this is, by definition, not dormant.
_FAILURE_COUNTER_TTL_SECONDS = 30 * 24 * 60 * 60  # 30d


class ScheduleFireStore:
    """Redis-backed idempotency + failure tracking for schedule fires."""

    def __init__(self, redis_url: str | None = None) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _agent_fire_key(self, schedule_id: str, event_id: str) -> str:
        return f"schedule:agent-fire:{schedule_id}:{event_id}"

    def _failure_key(self, schedule_id: str) -> str:
        return f"schedule:consec-fail:{schedule_id}"

    async def claim_agent_fire(
        self, *, schedule_id: UUID | str, event_id: str | None
    ) -> bool:
        """Claim a single agent-target fire.

        Returns True the first time a given ``(schedule_id, event_id)`` is seen
        (caller should dispatch) and False for a duplicate (caller should skip).
        With no stable ``event_id`` there is nothing to dedup on, so it returns
        True (matches prior behavior). Fails open (True) on any Redis error so a
        transient blip never silently drops a legitimate fire.
        """
        if not event_id:
            return True
        try:
            redis = await self._get_redis()
            claimed = await redis.set(
                self._agent_fire_key(str(schedule_id), str(event_id)),
                "1",
                ex=_AGENT_FIRE_DEDUP_TTL_SECONDS,
                nx=True,
            )
            return bool(claimed)
        except Exception as exc:  # noqa: BLE001 — dedup is best-effort
            logger.warning(
                "Schedule agent-fire dedup unavailable for %s:%s (%s); proceeding",
                schedule_id,
                event_id,
                exc,
            )
            return True

    async def record_failure(self, *, schedule_id: UUID | str) -> int:
        """Increment and return the consecutive-failure count for a schedule.

        Returns 0 on any Redis error so a blip never trips the breaker.
        """
        try:
            redis = await self._get_redis()
            key = self._failure_key(str(schedule_id))
            count = await redis.incr(key)
            await redis.expire(key, _FAILURE_COUNTER_TTL_SECONDS)
            return int(count)
        except Exception as exc:  # noqa: BLE001 — breaker is best-effort
            logger.warning(
                "Schedule failure counter unavailable for %s (%s)", schedule_id, exc
            )
            return 0

    async def reset_failures(self, *, schedule_id: UUID | str) -> None:
        """Clear the consecutive-failure count (on success or reactivation)."""
        try:
            redis = await self._get_redis()
            await redis.delete(self._failure_key(str(schedule_id)))
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning(
                "Failed clearing schedule failure counter for %s (%s)",
                schedule_id,
                exc,
            )


_store: ScheduleFireStore | None = None


def get_schedule_fire_store() -> ScheduleFireStore:
    global _store
    if _store is None:
        _store = ScheduleFireStore()
    return _store
