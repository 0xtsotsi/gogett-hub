"""Unit tests for the ImportService apply/resume loop, with in-memory fakes for
the repository and the resource applier (no DB, no real resource services)."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid7

import pytest

from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.domain.value_objects import (
    ImportAction,
    ImportStatus,
    ImportStep,
    ImportStepStatus,
)
from app.modules.pod_import.services.import_service import ImportService


@dataclass
class FakeRepo:
    """Records every save so we can assert a checkpoint lands after each step."""

    saves: int = 0
    rollbacks: int = 0

    async def save(self, entity: PodImportEntity) -> None:
        self.saves += 1

    async def get(self, import_id: UUID):  # pragma: no cover - unused here
        return None

    async def rollback(self) -> None:
        self.rollbacks += 1


@dataclass
class PoisonableRepo(FakeRepo):
    """Models the real session's failure mode: once a step's DB error aborts
    the transaction, every save raises until rollback() clears it."""

    poisoned: bool = False

    async def save(self, entity: PodImportEntity) -> None:
        if self.poisoned:
            raise RuntimeError("current transaction is aborted")
        await super().save(entity)

    async def rollback(self) -> None:
        self.poisoned = False
        await super().rollback()


@dataclass
class FakeApplier:
    """Records applied steps; can be told to raise on a given resource name once."""

    applied: list[tuple[str, str]] = field(default_factory=list)
    fail_on: str | None = None
    _failed_once: bool = False

    async def apply_step(self, step: ImportStep, ctx) -> None:
        if self.fail_on == step.resource_name and not self._failed_once:
            self._failed_once = True
            raise RuntimeError("connector timeout")
        self.applied.append((step.resource_type, step.resource_name))


@dataclass
class FakeCtx:
    pod_id: UUID
    user_id: UUID


def _import(*names: str) -> PodImportEntity:
    return PodImportEntity.create(
        pod_id=uuid7(),
        user_id=uuid7(),
        plan=[
            ImportStep(resource_type="tables", resource_name=n, action=ImportAction.CREATE)
            for n in names
        ],
    )


def _ctx() -> FakeCtx:
    return FakeCtx(pod_id=uuid7(), user_id=uuid7())


@pytest.mark.asyncio
async def test_apply_runs_every_step_and_completes():
    imp = _import("a", "b", "c")
    repo, applier = FakeRepo(), FakeApplier()
    service = ImportService(repository=repo, applier=applier)

    result = await service.apply(imp, ctx=_ctx())

    assert result.status is ImportStatus.COMPLETED
    assert applier.applied == [("tables", "a"), ("tables", "b"), ("tables", "c")]
    # begin_apply + 3 steps + complete = 5 checkpoints.
    assert repo.saves == 5


@pytest.mark.asyncio
async def test_apply_stops_at_failure_and_stays_resumable():
    imp = _import("a", "b", "c")
    repo = FakeRepo()
    applier = FakeApplier(fail_on="b")
    service = ImportService(repository=repo, applier=applier)

    result = await service.apply(imp, ctx=_ctx())

    assert result.status is ImportStatus.FAILED
    assert result.is_resumable is True
    assert "connector timeout" in result.error
    # a applied, b failed, c never attempted.
    assert applier.applied == [("tables", "a")]
    assert result.next_pending_step().resource_name == "b"


@pytest.mark.asyncio
async def test_resume_continues_without_rerunning_completed_steps():
    imp = _import("a", "b", "c")
    applier = FakeApplier(fail_on="b")
    service = ImportService(repository=FakeRepo(), applier=applier)

    failed = await service.apply(imp, ctx=_ctx())
    assert failed.status is ImportStatus.FAILED

    # Resume with the same entity: the applier no longer fails on b.
    resumed = await service.apply(failed, ctx=_ctx())

    assert resumed.status is ImportStatus.COMPLETED
    # 'a' is applied exactly once across the two passes — never re-run.
    assert applier.applied.count(("tables", "a")) == 1
    assert applier.applied == [("tables", "a"), ("tables", "b"), ("tables", "c")]


@pytest.mark.asyncio
async def test_apply_on_fully_done_plan_just_completes():
    imp = _import("a")
    imp.plan[0].status = ImportStepStatus.COMPLETED  # already applied elsewhere
    applier = FakeApplier()
    service = ImportService(repository=FakeRepo(), applier=applier)

    result = await service.apply(imp, ctx=_ctx())

    assert result.status is ImportStatus.COMPLETED
    assert applier.applied == []  # nothing re-run


@pytest.mark.asyncio
async def test_failed_step_rolls_back_before_the_failed_checkpoint_is_saved():
    # A step handler that dies mid-transaction (flush happened, then a DB error)
    # leaves the shared session aborted. Without a rollback first, saving the
    # FAILED checkpoint raises too and the import is stuck APPLYING forever.
    imp = _import("a", "b", "c")
    repo = PoisonableRepo()

    class PoisoningApplier:
        applied: list[str] = []

        async def apply_step(self, step: ImportStep, ctx) -> None:
            if step.resource_name == "b":
                repo.poisoned = True
                raise RuntimeError("duplicate key value violates unique constraint")
            self.applied.append(step.resource_name)

    service = ImportService(repository=repo, applier=PoisoningApplier())

    result = await service.apply(imp, ctx=_ctx())  # must not raise

    assert result.status is ImportStatus.FAILED
    assert result.is_resumable is True
    assert "duplicate key" in result.error
    failed = next(s for s in result.plan if s.resource_name == "b")
    assert failed.status is ImportStepStatus.FAILED
    assert "duplicate key" in failed.error
    assert repo.rollbacks == 1
    assert repo.poisoned is False  # the FAILED checkpoint landed on a clean txn


def _applying_import(*, updated_seconds_ago: float) -> PodImportEntity:
    from datetime import datetime, timedelta, timezone

    entity = PodImportEntity(
        pod_id=uuid7(),
        user_id=uuid7(),
        status=ImportStatus.APPLYING,
        plan=[
            ImportStep(
                resource_type="tables", resource_name="t", action=ImportAction.CREATE
            )
        ],
    )
    entity.updated_at = datetime.now(timezone.utc) - timedelta(
        seconds=updated_seconds_ago
    )
    return entity


def test_a_live_apply_cannot_be_joined_by_a_second_request():
    """Two concurrent loops would double-run the current step (e.g. both pass a
    table's empty check and double-seed it) — a fresh APPLYING must 409."""
    from app.modules.pod_import.domain.entities import ImportInProgressError

    with pytest.raises(ImportInProgressError):
        _applying_import(updated_seconds_ago=5).begin_apply()


def test_a_stale_applying_import_is_resumable():
    """A crashed worker's leftover stops checkpointing; once its updated_at is
    stale the import must be resumable, not locked forever."""
    entity = _applying_import(updated_seconds_ago=600)
    entity.begin_apply()
    assert entity.status is ImportStatus.APPLYING
