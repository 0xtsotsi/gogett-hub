"""Unit tests for the PodImport state machine — the resume/checkpoint brain."""

from __future__ import annotations

from uuid import uuid7

import pytest

from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.domain.value_objects import (
    ImportAction,
    ImportStatus,
    ImportStep,
    ImportStepStatus,
)


def _step(rtype: str, name: str, *, destructive: bool = False) -> ImportStep:
    return ImportStep(
        resource_type=rtype,
        resource_name=name,
        action=ImportAction.CREATE,
        destructive=destructive,
    )


def _import(*steps: ImportStep) -> PodImportEntity:
    return PodImportEntity.create(
        pod_id=uuid7(),
        user_id=uuid7(),
        plan=list(steps),
        source_name="acme-crm",
    )


def test_fresh_import_starts_planned_at_first_step():
    imp = _import(_step("tables", "contacts"), _step("agents", "triage"))
    assert imp.status is ImportStatus.PLANNED
    assert imp.next_pending_step().key == ("tables", "contacts")
    assert imp.progress == (0, 2)


def test_apply_advances_through_steps_to_completion():
    imp = _import(_step("tables", "contacts"), _step("agents", "triage"))
    imp.begin_apply()
    assert imp.status is ImportStatus.APPLYING

    while (step := imp.next_pending_step()) is not None:
        imp.record_step_completed(step)

    assert imp.progress == (2, 2)
    imp.complete()
    assert imp.status is ImportStatus.COMPLETED
    assert imp.completed_at is not None


def test_failure_is_resumable_and_skips_completed_steps():
    s1, s2, s3 = _step("tables", "a"), _step("functions", "b"), _step("agents", "c")
    imp = _import(s1, s2, s3)
    imp.begin_apply()

    imp.record_step_completed(s1)
    # b blows up mid-apply.
    imp.fail(s2, "boom: connector timeout")

    assert imp.status is ImportStatus.FAILED
    assert imp.error.startswith("boom")
    assert imp.is_resumable is True
    # a is done; the failed step b is retried first on resume.
    assert imp.next_pending_step().key == ("functions", "b")
    assert imp.progress == (1, 3)

    # Resume: re-enter apply, finish the rest. The completed step is never re-run.
    imp.begin_apply()
    assert imp.status is ImportStatus.APPLYING
    assert imp.error is None
    assert s1.status is ImportStepStatus.COMPLETED
    for step in (s2, s3):
        imp.record_step_completed(step)
    imp.complete()
    assert imp.status is ImportStatus.COMPLETED


def test_cannot_complete_with_pending_steps():
    imp = _import(_step("tables", "a"), _step("agents", "b"))
    imp.begin_apply()
    imp.record_step_completed(imp.plan[0])
    with pytest.raises(ValueError, match="still pending"):
        imp.complete()


def test_terminal_import_cannot_be_reapplied_or_cancelled():
    imp = _import(_step("tables", "a"))
    imp.begin_apply()
    imp.record_step_completed(imp.plan[0])
    imp.complete()
    assert imp.is_resumable is False
    with pytest.raises(ValueError, match="Cannot apply import"):
        imp.begin_apply()
    with pytest.raises(ValueError, match="Cannot cancel import"):
        imp.cancel()


def test_skipped_steps_count_as_done_and_are_not_rerun():
    imp = _import(_step("tables", "a"), _step("surfaces", "slack"))
    imp.begin_apply()
    imp.record_step_completed(imp.plan[0])
    imp.record_step_skipped(imp.plan[1])  # e.g. unresolved + deferred
    assert imp.remaining_steps() == []
    imp.complete()
    assert imp.status is ImportStatus.COMPLETED


def test_destructive_steps_are_surfaced():
    imp = _import(
        _step("tables", "a"),
        _step("tables", "contacts", destructive=True),
    )
    assert [s.resource_name for s in imp.destructive_steps] == ["contacts"]


def test_is_step_done_matches_by_identity():
    imp = _import(_step("tables", "a"), _step("agents", "b"))
    imp.begin_apply()
    imp.record_step_completed(imp.plan[0])
    assert imp.is_step_done("tables", "a") is True
    assert imp.is_step_done("agents", "b") is False
    assert imp.is_step_done("tables", "missing") is False
