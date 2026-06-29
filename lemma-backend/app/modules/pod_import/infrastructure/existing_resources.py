"""Adapter answering 'what does the target pod already contain?' for the plan
builder, over the pod's resource repositories.

The query bindings (per resource type, and a table's live schema for destructive
detection) are the integration seam — isolated here so the plan builder stays
pure. Until bound, it answers conservatively: nothing exists, so every resource
plans as a CREATE and no update is flagged destructive.
"""

from __future__ import annotations

from typing import Any

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


class PodExistingResources:
    def __init__(self, uow: SqlAlchemyUnitOfWork, pod_id: str) -> None:
        self.uow = uow
        self.pod_id = pod_id

    def has(self, resource_type: str, name: str) -> bool:
        # Seam: query the matching repo (tables/agents/…) for (pod_id, name).
        return False

    def table_schema(self, name: str) -> dict[str, Any] | None:
        # Seam: return the live table's columns/primary_key for destructive diff.
        return None
