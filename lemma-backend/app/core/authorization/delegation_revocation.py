"""Delegation-token revocation set (Redis-backed).

A delegated workload token (agent/function) is a signed session that stays valid
until it expires. When the workload loses its standing authority — most
importantly when the agent/function is deleted — its in-flight tokens must stop
working before they expire on their own. Revoking the workload's actor id here
blocks every delegated token minted for it until the entry's TTL (configured to
be >= the max access-token lifetime) elapses.

Redis-backed (shared across replicas, like the role-snapshot and session-approval
caches). Redis being unavailable degrades to "not revoked" — availability over
strictness, consistent with the other authz caches — but logs loudly so an
outage is visible. (Resource-grant removal needs no revocation: workload grants
are queried live on every check, so they take effect immediately.)
"""

from __future__ import annotations

from uuid import UUID

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger

logger = get_logger(__name__)

_revocation_cache: RedisJsonCache | None = None


def _get_revocation_cache() -> RedisJsonCache | None:
    global _revocation_cache
    ttl = settings.delegation_revocation_ttl_seconds
    if ttl <= 0:
        return None
    if _revocation_cache is None or _revocation_cache._ttl_seconds != ttl:
        _revocation_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="authz:delegation-revoked",
            ttl_seconds=ttl,
        )
    return _revocation_cache


async def revoke_delegation(*, actor_id: UUID) -> None:
    """Block every in-flight delegated token minted for ``actor_id``."""
    cache = _get_revocation_cache()
    if cache is None:
        return
    try:
        await cache.set_json(str(actor_id), {"revoked": True})
    except Exception:
        logger.warning(
            "Delegation-revocation store unavailable; workload %s not revoked "
            "(its token will still expire naturally).",
            actor_id,
            exc_info=True,
        )


async def is_delegation_revoked(*, actor_id: UUID) -> bool:
    """True when a delegated token for ``actor_id`` has been revoked."""
    cache = _get_revocation_cache()
    if cache is None:
        return False
    try:
        payload = await cache.get_json(str(actor_id))
    except Exception:
        logger.warning(
            "Delegation-revocation store unavailable; treating workload %s as "
            "not revoked (safe for availability).",
            actor_id,
            exc_info=True,
        )
        return False
    return payload is not None
