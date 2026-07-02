"""Resource applier — realizes one import step against the target pod.

The integration boundary between the (pure, tested) import engine and the
backend's own resource services. The engine hands it a step; it reads that
resource's manifest from the staged bundle and dispatches to the matching
service (TableService, AgentService, …) by resource type.

Every resource type has a CREATE and an UPDATE handler, dispatched on
``step.action``. e2e round-trip-verified: tables (schema + seed data), agents
(toolsets, schemas, grants), functions (code), workflows. Wired but not
exercisable in the e2e harness because their create path calls an external
service: schedules (scheduler API), surfaces (connector account), apps
(asset build) — app asset upload itself is still deferred (metadata only). An
unwired/failed step is recorded as a resumable failure, never a half-built pod.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import UUID

from lemma_pod_bundle import (
    GRANT_STEP_KINDS,
    diff_table_columns,
    read_manifest,
    read_table_data,
    resolve_placeholders,
)

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_import.domain.value_objects import ImportAction, ImportStep

# Audit columns Lemma materializes itself — a bundle must not declare them.
_RESERVED_COLUMNS = frozenset({"created_at", "updated_at", "user_id"})

# Grants are applied in a final pass, AFTER every resource exists, because a
# grant can target a resource created later (an agent granted a workflow, or a
# peer agent) — applying them inline would fail to resolve the target. Each
# entry maps a grant-step type to (manifest dir, grantee_type): the step types
# and manifest dirs come from the shared GRANT_STEP_KINDS mapping the plan
# emits from, so plan and applier can't drift; only the backend grantee-type
# names are local.
_GRANTEE_TYPES = {"agents": "AGENT", "functions": "FUNCTION"}
_GRANT_STEP_KINDS: dict[str, tuple[str, str]] = {
    step_kind: (kind, _GRANTEE_TYPES[kind]) for kind, step_kind in GRANT_STEP_KINDS.items()
}


class ImportApplyContext:
    """Per-apply context handed to the applier (satisfies the engine's port)."""

    def __init__(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        bundle_path: Path,
        ctx: Context,
        variables: dict[str, str] | None = None,
    ):
        self.pod_id = pod_id
        self.user_id = user_id
        self.bundle_path = bundle_path
        self.ctx = ctx
        # ${var} -> value map: portable account/member ids resolved at apply time.
        self.variables = variables or {}


class ResourceApplyNotWired(NotImplementedError):
    """Raised for a resource type whose service binding isn't wired yet."""


ResourceHandler = Callable[[dict[str, Any], "ImportApplyContext"], Awaitable[None]]


class BackendResourceApplier:
    """Dispatches import steps to the backend's resource services."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self._create_handlers: dict[str, ResourceHandler] = {
            "tables": self._apply_table,
            "agents": self._apply_agent,
            "functions": self._apply_function,
            "workflows": self._apply_workflow,
            "schedules": self._apply_schedule,
            "surfaces": self._apply_surface,
            "apps": self._apply_app,
        }
        self._update_handlers: dict[str, ResourceHandler] = {
            "tables": self._update_table,
            "agents": self._update_agent,
            "functions": self._update_function,
            "workflows": self._update_workflow,
            "schedules": self._update_schedule,
            "surfaces": self._update_surface,
            "apps": self._update_app,
        }

    async def apply_step(self, step: ImportStep, ctx: ImportApplyContext) -> None:
        # A grant step replays a grantee's grants; its manifest is the
        # agent/function it grants for, read from that resource's dir.
        grant_spec = _GRANT_STEP_KINDS.get(step.resource_type)
        read_kind = grant_spec[0] if grant_spec else step.resource_type
        manifest = read_manifest(ctx.bundle_path, read_kind, step.resource_name)
        # The resource name is the directory name; a manifest may omit it OR
        # carry an explicit null (an unnamed schedule exports as "name": null
        # under a uuid directory) — setdefault alone won't fix the latter, so
        # make it canonical before any handler reads manifest["name"].
        if not manifest.get("name"):
            manifest["name"] = step.resource_name
        # Resolve ${var} placeholders (account/member ids) before the handler
        # constructs entities; unsupplied ones drop their field.
        manifest = resolve_placeholders(manifest, ctx.variables)
        if grant_spec is not None:
            await self._apply_grants_phase(grant_spec[1], step.resource_name, manifest, ctx)
            return
        if step.action is ImportAction.SKIP:
            return
        handlers = (
            self._update_handlers
            if step.action is ImportAction.UPDATE
            else self._create_handlers
        )
        handler = handlers.get(step.resource_type)
        if handler is None:
            raise ResourceApplyNotWired(
                f"Applying '{step.resource_type}' ({step.action.value}) is not wired "
                f"to a backend service yet (resource '{step.resource_name}')."
            )
        await handler(manifest, ctx)

    # -- create handlers --------------------------------------------------------

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
        with _already_exists_is_done():
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
        # Seed only an empty table: re-running (a resume after the table was
        # created but before its rows landed, or an UPDATE of a table already
        # holding live data) must not duplicate or clobber existing rows.
        existing_rows, _ = await record_service.list_records(
            table_ctx, ctx.user_id, limit=1, offset=0, admin_mode=True
        )
        if existing_rows:
            return
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
        with _already_exists_is_done():
            await service.create_agent(
                pod_id=ctx.pod_id,
                user_id=ctx.user_id,
                name=str(manifest["name"]),
                instruction=instruction,
                description=manifest.get("description"),
                icon_url=manifest.get("icon_url"),
                agent_runtime=_agent_runtime_config(manifest.get("agent_runtime")),
                toolsets=manifest.get("toolsets") or None,
                input_schema=manifest.get("input_schema"),
                output_schema=manifest.get("output_schema"),
                visibility=manifest.get("visibility"),
                metadata=manifest.get("metadata"),
                ctx=ctx.ctx,
            )
        # Grants are replayed in the deferred grant pass (see _GRANT_STEP_KINDS),
        # after every resource the grants might target has been created.


    async def _apply_function(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.function.api.dependencies import build_function_service
        from app.modules.function.domain.entities import FunctionEntity

        code = manifest.get("code")
        if code is not None and not isinstance(code, str):
            raise ResourceApplyNotWired(
                f"Function '{manifest.get('name')}' has an unresolved code reference."
            )
        from app.modules.function.domain.entities import FunctionType

        entity = FunctionEntity(
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            name=str(manifest["name"]),
            description=manifest.get("description"),
            icon_url=manifest.get("icon_url"),
            config=manifest.get("config"),
            visibility=str(manifest.get("visibility") or "POD"),
            input_schema=manifest.get("input_schema") or {},
            output_schema=manifest.get("output_schema") or {},
            config_schema=manifest.get("config_schema"),
            type=FunctionType(manifest["type"]) if manifest.get("type") else FunctionType.API,
            python_packages=list(manifest.get("python_packages") or []),
        )
        service = build_function_service(self.uow)
        with _already_exists_is_done():
            await service.create_function(entity, ctx.user_id, code=code, ctx=ctx.ctx)
        # Grants are replayed in the deferred grant pass (see _GRANT_STEP_KINDS),
        # after every resource the grants might target has been created.


    # Org-scoped auth config is not a pod-level grant, so it never resolves in a
    # pod import — skip it. (connector / connector_account DO traverse: the
    # connector slug resolves against the global catalog, and a connector_account
    # is re-pointed to the importing user's own account below.)
    _UNRESOLVABLE_GRANT_TYPES = frozenset({"connector_auth_config"})

    async def _apply_grants_phase(
        self,
        grantee_type: str,
        grantee_name: str,
        manifest: dict[str, Any],
        ctx: ImportApplyContext,
    ) -> None:
        """Deferred grant pass: look up the (already-created) grantee by name and
        replay its grants now that every resource a grant could target exists."""
        grantee_id = await self._resolve_grantee_id(grantee_type, grantee_name, ctx)
        await self._apply_grants(grantee_type, grantee_id, manifest, ctx)

    async def _resolve_grantee_id(
        self, grantee_type: str, name: str, ctx: ImportApplyContext
    ) -> UUID:
        if grantee_type == "AGENT":
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
                pod_id=ctx.pod_id, name=name, requester_user_id=ctx.user_id, ctx=ctx.ctx
            )
            return agent.id
        if grantee_type == "FUNCTION":
            from app.modules.function.api.dependencies import build_function_service

            service = build_function_service(self.uow)
            function = await service.get_function_by_name(
                ctx.pod_id, name, ctx.user_id, raise_not_found=True, ctx=ctx.ctx
            )
            return function.id
        raise ResourceApplyNotWired(f"Grant grantee type '{grantee_type}' is not wired.")

    async def _apply_grants(
        self, grantee_type: str, grantee_id: UUID, manifest: dict[str, Any], ctx: ImportApplyContext
    ) -> None:
        """Replay a resource's grants (table/folder/agent/function/connector
        access) onto the grantee. Grants are name-based, so they resolve in the
        target pod as long as the referenced resource is in the bundle or, for
        connectors, in the importing user's connected accounts."""
        from types import SimpleNamespace

        from app.core.authorization.context import ResourceType
        from app.core.authorization.grants import (
            normalize_pod_resource_grants,
            replace_grantee_resource_grants,
            validate_pod_resource_grant_permissions,
        )

        raw = (manifest.get("permissions") or {}).get("grants") or []
        grant_inputs = []
        for g in raw:
            rtype = str(g.get("resource_type") or "")
            rname = g.get("resource_name")
            if not rname or rtype in self._UNRESOLVABLE_GRANT_TYPES:
                continue
            if rtype == "connector_account":
                # Exported as a provider slug; re-point to the importing user's
                # own account for that provider. Skip if they haven't connected
                # it — the requirements/consent flow surfaces that separately.
                account_id = await self._resolve_user_connector_account(str(rname), ctx)
                if account_id is None:
                    continue
                rname = str(account_id)
            grant_inputs.append(
                SimpleNamespace(
                    resource_type=ResourceType(rtype),
                    resource_name=rname,
                    permission_ids=list(g.get("permission_ids") or []),
                )
            )
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

    async def _resolve_user_connector_account(
        self, provider: str, ctx: ImportApplyContext
    ) -> UUID | None:
        """The importing user's connected account id for a connector provider
        slug (e.g. 'slack'), or None if they have no such connection."""
        org_id = getattr(ctx.ctx, "organization_id", None)
        if org_id is None:
            return None
        from app.core.crypto import get_secret_cipher
        from app.modules.connectors.infrastructure.repositories.account_repository import (
            AccountRepository,
        )

        repo = AccountRepository(self.uow, encryption=get_secret_cipher())
        account = await repo.get_by_user_org_and_app(ctx.user_id, org_id, provider)
        return account.id if account else None

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
        with _already_exists_is_done():
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
        with _already_exists_is_done():
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
        from app.core.helpers.slug import normalize_public_slug
        from app.modules.apps.api.dependencies import build_app_service
        from app.modules.apps.domain.entities import AppEntity

        service = build_app_service(self.uow)
        # Prefer the bundle's clean slug for a readable app URL. public_slug is
        # globally unique, so fall back to a pod-scoped slug only when the clean
        # one is already taken (e.g. the same bundle imported into a second pod).
        base_slug = normalize_public_slug(str(manifest.get("public_slug") or manifest["name"]))
        taken = bool(base_slug) and (
            await service.repository.get_by_public_slug(base_slug) is not None
        )
        if taken:
            pod_suffix = str(ctx.pod_id).replace("-", "")[:8]
            public_slug = f"{base_slug}-{pod_suffix}"
        else:
            public_slug = base_slug
        entity = AppEntity(
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            name=str(manifest["name"]),
            public_slug=public_slug,
            description=manifest.get("description"),
            visibility=manifest.get("visibility") or "POD",
        )
        with _already_exists_is_done():
            await service.create_app_with_context(entity, ctx.user_id, ctx=ctx.ctx)
        # Upload the prebuilt assets if the bundle carries them (no build needed —
        # a dist archive uploads straight to READY). Runs even when the app row
        # already existed: a resume after a partial create must still land them.
        await self._upload_app_assets(service, entity.name, ctx)

    async def _upload_app_assets(
        self, service, app_name: str, ctx: ImportApplyContext
    ) -> None:
        app_dir = ctx.bundle_path / "apps" / app_name
        source_bytes = _read_bytes(app_dir / "source.zip")
        dist_bytes = _read_bytes(app_dir / "dist.zip")
        if source_bytes or dist_bytes:
            await service.upload_bundle(
                ctx.pod_id,
                app_name,
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
        with _already_exists_is_done():
            await ScheduleService(uow=self.uow).create_schedule(create, ctx=ctx.ctx)

    # -- update handlers ----------------------------------------------------------
    # An UPDATE step means the plan found the resource in the target pod; the
    # bundle is the source of truth, so each handler makes the live resource
    # match the manifest through that kind's own update path.

    async def _update_table(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.datastore.api.dependencies import build_table_service
        from app.modules.datastore.domain.datastore_entities import ColumnSchema

        table_name = str(manifest["name"])
        service = build_table_service(self.uow)
        table = await service.get_table(ctx.pod_id, table_name, ctx.ctx)
        current = {
            "primary_key_column": table.primary_key_column,
            "columns": [column.model_dump(exclude_none=True) for column in table.columns],
        }
        # The same classifier the plan used to mark this step destructive, so
        # what gets applied is exactly what the importer consented to.
        diff = diff_table_columns(current, manifest)
        desired_by_name = {
            str(column.get("name")): column for column in manifest.get("columns") or []
        }
        # Drops first (removals + incompatible rebuilds), then adds. An
        # incompatible column (type/required/unique changed) can't be migrated
        # in place — it is dropped and re-added, losing its data; that is the
        # data loss the plan's destructive flag warned about.
        for name in [*diff.to_remove, *diff.incompatible]:
            await service.remove_column(ctx.pod_id, table_name, name, ctx.ctx)
        for column in [*diff.to_add, *(desired_by_name[name] for name in diff.incompatible)]:
            await service.add_column(
                ctx.pod_id, table_name, ColumnSchema(**_column_kwargs(column)), ctx.ctx
            )
        # Metadata the column diff doesn't cover follows the bundle too.
        await service.update_table(
            ctx.pod_id,
            table_name,
            config=manifest.get("config"),
            ctx=ctx.ctx,
            visibility=manifest.get("visibility"),
            enable_rls=bool(manifest["enable_rls"]) if "enable_rls" in manifest else None,
        )
        await self._seed_table(table_name, service, ctx)

    async def _update_agent(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.agent.infrastructure.repositories import AgentRepository
        from app.modules.agent.services.agent_service import AgentService
        from app.modules.pod.services.authorization_factory import (
            create_authorization_service,
        )

        instruction = manifest.get("instruction")
        if not isinstance(instruction, str):
            # Same sidecar limitation as the create path — fail clearly.
            raise ResourceApplyNotWired(
                f"Agent '{manifest.get('name')}' has a non-inline instruction "
                "(sidecar file); $file resolution isn't wired yet."
            )
        service = AgentService(
            agent_repository=AgentRepository(self.uow),
            authorization_service=create_authorization_service(self.uow),
        )
        await service.update_agent(
            pod_id=ctx.pod_id,
            name=str(manifest["name"]),
            instruction=instruction,
            description=manifest.get("description"),
            icon_url=manifest.get("icon_url"),
            agent_runtime=_agent_runtime_config(manifest.get("agent_runtime")),
            toolsets=manifest.get("toolsets") or None,
            input_schema=manifest.get("input_schema"),
            output_schema=manifest.get("output_schema"),
            visibility=manifest.get("visibility"),
            metadata=manifest.get("metadata"),
            requester_user_id=ctx.user_id,
            ctx=ctx.ctx,
        )

    async def _update_function(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.function.api.dependencies import build_function_service
        from app.modules.function.domain.entities import FunctionType, FunctionUpdateEntity

        code = manifest.get("code")
        if code is not None and not isinstance(code, str):
            raise ResourceApplyNotWired(
                f"Function '{manifest.get('name')}' has an unresolved code reference."
            )
        # A code update re-extracts input/output/config schemas server-side —
        # the same derivation create runs, so the bundle's schemas are never
        # trusted over the code they came from.
        update = FunctionUpdateEntity(
            description=manifest.get("description"),
            icon_url=manifest.get("icon_url"),
            code=code,
            config=manifest.get("config"),
            type=FunctionType(manifest["type"]) if manifest.get("type") else None,
            visibility=manifest.get("visibility"),
        )
        service = build_function_service(self.uow)
        await service.update_function(
            ctx.pod_id, str(manifest["name"]), update, ctx.user_id, ctx=ctx.ctx
        )

    async def _update_workflow(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.icon.services.icon_service import IconService
        from app.modules.workflow.domain.flow import FlowUpdateEntity
        from app.modules.workflow.domain.graph import WorkflowEdge
        from app.modules.workflow.domain.nodes import WORKFLOW_NODE_ADAPTER
        from app.modules.workflow.domain.start import FlowStart
        from app.modules.workflow.services.flow_service import FlowService

        name = str(manifest["name"])
        service = FlowService(self.uow, icon_service=IconService())
        flow = await service.get_flow_by_name(
            ctx.pod_id, name, requester_user_id=ctx.user_id, ctx=ctx.ctx
        )
        if flow is None:
            raise ValueError(f"Workflow '{name}' was planned as an update but no longer exists")
        nodes = [WORKFLOW_NODE_ADAPTER.validate_python(n) for n in manifest.get("nodes") or []]
        edges = [WorkflowEdge.model_validate(e) for e in manifest.get("edges") or []]
        start = FlowStart.model_validate(manifest["start"]) if manifest.get("start") else None
        await service.update_flow(
            flow.id,
            FlowUpdateEntity(
                description=manifest.get("description"),
                icon_url=manifest.get("icon_url"),
                start=start,
                visibility=manifest.get("visibility"),
            ),
            requester_user_id=ctx.user_id,
            ctx=ctx.ctx,
        )
        await service.update_flow_graph(
            flow.id, nodes, edges, start, requester_user_id=ctx.user_id, ctx=ctx.ctx
        )

    async def _update_schedule(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.schedule.domain.schedule import ScheduleUpdateEntity
        from app.modules.schedule.services.schedule_service import ScheduleService

        name = str(manifest["name"])
        service = ScheduleService(uow=self.uow)
        existing = await service.schedule_repository.get_by_name(
            pod_id=ctx.pod_id, name=name
        )
        if existing is None:
            # Legacy unnamed schedules are addressable only by id — their
            # bundle name IS the uuid (the plan's existence check matched this
            # step the same way).
            from app.modules.pod_import.infrastructure.existing_resources import (
                schedule_by_uuid_name,
            )

            existing = await schedule_by_uuid_name(
                service.schedule_repository, ctx.pod_id, name
            )
        if existing is None:
            raise ValueError(f"Schedule '{name}' was planned as an update but no longer exists")
        # agent/workflow targets stay name-based; the service resolves them.
        update = ScheduleUpdateEntity(
            config=manifest.get("config"),
            agent_name=manifest.get("agent_name"),
            workflow_name=manifest.get("workflow_name"),
            filter_instruction=manifest.get("filter_instruction"),
            filter_output_schema=manifest.get("filter_output_schema"),
            visibility=manifest.get("visibility"),
        )
        await service.update_schedule(existing.id, update, ctx=ctx.ctx)

    async def _update_surface(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.agent_surfaces.api.dependencies import get_surface_service
        from app.modules.agent_surfaces.domain.entities import (
            SurfaceConfig,
            SurfaceCredentialMode,
        )

        service = get_surface_service(self.uow)
        surface = await service.get_surface_by_platform_in_pod(
            pod_id=ctx.pod_id, platform=str(manifest["platform"]).upper()
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
        await service.update_surface(
            surface_id=surface.id,
            agent_id=agent_id,
            # Re-point the default agent only when the bundle names one — an
            # absent name must not detach the live surface's agent.
            update_agent_id=agent_id is not None,
            config=config,
            credential_mode=credential_mode,
            account_id=UUID(account_id) if isinstance(account_id, str) else account_id,
            ctx=ctx.ctx,
        )

    async def _update_app(self, manifest: dict[str, Any], ctx: ImportApplyContext) -> None:
        from app.modules.apps.api.dependencies import build_app_service
        from app.modules.apps.domain.entities import AppUpdateEntity

        name = str(manifest["name"])
        service = build_app_service(self.uow)
        # public_slug is left alone on update: it's globally unique and already
        # bound to this app's live URL — an import must not re-point it.
        await service.update_app(
            ctx.pod_id,
            name,
            AppUpdateEntity(
                description=manifest.get("description"),
                visibility=manifest.get("visibility"),
            ),
            ctx.user_id,
            ctx=ctx.ctx,
        )
        # Re-upload the bundle's prebuilt assets onto the existing app — the
        # bundle's build replaces the current release, no build step needed.
        await self._upload_app_assets(service, name, ctx)


@contextmanager
def _already_exists_is_done():
    """Idempotence for the one call in a create handler that can collide on a
    re-run: a resource left behind by a prior partial apply means the create is
    done, not failed. Only the create call is guarded — everything after it
    (table seeding, app asset upload) still runs, which the old handler-wide
    catch used to skip."""
    try:
        yield
    except Exception as exc:
        if not _is_already_exists(exc):
            raise


def _is_already_exists(exc: BaseException) -> bool:
    """True if the error means the resource is already present — the services
    raise *AlreadyExistsError / *ConflictError, or say so in the message."""
    name = type(exc).__name__
    if "AlreadyExists" in name or "Conflict" in name:
        return True
    return "already exists" in str(exc).lower()


def _read_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def _agent_runtime_config(data: Any):
    """Rebuild an AgentRuntimeConfig from its serialized manifest form, or None."""
    if not data:
        return None
    from app.modules.agent.domain.value_objects import AgentRuntimeConfig

    return AgentRuntimeConfig(**data)


def _column_kwargs(column: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields ColumnSchema accepts (the manifest may carry extras)."""
    from app.modules.datastore.domain.datastore_entities import ColumnSchema

    allowed = set(ColumnSchema.model_fields)
    return {key: value for key, value in column.items() if key in allowed}
