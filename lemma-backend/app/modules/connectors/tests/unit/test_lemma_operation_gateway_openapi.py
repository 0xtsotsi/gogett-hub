"""LemmaOperationGateway dispatches to the OpenAPI executor iff a descriptor exists."""

from __future__ import annotations

import pytest

from app.modules.connectors.infrastructure.adapters.lemma_operation_gateway import (
    LemmaOperationGateway,
)


class _RecordingExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return {"executed": True}


@pytest.mark.asyncio
async def test_execution_present_routes_to_executor():
    executor = _RecordingExecutor()
    gateway = LemmaOperationGateway(http_executor=executor)
    descriptor = {"mode": "openapi", "method": "GET", "path": "/x", "server_url": "https://h"}

    result = await gateway.execute_operation(
        connector_id="github",
        operation_name="op",
        payload={"a": 1},
        third_party_credentials={"access_token": "t"},
        execution=descriptor,
    )

    assert result == {"executed": True}
    assert len(executor.calls) == 1
    assert executor.calls[0]["execution"] == descriptor
    assert executor.calls[0]["payload"] == {"a": 1}


@pytest.mark.asyncio
async def test_no_execution_uses_package_path(monkeypatch):
    # Without a descriptor the gateway must NOT touch the executor; it falls back
    # to the vendored-package client path (which we stub to a sentinel).
    executor = _RecordingExecutor()
    gateway = LemmaOperationGateway(http_executor=executor)

    class _FakeClient:
        async def get_operation(self, name):
            return object()

        async def execute_operation(self, name, payload):
            return {"package": True}

    monkeypatch.setattr(
        "app.modules.connectors.infrastructure.adapters.lemma_operation_gateway.create_lemma_execution_client",
        lambda connector_id, creds: _FakeClient(),
    )

    result = await gateway.execute_operation(
        connector_id="gmail",
        operation_name="gmail_fetch",
        payload={},
        third_party_credentials={"access_token": "t"},
        execution=None,
    )
    assert result == {"package": True}
    assert executor.calls == []
