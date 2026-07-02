"""Ports for pod-import module boundaries.

The apply loop depends on two abstractions so it stays pure and testable: a
repository to persist the aggregate after every checkpoint, and a resource
applier that knows how to realize a single step against the target pod. The
real implementations (SQLAlchemy repository, backend-service-backed applier)
live in infrastructure; the service loop never names them.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.domain.value_objects import ImportStep


class ImportRepository(Protocol):
    """Persistence for the import aggregate. ``save`` is called after each step
    so an interrupted apply leaves a durable, resumable checkpoint."""

    async def save(self, entity: PodImportEntity) -> None: ...

    async def get(self, import_id: UUID) -> PodImportEntity | None: ...

    async def rollback(self) -> None:
        """Discard the current transaction's uncommitted work. Called before a
        FAILED checkpoint is saved: a failed step can poison the shared session,
        and the checkpoint must persist on a clean transaction."""
        ...


class ResourceApplier(Protocol):
    """Realizes a single import step against the target pod (create/update one
    resource), or raises to signal the step failed. Implementations dispatch on
    ``step.resource_type`` to the backend's own resource services."""

    async def apply_step(self, step: ImportStep, ctx: "ImportApplyContext") -> None: ...


class ImportApplyContext(Protocol):
    """Opaque per-apply context handed to the applier (pod id, acting user,
    bundle handle). Kept abstract here so the loop carries it without depending
    on storage/auth concretions."""

    pod_id: UUID
    user_id: UUID
