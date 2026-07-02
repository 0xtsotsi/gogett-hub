"""Adapter answering 'what does the target pod already contain?' for the plan
builder, over the pod's resource repositories.

Existence is a repository-level point lookup per (kind, name) — the same
get-by-name access paths the applier and exporter use, with no authorization
pass (the import route guard already gates the pod) and no entity hydration
beyond the single row. A kind without a lookup answers conservatively: it plans
as a CREATE and is never flagged destructive.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


async def schedule_by_uuid_name(repo: Any, pod_id: UUID, name: str) -> Any:
    """Resolve a schedule whose bundle name is its uuid: unnamed schedules
    export under their id (manifests may carry "name": null), so a name lookup
    misses them — fall back to the id, scoped to the pod. Shared by the plan's
    existence check and the applier's update handler so both address the same
    row."""
    try:
        schedule_id = UUID(name)
    except (ValueError, AttributeError, TypeError):
        return None
    schedule = await repo.get(schedule_id)
    return schedule if schedule is not None and schedule.pod_id == pod_id else None


class PodExistingResources:
    def __init__(self, uow: SqlAlchemyUnitOfWork, pod_id: UUID) -> None:
        self.uow = uow
        self.pod_id = pod_id
        self._lookups = {
            "tables": self._has_table,
            "functions": self._has_function,
            "agents": self._has_agent,
            "workflows": self._has_workflow,
            "schedules": self._has_schedule,
            "surfaces": self._has_surface,
            "apps": self._has_app,
        }

    async def has(self, resource_type: str, name: str) -> bool:
        lookup = self._lookups.get(resource_type)
        return await lookup(name) if lookup else False

    async def table_schema(self, name: str) -> dict[str, Any] | None:
        table = await self._get_table(name)
        if table is None:
            return None
        # The shape diff_table_columns consumes: primary key + column dicts.
        # System columns stay in — the diff filters them itself (same rule the
        # exporter applies), so both sides classify identically.
        return {
            "primary_key_column": table.primary_key_column,
            "columns": [column.model_dump(exclude_none=True) for column in table.columns],
        }

    # -- per-kind lookups -------------------------------------------------------

    async def _get_table(self, name: str):
        from app.modules.datastore.api.dependencies import build_table_service

        repository = build_table_service(self.uow).table_repository
        return await repository.get_by_datastore_and_name(self.pod_id, name)

    async def _has_table(self, name: str) -> bool:
        return await self._get_table(name) is not None

    async def _has_function(self, name: str) -> bool:
        from app.modules.function.infrastructure.repositories import FunctionRepository

        return await FunctionRepository(self.uow).get_by_name(self.pod_id, name) is not None

    async def _has_agent(self, name: str) -> bool:
        from app.modules.agent.infrastructure.repositories import AgentRepository

        agent = await AgentRepository(self.uow).get_by_pod_and_name(
            pod_id=self.pod_id, name=name
        )
        return agent is not None

    async def _has_workflow(self, name: str) -> bool:
        from app.modules.workflow.infrastructure.repositories import (
            SqlAlchemyFlowRepository,
        )

        return await SqlAlchemyFlowRepository(self.uow).get_by_name(self.pod_id, name) is not None

    async def _has_schedule(self, name: str) -> bool:
        from app.modules.schedule.repositories.schedule_repository import (
            ScheduleRepository,
        )

        repo = ScheduleRepository(uow=self.uow)
        schedule = await repo.get_by_name(pod_id=self.pod_id, name=name)
        if schedule is not None:
            return True
        return await schedule_by_uuid_name(repo, self.pod_id, name) is not None

    async def _has_surface(self, name: str) -> bool:
        from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
            SurfaceRepository,
        )

        # A surface's bundle name is its platform slug (the repository
        # upper-cases for the match), mirroring the exporter's naming.
        surface = await SurfaceRepository(self.uow).get_by_pod_and_platform(
            pod_id=self.pod_id, platform=name
        )
        return surface is not None

    async def _has_app(self, name: str) -> bool:
        from app.modules.apps.infrastructure.repositories import AppRepository

        return await AppRepository(self.uow).get_by_name(self.pod_id, name) is not None
