"""PodImport aggregate: the import state machine.

The apply loop (the thing that actually calls tables.create, agents.create, …)
lives in the service layer; this entity owns the state — the ordered plan, which
steps have been applied, status transitions, and the resume logic that makes a
mid-apply failure recoverable instead of stranding a half-built pod.

The same entity is the single source of truth for both renderers: the CLI polls
it to show progress and resume; the web wizard polls it to drive its apply step.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.domain.aggregate import AggregateRoot
from app.modules.pod_import.domain.value_objects import (
    TERMINAL_STATUSES,
    ImportStatus,
    ImportStep,
    summarize_error,
)


class PodImportEntity(AggregateRoot):
    """An import of a bundle into a pod, applied step by step and resumable."""

    pod_id: UUID
    user_id: UUID
    # Display/source metadata; the bundle bytes live in storage, not here.
    source_name: str | None = None

    status: ImportStatus = ImportStatus.PLANNED
    plan: list[ImportStep] = Field(default_factory=list)

    # Anything the importer still had to supply, captured at plan time (the
    # requirements/capabilities block from pod_requirements). Opaque to the
    # entity — it's rendered by the clients, gated by the service.
    requirements: dict[str, Any] = Field(default_factory=dict)
    capabilities: list[dict[str, Any]] = Field(default_factory=list)

    error: str | None = Field(
        default=None,
        description="Human-readable reason the import failed. Truncated to stay actionable.",
    )

    started_at: datetime | None = None
    completed_at: datetime | None = None

    # -- construction ---------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        pod_id: UUID,
        user_id: UUID,
        plan: list[ImportStep],
        source_name: str | None = None,
        requirements: dict[str, Any] | None = None,
        capabilities: list[dict[str, Any]] | None = None,
    ) -> "PodImportEntity":
        """A freshly planned import, positioned before the first step."""
        return cls(
            pod_id=pod_id,
            user_id=user_id,
            source_name=source_name,
            plan=list(plan),
            requirements=requirements or {},
            capabilities=capabilities or [],
            status=ImportStatus.PLANNED,
        )

    # -- queries (the resume brain) -------------------------------------------

    def next_pending_step(self) -> ImportStep | None:
        """The first step not yet applied — what a fresh apply or a resume runs
        next. Returns None when every step is done."""
        for step in self.plan:
            if not step.is_done:
                return step
        return None

    def remaining_steps(self) -> list[ImportStep]:
        return [step for step in self.plan if not step.is_done]

    def is_step_done(self, resource_type: str, resource_name: str) -> bool:
        for step in self.plan:
            if step.key == (resource_type, resource_name):
                return step.is_done
        return False

    @property
    def is_resumable(self) -> bool:
        """A non-terminal import with work left can be (re)applied."""
        return self.status not in TERMINAL_STATUSES and bool(self.remaining_steps())

    @property
    def progress(self) -> tuple[int, int]:
        """(done, total) for a progress readout."""
        done = sum(1 for step in self.plan if step.is_done)
        return done, len(self.plan)

    @property
    def destructive_steps(self) -> list[ImportStep]:
        return [step for step in self.plan if step.destructive]

    # -- transitions ----------------------------------------------------------

    def begin_apply(self) -> None:
        """Enter the apply loop. Idempotent across resumes: a FAILED import can
        re-enter APPLYING, but a terminal one cannot."""
        if self.status in TERMINAL_STATUSES:
            raise ValueError(f"Cannot apply import in {self.status.value} state")
        self.status = ImportStatus.APPLYING
        if self.started_at is None:
            self.started_at = datetime.now(timezone.utc)
        self.error = None
        self.touch()

    def record_step_completed(self, step: ImportStep) -> None:
        step.mark_completed()
        self.touch()

    def record_step_skipped(self, step: ImportStep) -> None:
        step.mark_skipped()
        self.touch()

    def fail(self, step: ImportStep | None, error: str) -> None:
        """Stop on a failed step. The import stays resumable: completed steps are
        preserved, and a later apply picks up from the failed/next step."""
        if step is not None:
            step.mark_failed(error)
        self.status = ImportStatus.FAILED
        self.error = summarize_error(error)
        self.touch()

    def complete(self) -> None:
        """Finish a fully-applied import."""
        if self.remaining_steps():
            raise ValueError(
                "Cannot complete import with "
                f"{len(self.remaining_steps())} step(s) still pending"
            )
        self.status = ImportStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)
        self.touch()

    def cancel(self) -> None:
        if self.status in TERMINAL_STATUSES:
            raise ValueError(f"Cannot cancel import in {self.status.value} state")
        self.status = ImportStatus.CANCELLED
        self.completed_at = datetime.now(timezone.utc)
        self.touch()

    def touch(self) -> None:
        self.updated_at = datetime.now(timezone.utc)
