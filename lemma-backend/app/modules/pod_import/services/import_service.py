"""Application service for applying a pod import.

This is the orchestration brain: it drives a ``PodImportEntity`` over its plan,
persisting a checkpoint after every step so a crash or failure mid-apply leaves
a durable, resumable record rather than a half-built pod. The actual resource
creation is delegated to a ``ResourceApplier`` — the loop knows nothing about
tables vs agents, only about stepping, checkpointing, and resuming.

The same ``apply`` call serves a fresh import and a resume: a FAILED import
re-enters APPLYING and the loop skips the steps already marked done.
"""

from __future__ import annotations

from app.core.log.log import get_logger
from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.domain.ports import (
    ImportApplyContext,
    ImportRepository,
    ResourceApplier,
)

logger = get_logger(__name__)


class ImportService:
    """Applies (and resumes) imports step by step with per-step checkpoints."""

    def __init__(
        self,
        *,
        repository: ImportRepository,
        applier: ResourceApplier,
    ) -> None:
        self._repo = repository
        self._applier = applier

    async def apply(
        self, entity: PodImportEntity, *, ctx: ImportApplyContext
    ) -> PodImportEntity:
        """Apply the import to completion, or stop at the first failing step.

        Idempotent across resumes: completed steps are never re-run. On any step
        failure the import is marked FAILED (still resumable) and returned — the
        caller decides whether to surface it and let the user retry.
        """
        entity.begin_apply()
        await self._repo.save(entity)

        while (step := entity.next_pending_step()) is not None:
            try:
                await self._applier.apply_step(step, ctx)
            except Exception as exc:  # noqa: BLE001 — failure is a first-class state
                logger.warning(
                    "import_step_failed",
                    import_id=str(entity.id),
                    resource_type=step.resource_type,
                    resource_name=step.resource_name,
                    error=str(exc),
                )
                # A step that died mid-transaction (e.g. a DB error) leaves the
                # shared session unusable; discard its partial work so the
                # FAILED checkpoint below persists on a clean transaction
                # instead of raising and stranding the import in APPLYING.
                await self._repo.rollback()
                entity.fail(step, str(exc))
                await self._repo.save(entity)
                return entity

            entity.record_step_completed(step)
            await self._repo.save(entity)

        entity.complete()
        await self._repo.save(entity)
        return entity
