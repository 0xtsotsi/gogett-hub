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

    async def save(self, entity: PodImportEntity) -> None:
        self.saves += 1

    async def get(self, import_id: UUID):  # pragma: no cover - unused here
        return None


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
