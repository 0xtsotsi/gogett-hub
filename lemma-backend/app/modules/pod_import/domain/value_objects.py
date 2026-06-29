"""Value objects for the pod-import state machine.

An import applies a bundle's resources in dependency order, one step at a time,
persisting a checkpoint after each. These types are the vocabulary of that
process: the status of the whole import, the status/identity of each step, and
the kind of change a step makes. They mirror the FlowRun precedent
(``workflow/domain/run.py``) so the import reads like the rest of the backend.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field

# Failure reasons are stored to be actionable, not full tracebacks.
MAX_ERROR_LENGTH = 2000


def summarize_error(error: str | None) -> str | None:
    """Bound a failure reason to an actionable, storable size (head + tail)."""
    if error is None:
        return None
    text = " ".join(str(error).split())
    if len(text) <= MAX_ERROR_LENGTH:
        return text
    head = text[: MAX_ERROR_LENGTH - 600]
    tail = text[-500:]
    return f"{head} … [truncated] … {tail}"


class ImportStatus(str, Enum):
    """Status of a pod import.

    PLANNED exists once the plan is computed and requirements resolved, before
    the first resource is applied. APPLYING covers the step loop. A run that
    stops mid-apply (failure or interruption) lands in FAILED but stays
    resumable — re-applying skips COMPLETED steps and continues. COMPLETED and
    CANCELLED are terminal.
    """

    PLANNED = "PLANNED"
    APPLYING = "APPLYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Terminal in the sense of "the import loop won't touch it again on its own".
# FAILED is deliberately excluded: a failed import is resumable.
TERMINAL_STATUSES = {ImportStatus.COMPLETED, ImportStatus.CANCELLED}


class ImportStepStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class ImportAction(str, Enum):
    """What a step does to the target pod."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    SKIP = "SKIP"


class ImportStep(BaseModel):
    """One unit of an import: apply a single resource.

    Identity is (resource_type, resource_name) — stable across re-plans, so a
    resumed import can match a persisted checkpoint to the step it represents.
    """

    resource_type: str
    resource_name: str
    action: ImportAction
    status: ImportStepStatus = ImportStepStatus.PENDING
    # Set on UPDATE steps that drop or rebuild columns — surfaced before apply.
    destructive: bool = False
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.resource_type, self.resource_name)

    def mark_running(self) -> None:
        self.status = ImportStepStatus.PENDING
        self.started_at = datetime.now(timezone.utc)

    def mark_completed(self) -> None:
        self.status = ImportStepStatus.COMPLETED
        self.completed_at = datetime.now(timezone.utc)

    def mark_skipped(self) -> None:
        self.status = ImportStepStatus.SKIPPED
        self.completed_at = datetime.now(timezone.utc)

    def mark_failed(self, error: str) -> None:
        self.status = ImportStepStatus.FAILED
        self.completed_at = datetime.now(timezone.utc)
        self.error = summarize_error(error)

    @property
    def is_done(self) -> bool:
        """A step the resume loop should not re-run (succeeded or intentionally
        skipped). FAILED steps are retried on resume."""
        return self.status in (ImportStepStatus.COMPLETED, ImportStepStatus.SKIPPED)
