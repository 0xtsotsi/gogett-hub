"""Invariant tests for the function use-case layer.

These lock the properties the function redesign bought, in the fast (mock) gate:
- the pooled DB connection is NOT held across the sandbox round-trip (create
  schema extraction + API execute),
- the worker path executes a run with NO ctx (trusting the persisted run),
- a JOB dispatch returns PENDING + does not run the sandbox inline.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.function.application.function_run_executor import FunctionRunExecutor
from app.modules.function.application.function_use_cases import FunctionUseCases
from app.modules.function.domain.entities import (
    FunctionEntity,
    FunctionRunEntity,
    FunctionRunStatus,
    FunctionStatus,
    FunctionType,
)
from app.modules.function.services.function_service import ResolvedExecution

pytestmark = pytest.mark.asyncio


class _TrackingUowFactory:
    """A ``uow_factory`` whose context manager flips a shared ``open`` flag, so a
    test can observe whether a pooled connection is held during a given call."""

    def __init__(self):
        self.state = {"open": False, "opens": 0}

    def __call__(self):
        state = self.state

        class _Cm:
            async def __aenter__(self_):
                state["open"] = True
                state["opens"] += 1
                return SimpleNamespace(session=object())

            async def __aexit__(self_, *exc):
                state["open"] = False
                return False

        return _Cm()


@pytest.fixture(autouse=True)
def _stub_pod_context(monkeypatch):
    monkeypatch.setattr(
        "app.core.authorization.scope.resolve_pod_context",
        AsyncMock(return_value=SimpleNamespace(require=AsyncMock())),
    )
    # The executor builds its run-status repo + message bus from each short UoW
    # via inline imports — stub them so the engine's status writes are no-ops.
    monkeypatch.setattr(
        "app.modules.function.infrastructure.repositories.FunctionRunRepository",
        lambda uow, message_bus=None: AsyncMock(),
    )
    monkeypatch.setattr(
        "app.core.infrastructure.events.message_bus.get_message_bus",
        lambda: AsyncMock(),
    )


def _function(**overrides) -> FunctionEntity:
    payload = {
        "id": uuid4(),
        "pod_id": uuid4(),
        "user_id": uuid4(),
        "name": "fn",
        "type": FunctionType.API,
        "status": FunctionStatus.DRAFT,
    }
    payload.update(overrides)
    return FunctionEntity(**payload)


@pytest.mark.asyncio
async def test_create_extracts_schemas_with_no_connection_held(monkeypatch):
    factory = _TrackingUowFactory()
    created = _function(name="with-code")
    service = SimpleNamespace(
        resolve_create=AsyncMock(return_value=created),
        persist_create=AsyncMock(return_value=created),
        get_function_by_name=AsyncMock(return_value=created),
    )
    executor = FunctionRunExecutor(
        uow_factory=factory,
        workspace_service=AsyncMock(),
        storage_factory=lambda function_id: AsyncMock(),
    )
    captured = {}

    async def _fake_extract(user_id, code, code_path, pod_id, function_id):
        captured["open"] = factory.state["open"]
        return ({"a": 1}, {"b": 2}, None)

    executor.extract_schemas = _fake_extract

    use_cases = FunctionUseCases(factory, lambda uow: service, executor)
    result = await use_cases.create_function(
        pod_id=created.pod_id,
        entity=created,
        user_id=created.user_id,
        code="def run(): ...",
        request=SimpleNamespace(),
    )

    # Schema extraction (a sandbox round-trip) ran with no pooled connection held.
    assert captured["open"] is False
    assert factory.state["open"] is False
    # Resolve (insert) + persist happened in distinct short UoWs.
    assert factory.state["opens"] >= 2
    assert result is created


@pytest.mark.asyncio
async def test_execute_api_touches_sandbox_with_no_connection_held(monkeypatch):
    factory = _TrackingUowFactory()
    function = _function(name="api-fn", type=FunctionType.API)
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        input_data={"x": 1},
        status=FunctionRunStatus.PENDING,
    )
    service = SimpleNamespace(
        resolve_execute=AsyncMock(
            return_value=ResolvedExecution(function=function, run=run)
        )
    )

    captured = {}

    async def _fake_get_session(**kwargs):
        # Provisioning the sandbox must happen with no DB connection held.
        captured["open"] = factory.state["open"]
        raise ValueError("stop before the function executor")  # non-recoverable

    workspace_service = AsyncMock()
    workspace_service.get_session = _fake_get_session
    executor = FunctionRunExecutor(
        uow_factory=factory,
        workspace_service=workspace_service,
        storage_factory=lambda function_id: AsyncMock(),
    )

    use_cases = FunctionUseCases(factory, lambda uow: service, executor)
    result = await use_cases.execute_function(
        pod_id=function.pod_id,
        name="api-fn",
        input_data={"x": 1},
        user_id=function.user_id,
        user_email=None,
        request=SimpleNamespace(),
    )

    assert captured["open"] is False
    assert result.status == FunctionRunStatus.FAILED
    assert factory.state["open"] is False


@pytest.mark.asyncio
async def test_execute_run_by_id_worker_path_needs_no_ctx():
    factory = _TrackingUowFactory()
    function = _function(type=FunctionType.JOB)
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        user_email="u@example.com",
        status=FunctionRunStatus.PENDING,
    )
    service = SimpleNamespace(
        load_run_and_function=AsyncMock(return_value=(function, run))
    )
    completed = run.model_copy()
    completed.status = FunctionRunStatus.COMPLETED
    executor = SimpleNamespace(execute=AsyncMock(return_value=completed))

    use_cases = FunctionUseCases(factory, lambda uow: service, executor)
    # No request / no ctx is supplied — the worker trusts the persisted run.
    result = await use_cases.execute_run_by_id(run.id, timeout_seconds=42)

    service.load_run_and_function.assert_awaited_once_with(run.id)
    executor.execute.assert_awaited_once()
    assert executor.execute.await_args.kwargs["timeout_seconds"] == 42
    assert result.status == FunctionRunStatus.COMPLETED


@pytest.mark.asyncio
async def test_execute_job_returns_pending_without_running_sandbox():
    factory = _TrackingUowFactory()
    function = _function(type=FunctionType.JOB)
    run = FunctionRunEntity(
        id=uuid4(),
        function_id=function.id,
        user_id=function.user_id,
        status=FunctionRunStatus.PENDING,
    )
    service = SimpleNamespace(
        resolve_execute=AsyncMock(
            return_value=ResolvedExecution(function=function, run=run)
        )
    )
    executor = SimpleNamespace(execute=AsyncMock())

    use_cases = FunctionUseCases(factory, lambda uow: service, executor)
    result = await use_cases.execute_function(
        pod_id=function.pod_id,
        name="job-fn",
        input_data={},
        user_id=function.user_id,
        user_email=None,
        request=SimpleNamespace(),
    )

    # JOB dispatch returns the PENDING run; the worker runs it later.
    assert result.status == FunctionRunStatus.PENDING
    executor.execute.assert_not_awaited()
