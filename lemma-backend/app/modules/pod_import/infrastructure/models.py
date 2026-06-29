"""SQLAlchemy model for the pod-import aggregate.

A single row holds the whole import: its ordered plan and per-step status
(JSONB), the requirements/capabilities computed at plan time, and the run
status. Persisting the plan inline is what makes the import resumable — the
checkpoint after each step is just a row update.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.domain.value_objects import (
    ImportStatus,
    ImportStep,
)


class PodImportModel(UUIDAuditBase):
    """A bundle import into a pod, applied step by step and resumable."""

    __tablename__ = "pod_imports"
    __table_args__ = (
        Index("ix_pod_import_pod_status", "pod_id", "status"),
    )

    pod_id: Mapped[UUID] = mapped_column(
        ForeignKey("pods.id", ondelete="CASCADE"), index=True, nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    source_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=ImportStatus.PLANNED.value)

    plan: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    requirements: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    capabilities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __str__(self) -> str:
        return f"import {self.id} ({self.status})"

    def to_entity(self) -> PodImportEntity:
        return PodImportEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pod_id=self.pod_id,
            user_id=self.user_id,
            source_name=self.source_name,
            status=ImportStatus(self.status),
            plan=[ImportStep.model_validate(step) for step in (self.plan or [])],
            requirements=self.requirements or {},
            capabilities=self.capabilities or [],
            error=self.error,
            started_at=self.started_at,
            completed_at=self.completed_at,
        )

    @classmethod
    def from_entity(cls, entity: PodImportEntity) -> "PodImportModel":
        return cls(**_columns_from_entity(entity))

    def apply_entity(self, entity: PodImportEntity) -> None:
        """Copy mutable state from the entity onto an attached row (for updates,
        so the per-step checkpoint persists)."""
        for key, value in _columns_from_entity(entity).items():
            setattr(self, key, value)


def _columns_from_entity(entity: PodImportEntity) -> dict:
    return {
        "id": entity.id,
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
        "pod_id": entity.pod_id,
        "user_id": entity.user_id,
        "source_name": entity.source_name,
        "status": entity.status.value,
        "plan": [step.model_dump(mode="json") for step in entity.plan],
        "requirements": entity.requirements,
        "capabilities": entity.capabilities,
        "error": entity.error,
        "started_at": entity.started_at,
        "completed_at": entity.completed_at,
    }
