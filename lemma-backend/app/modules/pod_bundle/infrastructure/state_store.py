"""Durable state store for pod bundle jobs.

Import jobs are persisted in PostgreSQL and mirrored to Redis for low-latency
status/SSE reads. PostgreSQL is authoritative: Redis loss cannot erase a job,
reverse a cancellation tombstone, or make a committed step disappear. A
legacy Redis-only import is copied into PostgreSQL the first time it is read.

Export and publish jobs retain their existing Redis-backed lifecycle.
"""

from __future__ import annotations

from typing import TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.core.config import settings
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.infrastructure.db.session import async_session_maker
from app.core.log.log import get_logger
from app.modules.pod_bundle.config import pod_bundle_settings
from app.modules.pod_bundle.domain.state import (
    IMPORT_TERMINAL_STATUSES,
    ExportState,
    ImportState,
    ImportStatus,
    PublishState,
)

StateT = TypeVar("StateT", bound=BaseModel)

_KEY_PREFIX = "pod-bundle"
logger = get_logger(__name__)


class PodBundleStateStore:
    """Typed facade over :class:`RedisJsonCache` for the three job documents."""

    def __init__(self, cache: RedisJsonCache | None = None):
        self._durable = cache is None
        self._cache = cache or RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix=_KEY_PREFIX,
            ttl_seconds=pod_bundle_settings.pod_bundle_state_ttl_seconds,
        )

    # --- generic plumbing -------------------------------------------------

    async def _get(self, kind: str, job_id: UUID, model: type[StateT]) -> StateT | None:
        raw = await self._cache.get_json(f"{kind}:{job_id}")
        if raw is None:
            return None
        return model.model_validate(raw)

    async def _save(
        self, kind: str, job_id: UUID, state: BaseModel, ttl_seconds: int | None = None
    ) -> None:
        # touch() bumps seq/updated_at exactly once per durable write, so SSE
        # consumers can totally order events against a replayed snapshot.
        state.touch()  # type: ignore[attr-defined]
        await self._cache.set_json(
            f"{kind}:{job_id}", state.model_dump(mode="json"), ttl_seconds=ttl_seconds
        )

    async def _delete(self, kind: str, job_id: UUID) -> None:
        await self._cache.delete(f"{kind}:{job_id}")

    # --- imports ----------------------------------------------------------

    async def get_import(self, import_id: UUID) -> ImportState | None:
        if not self._durable:
            return await self._get("import", import_id, ImportState)

        from app.modules.pod_bundle.infrastructure.models import PodBundleImportJob

        async with async_session_maker() as session:
            model = await session.get(PodBundleImportJob, import_id)
        if model is not None:
            return ImportState.model_validate(model.snapshot)

        # Rolling-deployment bridge: import a job written by an older backend
        # from Redis before allowing durable cancellation for that job.
        try:
            legacy = await self._get("import", import_id, ImportState)
        except (RedisError, ValidationError):
            logger.warning(
                "Failed to inspect legacy pod bundle state cache",
                import_id=str(import_id),
            )
            return None
        if legacy is None:
            return None
        await self._persist_import(legacy)
        return legacy

    async def save_import(self, state: ImportState) -> None:
        if not self._durable:
            await self._save("import", state.import_id, state)
            return

        persisted = await self._persist_import(state)
        if not persisted:
            return
        try:
            await self._cache.set_json(
                f"import:{state.import_id}", state.model_dump(mode="json")
            )
        except RedisError:
            logger.warning(
                "Failed to refresh pod bundle state cache",
                import_id=str(state.import_id),
                status=state.status.value,
            )

    async def _persist_import(self, state: ImportState) -> bool:
        from app.modules.pod_bundle.infrastructure.models import (
            PodBundleImportJob,
            PodBundleImportStep,
        )

        async with async_session_maker() as session, session.begin():
            existing_model = await session.scalar(
                select(PodBundleImportJob)
                .where(PodBundleImportJob.id == state.import_id)
                .with_for_update()
            )
            if existing_model is not None:
                existing = ImportState.model_validate(existing_model.snapshot)
                if existing.status in IMPORT_TERMINAL_STATUSES:
                    return False
                state.seq = max(state.seq, existing.seq)
                state.committed_steps = sorted(
                    set(existing.committed_steps) | set(state.committed_steps)
                )
                if existing.status == ImportStatus.CANCELLING and state.status not in {
                    ImportStatus.CANCELLING,
                    ImportStatus.CANCELLED,
                    ImportStatus.PARTIALLY_CANCELLED,
                }:
                    state.status = ImportStatus.CANCELLING
                    state.cancel_requested_at = existing.cancel_requested_at

            # Sequence/timestamp advance is part of the same durable write as
            # the state transition, and the Redis mirror receives this snapshot.
            state.touch()
            snapshot = state.model_dump(mode="json")
            await session.execute(
                insert(PodBundleImportJob)
                .values(
                    id=state.import_id,
                    pod_id=state.pod_id,
                    user_id=state.user_id,
                    status=state.status.value,
                    snapshot=snapshot,
                    cancel_requested_at=state.cancel_requested_at,
                    current_step=state.current_step,
                    committed_steps=state.committed_steps,
                    error=state.error,
                    completed_at=state.completed_at,
                )
                .on_conflict_do_update(
                    index_elements=(PodBundleImportJob.id,),
                    set_={
                        "status": state.status.value,
                        "snapshot": snapshot,
                        "cancel_requested_at": state.cancel_requested_at,
                        "current_step": state.current_step,
                        "committed_steps": state.committed_steps,
                        "error": state.error,
                        "completed_at": state.completed_at,
                        "updated_at": state.updated_at,
                    },
                )
            )
            if state.plan is not None:
                for step in state.plan.steps:
                    await session.execute(
                        insert(PodBundleImportStep)
                        .values(
                            import_id=state.import_id,
                            step_index=step.index,
                            kind=step.kind.value,
                            name=step.name,
                            status=step.status.value,
                            error=step.error,
                        )
                        .on_conflict_do_update(
                            constraint="uq_pod_bundle_import_step",
                            set_={"status": step.status.value, "error": step.error},
                        )
                    )
        return True

    async def delete_import(self, import_id: UUID) -> None:
        await self._delete("import", import_id)

    # --- exports ----------------------------------------------------------

    async def get_export(self, export_id: UUID) -> ExportState | None:
        return await self._get("export", export_id, ExportState)

    async def save_export(
        self, state: ExportState, *, ttl_seconds: int | None = None
    ) -> None:
        await self._save("export", state.export_id, state, ttl_seconds=ttl_seconds)

    async def delete_export(self, export_id: UUID) -> None:
        await self._delete("export", export_id)

    # --- publishes --------------------------------------------------------

    async def get_publish(self, publish_id: UUID) -> PublishState | None:
        return await self._get("publish", publish_id, PublishState)

    async def save_publish(self, state: PublishState) -> None:
        await self._save("publish", state.publish_id, state)

    async def delete_publish(self, publish_id: UUID) -> None:
        await self._delete("publish", publish_id)

    async def close(self) -> None:
        await self._cache.close()


_state_store: PodBundleStateStore | None = None


def get_pod_bundle_state_store() -> PodBundleStateStore:
    """Process-wide store (API and worker each get one lazy Redis client)."""
    global _state_store
    if _state_store is None:
        _state_store = PodBundleStateStore()
    return _state_store
