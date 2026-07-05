"""Unit tests for per-auth-config operation discovery + backfill orchestration."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.services.connector_service import ConnectorService

SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "https://api.example.com"}],
    "paths": {
        "/ping": {
            "get": {
                "operationId": "ping",
                "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
            }
        },
        "/items/{id}": {
            "get": {
                "operationId": "get-item",
                "parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}],
                "responses": {"200": {"content": {"application/json": {"schema": {"type": "object"}}}}},
            }
        },
    },
}


class _FakeOpRepo:
    def __init__(self):
        self.created = []
        self.deleted = []

    async def delete_by_auth_config(self, auth_config_id):
        self.deleted.append(auth_config_id)
        return 0

    async def create(self, entity):
        self.created.append(entity)
        return entity


class _FakeAcRepo:
    def __init__(self):
        self.updated = []

    async def update(self, entity):
        self.updated.append(entity)
        return entity


class _FakeUow:
    async def commit(self):
        return None


def _service(op_repo, ac_repo):
    svc = ConnectorService.__new__(ConnectorService)
    svc.operation_repository = op_repo
    svc.auth_config_repository = ac_repo
    svc.uow = _FakeUow()
    return svc


def _auth_config(provider_config):
    return AuthConfigEntity(
        organization_id=uuid4(),
        connector_id="openapi",
        name="my-api",
        provider_config=provider_config,
    )


@pytest.mark.asyncio
async def test_discovery_backfills_openapi_ops_from_inline_spec():
    op_repo, ac_repo = _FakeOpRepo(), _FakeAcRepo()
    svc = _service(op_repo, ac_repo)
    auth_config = _auth_config({"spec_inline": SPEC})

    await svc._discover_and_backfill(auth_config, connector=None, kind="http")

    # Deleted existing ops for this auth-config, then created the discovered ones.
    assert op_repo.deleted == [auth_config.id]
    names = {op.name for op in op_repo.created}
    assert names == {"ping", "get_item"}
    for op in op_repo.created:
        assert op.auth_config_id == auth_config.id
        assert op.execution["kind"] == "http"
        assert op.id.startswith("openapi:lemma:")
    # Status recorded on the auth-config metadata.
    assert auth_config.metadata["discovery"]["status"] == "READY"
    assert auth_config.metadata["discovery"]["operation_count"] == 2


@pytest.mark.asyncio
async def test_discovery_noop_for_static_http_connector():
    # A catalog HTTP connector (no spec_url/spec_inline) is not discovery-based.
    op_repo, ac_repo = _FakeOpRepo(), _FakeAcRepo()
    svc = _service(op_repo, ac_repo)
    auth_config = _auth_config({"server_url": "https://api.github.com"})

    await svc._discover_and_backfill(auth_config, connector=None, kind="http")

    assert op_repo.created == []
    assert op_repo.deleted == []


@pytest.mark.asyncio
async def test_discovery_error_sets_error_status(monkeypatch):
    op_repo, ac_repo = _FakeOpRepo(), _FakeAcRepo()
    svc = _service(op_repo, ac_repo)
    auth_config = _auth_config({"server_url": "https://mcp.example.com"})

    async def _boom(**kwargs):
        raise RuntimeError("connection refused")

    import app.modules.connectors.services.discovery as disc

    monkeypatch.setattr(disc, "discover_mcp", _boom)

    await svc._discover_and_backfill(auth_config, connector=None, kind="mcp")

    assert op_repo.created == []  # nothing backfilled on failure
    assert auth_config.metadata["discovery"]["status"] == "ERROR"
    assert "connection refused" in auth_config.metadata["discovery"]["error"]
