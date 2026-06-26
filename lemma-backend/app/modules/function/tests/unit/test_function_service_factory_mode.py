"""Regression tests (DB pool exhaustion): FunctionService factory mode releases
the pooled DB connection during sandbox round-trips.

In factory mode the service opens a SHORT UoW per DB write, so the multi-second
sandbox calls — schema extraction on create, and the function executor on a
synchronous (API-type) run — must execute with **no UoW open**. A tracking
``uow_factory`` flips an ``open`` flag so the tests can assert the connection is
not held when the sandbox is touched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import app.core.infrastructure.events.message_bus as message_bus_module
import app.modules.function.infrastructure.repositories as repositories_module
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.services.function_service import FunctionService
from app.modules.test_support.authz import allow_all_context

pytestmark = pytest.mark.asyncio


class _TrackingUowFactory:
    """A ``uow_factory`` whose context manager flips a shared ``open`` flag."""

    def __init__(self):
        self.state = {"open": False, "opens": 0}

    def __call__(self):
        state = self.state

        class _Cm:
            async def __aenter__(self_inner):
                state["open"] = True
                state["opens"] += 1
                return SimpleNamespace(session=object())

            async def __aexit__(self_inner, *exc):
                state["open"] = False
                return False

        return _Cm()


def _factory_service(
    factory, *, function_repo, run_repo, workspace_service, monkeypatch
) -> FunctionService:
    # In factory mode the service builds the repositories from each short UoW via
    # inline imports — patch them (and the message-bus getter) to our fakes.
    monkeypatch.setattr(
        repositories_module,
        "FunctionRepository",
        lambda uow, message_bus=None: function_repo,
    )
    monkeypatch.setattr(
        repositories_module,
        "FunctionRunRepository",
        lambda uow, message_bus=None: run_repo,
    )
    monkeypatch.setattr(message_bus_module, "get_message_bus", lambda: AsyncMock())
    return FunctionService(
        function_repository=None,
        run_repository=None,
        workspace_service=workspace_service,
        storage_factory=lambda function_id: AsyncMock(),
        job_queue=AsyncMock(),
        authorization_service=None,
        uow_factory=factory,
    )


def _function_entity(**overrides) -> FunctionEntity:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "user_id": uuid4(),
        "name": "test-function",
        "description": None,
        "input_schema": {},
        "output_schema": {},
        "config_schema": None,
        "config": None,
        "status": FunctionStatus.DRAFT,
        "code_path": None,
        "type": FunctionType.API,
    }
    payload.update(overrides)
    return FunctionEntity(**payload)


async def test_create_function_extracts_schemas_with_no_uow_open(monkeypatch):
    factory = _TrackingUowFactory()
    function_repo = AsyncMock()
    function_repo.get_by_name.return_value = None
    created = _function_entity(name="with-code")
    function_repo.create.return_value = created
    updated = created.model_copy()
    updated.status = FunctionStatus.READY
    function_repo.update.return_value = updated

    service = _factory_service(
        factory,
        function_repo=function_repo,
        run_repo=AsyncMock(),
        workspace_service=AsyncMock(),
        monkeypatch=monkeypatch,
    )

    captured = {}

    async def _fake_extract(*args, **kwargs):
        captured["open"] = factory.state["open"]
        return ({"a": 1}, {"b": 2}, None)

    service._extract_schemas = _fake_extract

    entity = _function_entity(id=None, name="with-code")
    result = await service.create_function(
        entity, created.user_id, code="# code", ctx=allow_all_context()
    )

    # Schema extraction (a sandbox round-trip) ran with no pooled connection held.
    assert captured["open"] is False
    assert result.status == FunctionStatus.READY
    assert factory.state["open"] is False
    # Insert and schema-write happened in distinct short UoWs.
    assert factory.state["opens"] >= 2


async def test_execute_api_function_touches_sandbox_with_no_uow_open(monkeypatch):
    factory = _TrackingUowFactory()
    function = _function_entity(name="api-fn", type=FunctionType.API)
    function_repo = AsyncMock()
    function_repo.get_by_name.return_value = function

    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        input_data={"x": 1},
        status=FunctionRunStatus.PENDING,
    )
    run_repo = AsyncMock()
    run_repo.create_run.return_value = run
    run_repo.update_run.return_value = run
    run_repo.update_run_and_collect.return_value = run

    captured = {}

    async def _fake_get_session(**kwargs):
        # Provisioning the sandbox must happen with no DB connection held.
        captured["open"] = factory.state["open"]
        raise ValueError("stop before the function executor")  # non-recoverable

    workspace_service = AsyncMock()
    workspace_service.get_session = _fake_get_session

    service = _factory_service(
        factory,
        function_repo=function_repo,
        run_repo=run_repo,
        workspace_service=workspace_service,
        monkeypatch=monkeypatch,
    )

    result = await service.execute_function(
        function.pod_id,
        "api-fn",
        {"x": 1},
        function.user_id,
        ctx=allow_all_context(),
    )

    assert captured["open"] is False
    assert result.status == FunctionRunStatus.FAILED
    assert factory.state["open"] is False
