"""Applier dispatch, substitution, CSV, and upsert idempotency with fakes."""

import json
from pathlib import Path

import pytest

from app.modules.pod_bundle.domain.state import PlanStep, StepAction, StepKind
from app.modules.pod_bundle.infrastructure.applier import (
    BundleApplier,
    StepNotApplicableError,
    _grants_from_payload,
    _read_csv,
    _substitute,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _step(kind: StepKind, name: str, *, action=StepAction.CREATE, destructive=False) -> PlanStep:
    return PlanStep(index=0, kind=kind, name=name, action=action, destructive=destructive)


def _applier(root: Path, **kw) -> BundleApplier:
    return BundleApplier(
        uow=object(), ctx=object(), pod_id=_UUID, user_id=_UUID, bundle_root=root, **kw
    )


from uuid import uuid4  # noqa: E402

_UUID = uuid4()


def test_substitute_replaces_placeholders():
    node = {"a": "${x}", "b": ["${y}", "plain"], "c": 3}
    out = _substitute(node, {"x": "1", "y": "2"})
    assert out == {"a": "1", "b": ["2", "plain"], "c": 3}


def test_substitute_leaves_unresolved():
    assert _substitute("${missing}", {"x": "1"}) == "${missing}"


def test_read_csv_parses_rows(tmp_path):
    p = tmp_path / "data.csv"
    p.write_text("title,score\nfirst,1\nsecond,\n", encoding="utf-8")
    rows = _read_csv(p)
    assert rows == [
        {"title": "first", "score": "1"},
        {"title": "second", "score": None},
    ]


async def test_unsupported_step_raises(tmp_path):
    applier = _applier(tmp_path)
    with pytest.raises(StepNotApplicableError):
        await applier.apply_step(_step(StepKind.APP, "dashboard"))
    with pytest.raises(StepNotApplicableError):
        await applier.apply_step(_step(StepKind.SURFACE, "slack"))


class FakeTableService:
    def __init__(self):
        self.created = []
        self.added = []
        self.removed = []
        self._existing = {}

    async def get_table(self, pod_id, name, ctx):
        return self._existing.get(name)

    async def create_table(self, pod_id, name, pk, columns, config, enable_rls, *, visibility=None, ctx=None):
        self.created.append((name, [c.name for c in columns]))


async def test_table_create_calls_service(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    _write(
        root / "tables" / "leads" / "leads.json",
        {
            "name": "leads",
            "primary_key_column": "id",
            "columns": [
                {"name": "id", "type": "UUID"},
                {"name": "title", "type": "TEXT"},
                {"name": "created_at", "type": "TIMESTAMP", "system": True},
            ],
        },
    )
    fake = FakeTableService()
    monkeypatch.setattr(
        "app.modules.datastore.api.dependencies.build_table_service", lambda uow: fake
    )
    await _applier(root).apply_step(_step(StepKind.TABLE, "leads"))
    # System column dropped; only user columns created.
    assert fake.created == [("leads", ["id", "title"])]


async def test_table_update_adds_new_columns_only(tmp_path, monkeypatch):
    class Existing:
        primary_key_column = "id"

        class _C:
            def __init__(self, n):
                self.name = n

        columns = [_C("id"), _C("title")]

    root = tmp_path / "bundle"
    _write(
        root / "tables" / "leads" / "leads.json",
        {
            "name": "leads",
            "primary_key_column": "id",
            "columns": [
                {"name": "id", "type": "UUID"},
                {"name": "title", "type": "TEXT"},
                {"name": "score", "type": "INTEGER"},
            ],
        },
    )
    fake = FakeTableService()
    fake._existing["leads"] = Existing()

    async def _add_column(pod_id, name, column, ctx):
        fake.added.append(column.name)

    fake.add_column = _add_column
    monkeypatch.setattr(
        "app.modules.datastore.api.dependencies.build_table_service", lambda uow: fake
    )
    # Non-destructive update: adds `score`, never creates or removes.
    await _applier(root).apply_step(
        _step(StepKind.TABLE, "leads", action=StepAction.UPDATE)
    )
    assert fake.added == ["score"]
    assert fake.created == []


# --- grants ------------------------------------------------------------------


_FUNC_ID = uuid4()
_AGENT_ID = uuid4()


class _FakeUow:
    """Applier grant paths need ``uow.session``; the bare ``object()`` used by
    the table tests has none."""

    def __init__(self):
        self.session = object()


def _grant_applier(root: Path) -> BundleApplier:
    return BundleApplier(
        uow=_FakeUow(), ctx=object(), pod_id=_UUID, user_id=_UUID, bundle_root=root
    )


def _patch_grant_layer(monkeypatch) -> dict:
    """Stub the shared authorization grant functions (imported lazily inside the
    applier) and record the calls. Returns the recorder dict."""
    calls: dict = {}

    def _validate(grants):
        calls["validated"] = list(grants)

    async def _normalize(session, *, pod_id, grants):
        calls["normalized"] = list(grants)
        calls["normalize_pod_id"] = pod_id
        return ["NORMALIZED"]

    async def _replace(
        session, *, pod_id, grantee_type, grantee_id, grants, created_by_user_id
    ):
        calls["replace"] = {
            "grantee_type": grantee_type,
            "grantee_id": grantee_id,
            "grants": grants,
            "created_by_user_id": created_by_user_id,
        }

    monkeypatch.setattr(
        "app.core.authorization.grants.validate_pod_resource_grant_permissions",
        _validate,
    )
    monkeypatch.setattr(
        "app.core.authorization.grants.normalize_pod_resource_grants", _normalize
    )
    monkeypatch.setattr(
        "app.core.authorization.grants.replace_grantee_resource_grants", _replace
    )
    return calls


def test_grants_from_payload_parses_and_skips_invalid():
    from app.core.authorization.context import ResourceType

    grants = _grants_from_payload(
        {
            "permissions": {
                "grants": [
                    {
                        "resource_type": "datastore_table",
                        "resource_name": "tickets",
                        "permission_ids": [
                            "datastore.record.read",
                            "datastore.record.write",
                        ],
                    },
                    # Unknown resource_type -> skipped, not fatal.
                    {"resource_type": "nonsense", "resource_name": "x"},
                    # Missing resource_name -> skipped.
                    {"resource_type": "function", "permission_ids": []},
                ]
            }
        }
    )
    assert len(grants) == 1
    assert grants[0].resource_type == ResourceType.DATASTORE_TABLE
    assert grants[0].resource_name == "tickets"
    assert grants[0].permission_ids == [
        "datastore.record.read",
        "datastore.record.write",
    ]


def test_grants_from_payload_accepts_bare_grants_list():
    from app.core.authorization.context import ResourceType

    grants = _grants_from_payload(
        {
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": "triage",
                    "permission_ids": ["function.execute"],
                }
            ]
        }
    )
    assert len(grants) == 1
    assert grants[0].resource_type == ResourceType.FUNCTION
    assert _grants_from_payload({}) == []


class FakeFunctionService:
    def __init__(self):
        self.created = []
        self.updated = []

    async def get_function_by_name(
        self, pod_id, name, user_id, *, raise_not_found=False, ctx=None
    ):
        return None

    async def create_function(self, entity, user_id, code=None, ctx=None):
        entity.id = _FUNC_ID
        self.created.append(entity.name)
        return entity


async def test_function_apply_applies_grants_and_invalidates(tmp_path, monkeypatch):
    from app.core.authorization.context import ResourceType

    root = tmp_path / "bundle"
    _write(
        root / "functions" / "triage" / "triage.json",
        {
            "name": "triage",
            "code": "def main():\n    return {}\n",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "datastore_table",
                        "resource_name": "tickets",
                        "permission_ids": ["datastore.record.read"],
                    }
                ]
            },
        },
    )
    fake = FakeFunctionService()
    monkeypatch.setattr(
        "app.modules.function.api.dependencies.build_function_service",
        lambda uow: fake,
    )
    calls = _patch_grant_layer(monkeypatch)
    invalidated: dict = {}

    async def _invalidate(*, pod_id, function_id):
        invalidated["function_id"] = function_id

    monkeypatch.setattr(
        "app.modules.workspace.services.workspace_tool_runtime."
        "invalidate_function_workspace_env_cache",
        _invalidate,
    )

    await _grant_applier(root).apply_step(_step(StepKind.FUNCTION, "triage"))

    assert fake.created == ["triage"]
    assert calls["replace"]["grantee_type"] == "FUNCTION"
    assert calls["replace"]["grantee_id"] == _FUNC_ID
    assert calls["replace"]["grants"] == ["NORMALIZED"]
    grant = calls["validated"][0]
    assert grant.resource_type == ResourceType.DATASTORE_TABLE
    assert grant.resource_name == "tickets"
    # Cache dropped so the new scopes take effect on the next run.
    assert invalidated["function_id"] == _FUNC_ID


async def test_function_apply_without_grants_skips_grant_layer(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    _write(
        root / "functions" / "noop" / "noop.json",
        {"name": "noop", "code": "def main():\n    return {}\n"},
    )
    fake = FakeFunctionService()
    monkeypatch.setattr(
        "app.modules.function.api.dependencies.build_function_service",
        lambda uow: fake,
    )
    calls = _patch_grant_layer(monkeypatch)
    await _grant_applier(root).apply_step(_step(StepKind.FUNCTION, "noop"))
    assert fake.created == ["noop"]
    assert "replace" not in calls  # no grants -> grant layer untouched


class FakeAgentService:
    class _Agent:
        id = _AGENT_ID

    async def get_agent_by_name(self, *, pod_id, name, ctx=None):
        return self._Agent()


async def test_agent_grants_step_applies_grants(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    _write(
        root / "agents" / "support" / "support.json",
        {
            "name": "support",
            "permissions": {
                "grants": [
                    {
                        "resource_type": "function",
                        "resource_name": "triage",
                        "permission_ids": ["function.execute"],
                    }
                ]
            },
        },
    )
    monkeypatch.setattr(
        "app.modules.agent.api.dependencies.get_agent_service",
        lambda uow: FakeAgentService(),
    )
    calls = _patch_grant_layer(monkeypatch)

    await _grant_applier(root).apply_step(
        _step(StepKind.AGENT_GRANTS, "support", action=StepAction.UPDATE)
    )

    assert calls["replace"]["grantee_type"] == "AGENT"
    assert calls["replace"]["grantee_id"] == _AGENT_ID
    assert calls["replace"]["grants"] == ["NORMALIZED"]


# --- workflows + schedules ---------------------------------------------------


class FakeFlowService:
    def __init__(self):
        self.created = []

    async def get_flow_by_name(self, pod_id, name, requester_user_id=None, ctx=None):
        # A missing flow returns None (does NOT raise) — the applier must treat
        # that as "create", not "already exists".
        return None

    async def create_flow(self, **kwargs):
        self.created.append(kwargs["name"])


async def test_workflow_apply_creates_when_absent(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    _write(
        root / "workflows" / "score_flow" / "score_flow.json",
        {
            "name": "score_flow",
            "start": {"type": "MANUAL"},
            "nodes": [{"id": "n", "type": "END"}],
            "edges": [],
        },
    )
    fake = FakeFlowService()
    monkeypatch.setattr(
        "app.modules.workflow.api.dependencies.get_flow_service", lambda uow: fake
    )
    await _grant_applier(root).apply_step(_step(StepKind.WORKFLOW, "score_flow"))
    # Regression: get_flow_by_name returning None must not be read as "exists".
    assert fake.created == ["score_flow"]


class FakeScheduleService:
    def __init__(self):
        self.created = []

    async def list_schedules(self, *, pod_id, name=None, ctx=None, **kwargs):
        return [], None

    async def create_schedule(self, entity, ctx):
        self.created.append(entity)


async def test_schedule_apply_maps_manifest_to_entity(tmp_path, monkeypatch):
    root = tmp_path / "bundle"
    _write(
        root / "schedules" / "nightly" / "nightly.json",
        {
            "name": "nightly",
            "schedule_type": "TIME",
            "workflow_name": "score_flow",
            "config": {"cron": "0 2 * * *"},
        },
    )
    fake = FakeScheduleService()
    monkeypatch.setattr(
        "app.modules.schedule.api.dependencies.get_schedule_service",
        lambda uow: fake,
    )
    await _grant_applier(root).apply_step(_step(StepKind.SCHEDULE, "nightly"))
    assert len(fake.created) == 1
    entity = fake.created[0]
    assert entity.name == "nightly"
    assert entity.schedule_type.value == "TIME"
    assert entity.workflow_name == "score_flow"
    assert entity.config == {"cron": "0 2 * * *"}
