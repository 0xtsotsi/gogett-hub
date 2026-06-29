"""Request/response schemas for the pod-import API.

The response is the same view both renderers consume: the CLI prints it and
polls it; the web wizard renders it step by step and drives apply from it.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.modules.pod_import.domain.entities import PodImportEntity


class ImportStepResponse(BaseModel):
    resource_type: str
    resource_name: str
    action: str
    status: str
    destructive: bool = False
    error: str | None = None

    model_config = ConfigDict(from_attributes=True)


class PodImportResponse(BaseModel):
    id: UUID
    pod_id: UUID
    status: str
    source_name: str | None = None
    plan: list[ImportStepResponse]
    requirements: dict = {}
    capabilities: list = []
    progress_done: int
    progress_total: int
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)

    @classmethod
    def from_entity(cls, entity: PodImportEntity) -> "PodImportResponse":
        done, total = entity.progress
        return cls(
            id=entity.id,
            pod_id=entity.pod_id,
            status=entity.status.value,
            source_name=entity.source_name,
            plan=[
                ImportStepResponse(
                    resource_type=step.resource_type,
                    resource_name=step.resource_name,
                    action=step.action.value,
                    status=step.status.value,
                    destructive=step.destructive,
                    error=step.error,
                )
                for step in entity.plan
            ],
            requirements=entity.requirements,
            capabilities=entity.capabilities,
            progress_done=done,
            progress_total=total,
            error=entity.error,
            started_at=entity.started_at,
            completed_at=entity.completed_at,
        )
