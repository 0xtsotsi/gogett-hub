"""Resource applier — realizes one import step against the target pod.

The integration boundary between the (pure, tested) import engine and the
backend's own resource services. The engine hands it a step; it reads that
resource's manifest from the staged bundle and dispatches to the matching
service (TableService, AgentService, …) by resource type.

Every resource type has a handler. e2e round-trip-verified: tables (schema +
seed data), agents (toolsets, schemas, grants), functions (code), workflows.
Wired but not exercisable in the e2e harness because their create path calls an
external service: schedules (scheduler API), surfaces (connector account), apps
(asset build) — app asset upload itself is still deferred (metadata only). An
unwired/failed step is recorded as a resumable failure, never a half-built pod.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID

from lemma_pod_bundle import read_manifest, read_table_data

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_import.domain.value_objects import ImportStep

# Audit columns Lemma materializes itself — a bundle must not declare them.
_RESERVED_COLUMNS = frozenset({"created_at", "updated_at", "user_id"})


class ImportApplyContext:
    """Per-apply context handed to the applier (satisfies the engine's port)."""

    def __init__(self, *, pod_id: UUID, user_id: UUID, bundle_path: Path, ctx: Context):
        self.pod_id = pod_id
        self.user_id = user_id
        self.bundle_path = bundle_path
        self.ctx = ctx


class ResourceApplyNotWired(NotImplementedError):
    """Raised for a resource type whose service binding isn't wired yet."""


ResourceHandler = Callable[[dict[str, Any], "ImportApplyContext"], Awaitable[None]]


class BackendResourceApplier:
    """Dispatches import steps to the backend's resource services."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self._handlers: dict[str, ResourceHandler] = {
            "tables": self._apply_table,
            "agents": self._apply_agent,
            "functions": self._apply_function,
            "workflows": self._apply_workflow,
            "schedules": self._apply_schedule,
            "surfaces": self._apply_surface,
            "apps": self._apply_app,
        }

    async def apply_step(self, step: ImportStep, ctx: ImportApplyContext) -> None:
        manifest = read_manifest(ctx.bundle_path, step.resource_type, step.resource_name)
        handler = self._handlers.get(step.resource_type)
        if handler is None:
            raise ResourceApplyNotWired(
                f"Applying '{step.resource_type}' is not wired to a backend service yet "
                f"(resource '{step.resource_name}')."
            )
        await handler(manifest, ctx)

    # -- handlers -------------------------------------------------------------

    async def _apply_table(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.datastore.api.dependencies import build_table_service
        from app.modules.datastore.domain.datastore_entities import ColumnSchema

        columns = [
            ColumnSchema(**_column_kwargs(column))
            for column in manifest.get("columns") or []
            if str(column.get("name") or "") not in _RESERVED_COLUMNS
            and not column.get("system")
        ]
        table_name = str(manifest["name"])
        service = build_table_service(self.uow)
        await service.create_table(
            pod_id=ctx.pod_id,
            table_name=table_name,
            primary_key_column=str(manifest.get("primary_key_column") or "id"),
            columns=columns,
            config=manifest.get("config"),
            enable_rls=bool(manifest.get("enable_rls", False)),
            visibility=manifest.get("visibility"),
            ctx=ctx.ctx,
        )
        await self._seed_table(table_name, service, ctx)

    async def _seed_table(self, table_name, table_service, ctx: ImportApplyContext) -> None:
        """Seed bundled rows. The RecordService validator strips system/auto
        columns, so source ids/timestamps don't conflict."""
        from app.modules.datastore.api.dependencies import build_record_service
        from app.modules.datastore.services.table_context import TableContext

        rows = read_table_data(ctx.bundle_path, table_name)
        if not rows:
            return
        table = await table_service.get_table(ctx.pod_id, table_name, ctx.ctx)
        schema_name = table_service.schema_manager.get_schema_name(ctx.pod_id)
        table_ctx = TableContext.from_table_entity(table, schema_name, events_enabled=True)
        record_service = build_record_service(self.uow)
        await record_service.bulk_create_records(
            table_ctx, rows, ctx.user_id, upsert=True
        )

    async def _apply_agent(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.agent.infrastructure.repositories import AgentRepository
        from app.modules.agent.services.agent_service import AgentService
        from app.modules.pod.services.authorization_factory import (
            create_authorization_service,
        )

        instruction = manifest.get("instruction")
        if not isinstance(instruction, str):
            # A bundle may carry the instruction in a sidecar file ($file ref);
            # resolving those is a follow-up. Fail clearly rather than guess.
            raise ResourceApplyNotWired(
                f"Agent '{manifest.get('name')}' has a non-inline instruction "
                "(sidecar file); $file resolution isn't wired yet."
            )
        service = AgentService(
            agent_repository=AgentRepository(self.uow),
            authorization_service=create_authorization_service(self.uow),
        )
        agent = await service.create_agent(
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            name=str(manifest["name"]),
            instruction=instruction,
            description=manifest.get("description"),
            icon_url=manifest.get("icon_url"),
            toolsets=manifest.get("toolsets") or None,
            input_schema=manifest.get("input_schema"),
            output_schema=manifest.get("output_schema"),
            visibility=manifest.get("visibility"),
            ctx=ctx.ctx,
        )
        await self._apply_grants("AGENT", agent.id, manifest, ctx)


    async def _apply_function(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.function.api.dependencies import build_function_service
        from app.modules.function.domain.entities import FunctionEntity

        code = manifest.get("code")
        if code is not None and not isinstance(code, str):
            raise ResourceApplyNotWired(
                f"Function '{manifest.get('name')}' has an unresolved code reference."
            )
        entity = FunctionEntity(
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            name=str(manifest["name"]),
            description=manifest.get("description"),
            icon_url=manifest.get("icon_url"),
            config=manifest.get("config"),
            visibility=str(manifest.get("visibility") or "POD"),
        )
        service = build_function_service(self.uow)
        created = await service.create_function(entity, ctx.user_id, code=code, ctx=ctx.ctx)
        await self._apply_grants("FUNCTION", created.id, manifest, ctx)


    # Connector-scoped grants reference environment-specific resources that
    # can't resolve by name in a fresh pod — skip them (the requirements/gate
    # path wires connectors up separately).
    _UNRESOLVABLE_GRANT_TYPES = frozenset({"connector", "connector_account", "connector_auth_config"})

    async def _apply_grants(
        self, grantee_type: str, grantee_id: UUID, manifest: dict[str, Any], ctx: ImportApplyContext
    ) -> None:
        """Replay a resource's grants (table/folder/agent/function access) onto
        the freshly-created grantee. Grants are name-based, so they resolve in
        the target pod as long as the referenced resource is in the bundle."""
        from types import SimpleNamespace

        from app.core.authorization.context import ResourceType
        from app.core.authorization.grants import (
            normalize_pod_resource_grants,
            replace_grantee_resource_grants,
            validate_pod_resource_grant_permissions,
        )

        raw = (manifest.get("permissions") or {}).get("grants") or []
        grant_inputs = [
            SimpleNamespace(
                resource_type=ResourceType(g["resource_type"]),
                resource_name=g["resource_name"],
                permission_ids=list(g.get("permission_ids") or []),
            )
            for g in raw
            if g.get("resource_name")
            and str(g.get("resource_type")) not in self._UNRESOLVABLE_GRANT_TYPES
        ]
        if not grant_inputs:
            return
        validate_pod_resource_grant_permissions(grant_inputs)
        normalized = await normalize_pod_resource_grants(
            self.uow.session, pod_id=ctx.pod_id, grants=grant_inputs
        )
        await replace_grantee_resource_grants(
            self.uow.session,
            pod_id=ctx.pod_id,
            grantee_type=grantee_type,
            grantee_id=grantee_id,
            grants=normalized,
            created_by_user_id=ctx.user_id,
        )

    async def _apply_workflow(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.icon.services.icon_service import IconService
        from app.modules.workflow.domain.graph import WorkflowEdge
        from app.modules.workflow.domain.nodes import WORKFLOW_NODE_ADAPTER
        from app.modules.workflow.domain.start import FlowStart
        from app.modules.workflow.services.flow_service import FlowService

        nodes = [WORKFLOW_NODE_ADAPTER.validate_python(n) for n in manifest.get("nodes") or []]
        edges = [WorkflowEdge.model_validate(e) for e in manifest.get("edges") or []]
        start = FlowStart.model_validate(manifest["start"]) if manifest.get("start") else None
        service = FlowService(self.uow, icon_service=IconService())
        await service.create_flow(
            ctx.pod_id,
            str(manifest["name"]),
            manifest.get("description"),
            manifest.get("icon_url"),
            start,
            nodes=nodes,
            edges=edges,
            visibility=manifest.get("visibility"),
            requester_user_id=ctx.user_id,
            ctx=ctx.ctx,
        )

    async def _resolve_agent_id(self, agent_name: str | None, ctx: ImportApplyContext):
        if not agent_name:
            return None
        from app.modules.agent.infrastructure.repositories import AgentRepository
        from app.modules.agent.services.agent_service import AgentService
        from app.modules.pod.services.authorization_factory import (
            create_authorization_service,
        )

        service = AgentService(
            agent_repository=AgentRepository(self.uow),
            authorization_service=create_authorization_service(self.uow),
        )
        agent = await service.get_agent_by_name(
            pod_id=ctx.pod_id, name=agent_name, requester_user_id=ctx.user_id, ctx=ctx.ctx
        )
        return agent.id if agent else None

    async def _apply_surface(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.agent_surfaces.api.dependencies import get_surface_service
        from app.modules.agent_surfaces.domain.entities import (
            SurfaceConfig,
            SurfaceCredentialMode,
            SurfacePlatform,
        )

        agent_id = await self._resolve_agent_id(
            manifest.get("default_agent_name") or manifest.get("agent_name"), ctx
        )
        config = SurfaceConfig.model_validate(manifest["config"]) if manifest.get("config") else None
        credential_mode = (
            SurfaceCredentialMode(manifest["credential_mode"])
            if manifest.get("credential_mode")
            else None
        )
        account_id = manifest.get("account_id")
        await get_surface_service(self.uow).create_surface(
            pod_id=ctx.pod_id,
            agent_id=agent_id,
            platform=SurfacePlatform(str(manifest["platform"]).upper()),
            config=config,
            credential_mode=credential_mode,
            account_id=UUID(account_id) if isinstance(account_id, str) else account_id,
            ctx=ctx.ctx,
        )

    async def _apply_app(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.apps.api.dependencies import build_app_service
        from app.modules.apps.domain.entities import AppEntity

        # public_slug is globally unique, so a bundle's slug can't be reused
        # verbatim in another pod — derive a pod-scoped slug to avoid collisions.
        base_slug = str(manifest.get("public_slug") or manifest["name"])
        pod_suffix = str(ctx.pod_id).replace("-", "")[:8]
        entity = AppEntity(
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            name=str(manifest["name"]),
            public_slug=f"{base_slug}-{pod_suffix}",
            description=manifest.get("description"),
            visibility=manifest.get("visibility") or "POD",
        )
        service = build_app_service(self.uow)
        await service.create_app_with_context(entity, ctx.user_id, ctx=ctx.ctx)
        # Upload the prebuilt assets if the bundle carries them (no build needed —
        # a dist archive uploads straight to READY).
        app_dir = ctx.bundle_path / "apps" / entity.name
        source_bytes = _read_bytes(app_dir / "source.zip")
        dist_bytes = _read_bytes(app_dir / "dist.zip")
        if source_bytes or dist_bytes:
            await service.upload_bundle(
                ctx.pod_id,
                entity.name,
                ctx.user_id,
                source_archive_bytes=source_bytes,
                dist_archive_bytes=dist_bytes,
                ctx=ctx.ctx,
            )

    async def _apply_schedule(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.schedule.domain.schedule import ScheduleCreateEntity
        from app.modules.schedule.services.schedule_service import ScheduleService

        # agent/workflow targets are referenced by name (portable); the service
        # resolves them to ids in the target pod.
        create = ScheduleCreateEntity(
            user_id=ctx.user_id,
            pod_id=ctx.pod_id,
            name=manifest.get("name"),
            schedule_type=manifest["schedule_type"],
            agent_name=manifest.get("agent_name"),
            workflow_name=manifest.get("workflow_name"),
            config=manifest.get("config") or {},
            filter_instruction=manifest.get("filter_instruction"),
            filter_output_schema=manifest.get("filter_output_schema"),
            visibility=manifest.get("visibility"),
        )
        await ScheduleService(uow=self.uow).create_schedule(create, ctx=ctx.ctx)


def _read_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _column_kwargs(column: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields ColumnSchema accepts (the manifest may carry extras)."""
    from app.modules.datastore.domain.datastore_entities import ColumnSchema

    allowed = set(ColumnSchema.model_fields)
    return {key: value for key, value in column.items() if key in allowed}
