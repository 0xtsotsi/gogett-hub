"""Unit tests for the applier — action dispatch, per-call idempotency, and the
conflict classifier — with fake resource services monkeypatched over the lazy
service-builder imports (no DB, no real backend services)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid7

from app.modules.pod_import.domain.value_objects import ImportAction, ImportStep
from app.modules.pod_import.infrastructure.applier import (
    BackendResourceApplier,
    ImportApplyContext,
    _is_already_exists,
)


class AgentAlreadyExistsError(Exception):
    pass


class DatastoreConflictError(Exception):
    pass


def test_conflict_exceptions_are_treated_as_already_exists():
    assert _is_already_exists(AgentAlreadyExistsError("mr-toot"))
    assert _is_already_exists(DatastoreConflictError("nope"))
    assert _is_already_exists(ValueError("Table 'commitments' already exists in this datastore"))


def test_real_errors_are_not_swallowed():
    assert not _is_already_exists(ValueError("invalid column type"))
    assert not _is_already_exists(RuntimeError("connector timeout"))


# -- action dispatch + idempotent table apply --------------------------------------


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _table_bundle(tmp_path: Path, columns: list[dict]) -> Path:
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "bundle"})
    _write(root / "tables" / "widgets" / "widgets.json", {
        "primary_key_column": "id",
        "columns": columns,
    })
    _write(root / "tables" / "widgets" / "data.json", [{"label": "alpha"}])
    return root


def _ctx(bundle_root: Path) -> ImportApplyContext:
    return ImportApplyContext(
        pod_id=uuid7(), user_id=uuid7(), bundle_path=bundle_root, ctx=SimpleNamespace()
    )


def _table_entity(pod_id, columns: list[dict]):
    from app.modules.datastore.domain.datastore_entities import (
        ColumnSchema,
        DatastoreTableEntity,
    )

    return DatastoreTableEntity(
        pod_id=pod_id,
        table_name="widgets",
        primary_key_column="id",
        columns=[ColumnSchema(**c) for c in columns],
        enable_rls=False,
    )


class FakeTableService:
    """Collision on create is switchable; every schema mutation is recorded."""

    def __init__(self, table_entity, *, collide: bool = False):
        self.table_entity = table_entity
        self.collide = collide
        self.created = 0
        self.removed_columns: list[str] = []
        self.added_columns: list[str] = []
        self.meta_updates = 0
        self.schema_manager = SimpleNamespace(get_schema_name=lambda pod_id: "pod_x")

    async def create_table(self, **kwargs):
        if self.collide:
            raise DatastoreConflictError(
                f"Table '{kwargs['table_name']}' already exists in this datastore"
            )
        self.created += 1

    async def get_table(self, pod_id, table_name, ctx):
        return self.table_entity

    async def remove_column(self, pod_id, table_name, column_name, ctx):
        self.removed_columns.append(column_name)

    async def add_column(self, pod_id, table_name, column, ctx):
        self.added_columns.append(column.name)

    async def update_table(self, pod_id, table_name, config=None, ctx=None,
                           visibility=None, enable_rls=None):
        self.meta_updates += 1


class FakeRecordService:
    def __init__(self, existing_rows: int = 0):
        self.existing_rows = existing_rows
        self.seeded: list[list[dict]] = []

    async def list_records(self, table_ctx, user_id, limit=20, offset=0,
                           sorts=None, filters=None, *, admin_mode=False):
        return [{"id": "row"}] * min(self.existing_rows, limit), self.existing_rows

    async def bulk_create_records(self, table_ctx, rows, user_id, upsert=False):
        self.seeded.append(rows)


def _patch_datastore(monkeypatch, table_service, record_service):
    import app.modules.datastore.api.dependencies as datastore_deps

    monkeypatch.setattr(datastore_deps, "build_table_service", lambda uow: table_service)
    monkeypatch.setattr(datastore_deps, "build_record_service", lambda uow: record_service)


_BUNDLE_COLUMNS = [{"name": "id", "type": "UUID"}, {"name": "label", "type": "TEXT"}]


async def test_resumed_table_create_still_seeds(tmp_path, monkeypatch):
    # A prior run created the table but died before its rows landed. On resume
    # the create collides — the step must fall through to seeding, not be
    # declared done with a permanently empty table.
    root = _table_bundle(tmp_path, _BUNDLE_COLUMNS)
    ctx = _ctx(root)
    table_service = FakeTableService(_table_entity(ctx.pod_id, _BUNDLE_COLUMNS), collide=True)
    record_service = FakeRecordService(existing_rows=0)
    _patch_datastore(monkeypatch, table_service, record_service)

    step = ImportStep(resource_type="tables", resource_name="widgets", action=ImportAction.CREATE)
    await BackendResourceApplier(SimpleNamespace()).apply_step(step, ctx)

    assert table_service.created == 0  # collided, treated as done
    assert record_service.seeded == [[{"label": "alpha"}]]


async def test_seeding_skips_a_table_that_already_has_rows(tmp_path, monkeypatch):
    # Re-running a completed-then-reimported table must not duplicate or
    # clobber live rows — seeding only fills an empty table.
    root = _table_bundle(tmp_path, _BUNDLE_COLUMNS)
    ctx = _ctx(root)
    table_service = FakeTableService(_table_entity(ctx.pod_id, _BUNDLE_COLUMNS), collide=True)
    record_service = FakeRecordService(existing_rows=2)
    _patch_datastore(monkeypatch, table_service, record_service)

    step = ImportStep(resource_type="tables", resource_name="widgets", action=ImportAction.CREATE)
    await BackendResourceApplier(SimpleNamespace()).apply_step(step, ctx)

    assert record_service.seeded == []


async def test_update_table_applies_column_diff_without_recreating(tmp_path, monkeypatch):
    # Live table has an extra `phone` column and lacks `label`: the UPDATE step
    # must drop/add per the diff through the table service, never create_table.
    root = _table_bundle(tmp_path, _BUNDLE_COLUMNS)
    ctx = _ctx(root)
    existing_columns = [{"name": "id", "type": "UUID"}, {"name": "phone", "type": "TEXT"}]
    table_service = FakeTableService(_table_entity(ctx.pod_id, existing_columns))
    record_service = FakeRecordService(existing_rows=5)
    _patch_datastore(monkeypatch, table_service, record_service)

    step = ImportStep(resource_type="tables", resource_name="widgets", action=ImportAction.UPDATE)
    await BackendResourceApplier(SimpleNamespace()).apply_step(step, ctx)

    assert table_service.created == 0
    assert table_service.removed_columns == ["phone"]
    assert table_service.added_columns == ["label"]
    assert table_service.meta_updates == 1
    assert record_service.seeded == []  # rows present — never reseeded


async def test_update_step_dispatches_to_the_agent_update_path(tmp_path, monkeypatch):
    # An UPDATE agent step must land on AgentService.update_agent, not create.
    root = tmp_path / "bundle"
    _write(root / "pod.json", {"name": "bundle"})
    _write(root / "agents" / "triage" / "triage.json", {
        "name": "triage", "instruction": "You triage.", "description": "Sorts things.",
    })

    class FakeAgentService:
        def __init__(self):
            self.updated: list[dict] = []
            self.created: list[dict] = []

        async def update_agent(self, **kwargs):
            self.updated.append(kwargs)

        async def create_agent(self, **kwargs):
            self.created.append(kwargs)

    fake = FakeAgentService()
    import app.modules.agent.infrastructure.repositories as agent_repos
    import app.modules.agent.services.agent_service as agent_service_module
    import app.modules.pod.services.authorization_factory as authz_factory

    monkeypatch.setattr(agent_repos, "AgentRepository", lambda uow: SimpleNamespace())
    monkeypatch.setattr(authz_factory, "create_authorization_service", lambda uow: SimpleNamespace())
    monkeypatch.setattr(agent_service_module, "AgentService", lambda **kwargs: fake)

    ctx = _ctx(root)
    step = ImportStep(resource_type="agents", resource_name="triage", action=ImportAction.UPDATE)
    await BackendResourceApplier(SimpleNamespace()).apply_step(step, ctx)

    assert fake.created == []
    assert len(fake.updated) == 1
    assert fake.updated[0]["name"] == "triage"
    assert fake.updated[0]["instruction"] == "You triage."


async def test_skip_steps_touch_nothing(tmp_path, monkeypatch):
    root = _table_bundle(tmp_path, _BUNDLE_COLUMNS)
    ctx = _ctx(root)
    table_service = FakeTableService(_table_entity(ctx.pod_id, _BUNDLE_COLUMNS))
    record_service = FakeRecordService()
    _patch_datastore(monkeypatch, table_service, record_service)

    step = ImportStep(resource_type="tables", resource_name="widgets", action=ImportAction.SKIP)
    await BackendResourceApplier(SimpleNamespace()).apply_step(step, ctx)

    assert table_service.created == 0
    assert table_service.removed_columns == []
    assert record_service.seeded == []
