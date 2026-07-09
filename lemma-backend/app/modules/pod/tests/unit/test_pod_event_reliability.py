from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.pod.domain.events import PodCreatedEvent, PodJoinRequestedEvent
from app.modules.pod.domain.pod_entities import PodProvisioningStatus
from app.modules.pod.events import pod_handlers
from app.modules.test_support.fakes import PassthroughEventInbox


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _ProvisioningSession:
    def __init__(self, pod=None, rows=()) -> None:
        self.pod = pod
        self.rows = list(rows)
        self.get = AsyncMock(return_value=pod)

    def begin(self):
        return _AsyncContext(self)

    async def scalar(self, statement):
        del statement
        return self.pod

    async def execute(self, statement):
        del statement
        return SimpleNamespace(all=lambda: self.rows)


def _created_event() -> PodCreatedEvent:
    return PodCreatedEvent(
        pod_id=uuid4(),
        organization_id=uuid4(),
        creator_id=uuid4(),
        name="reliable-pod",
    )


@pytest.mark.asyncio
async def test_pod_created_wrapper_ignores_other_events():
    inbox = SimpleNamespace(process=AsyncMock())

    await pod_handlers.on_pod_created(
        {"event_type": "pod.updated"}, Mock(), inbox=inbox
    )

    inbox.process.assert_not_awaited()


@pytest.mark.asyncio
async def test_pod_created_wrapper_claims_inbox_before_processing(monkeypatch):
    event = _created_event().model_dump(mode="json")
    process = AsyncMock()
    monkeypatch.setattr(pod_handlers, "_process_pod_created", process)
    logger = Mock()

    await pod_handlers.on_pod_created(event, logger, inbox=PassthroughEventInbox())

    parsed = process.await_args.args[0]
    assert str(parsed.pod_id) == event["pod_id"]
    assert process.await_args.args[1] is logger


@pytest.mark.asyncio
async def test_pod_provisioning_noops_when_claim_is_not_available(monkeypatch):
    event = _created_event()
    monkeypatch.setattr(
        pod_handlers, "_begin_provisioning", AsyncMock(return_value=None)
    )
    manager = SimpleNamespace(create_datastore_schema=AsyncMock())
    monkeypatch.setattr(pod_handlers, "SchemaManager", lambda: manager)

    await pod_handlers._process_pod_created(event, Mock())

    manager.create_datastore_schema.assert_not_awaited()


@pytest.mark.asyncio
async def test_pod_provisioning_marks_ready_only_after_schema_creation(monkeypatch):
    event = _created_event()
    monkeypatch.setattr(pod_handlers, "_begin_provisioning", AsyncMock(return_value=3))
    manager = SimpleNamespace(create_datastore_schema=AsyncMock())
    monkeypatch.setattr(pod_handlers, "SchemaManager", lambda: manager)
    finish = AsyncMock()
    monkeypatch.setattr(pod_handlers, "_finish_provisioning", finish)

    await pod_handlers._process_pod_created(event, Mock())

    manager.create_datastore_schema.assert_awaited_once_with(event.pod_id)
    finish.assert_awaited_once_with(event.pod_id, status=PodProvisioningStatus.READY)


@pytest.mark.asyncio
async def test_pod_provisioning_persists_sanitized_failure_then_rethrows(monkeypatch):
    class DependencyFailure(RuntimeError):
        code = "DATASTORE_UNAVAILABLE"

    event = _created_event()
    monkeypatch.setattr(pod_handlers, "_begin_provisioning", AsyncMock(return_value=1))
    manager = SimpleNamespace(
        create_datastore_schema=AsyncMock(
            side_effect=DependencyFailure("secret details")
        )
    )
    monkeypatch.setattr(pod_handlers, "SchemaManager", lambda: manager)
    finish = AsyncMock()
    monkeypatch.setattr(pod_handlers, "_finish_provisioning", finish)
    logger = Mock()

    with pytest.raises(DependencyFailure, match="secret details"):
        await pod_handlers._process_pod_created(event, logger)

    finish.assert_awaited_once_with(
        event.pod_id,
        status=PodProvisioningStatus.FAILED,
        error_type="DependencyFailure",
        error_code="DATASTORE_UNAVAILABLE",
    )
    assert "secret details" not in str(logger.error.call_args)


@pytest.mark.asyncio
async def test_provisioning_claim_and_finish_cover_durable_state_guards(monkeypatch):
    now = datetime.now(timezone.utc)

    def pod(**overrides):
        values = {
            "is_deleted": False,
            "provisioning_status": PodProvisioningStatus.UNKNOWN.value,
            "provisioning_started_at": None,
            "provisioning_attempts": 0,
            "provisioning_completed_at": now,
            "provisioning_error_type": "OldError",
            "provisioning_error_code": "OLD",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    blocked = [
        None,
        pod(is_deleted=True),
        pod(provisioning_status=PodProvisioningStatus.READY.value),
        pod(
            provisioning_status=PodProvisioningStatus.PROVISIONING.value,
            provisioning_started_at=now - timedelta(seconds=10),
            provisioning_attempts=1,
        ),
        pod(provisioning_attempts=10),
    ]
    for candidate in blocked:
        session = _ProvisioningSession(candidate)
        monkeypatch.setattr(
            pod_handlers,
            "async_session_maker",
            lambda session=session: _AsyncContext(session),
        )
        assert await pod_handlers._begin_provisioning(uuid4()) is None

    claimable = pod(provisioning_attempts=4)
    session = _ProvisioningSession(claimable)
    monkeypatch.setattr(
        pod_handlers, "async_session_maker", lambda: _AsyncContext(session)
    )
    assert await pod_handlers._begin_provisioning(uuid4()) == 5
    assert claimable.provisioning_status == PodProvisioningStatus.PROVISIONING.value
    assert claimable.provisioning_completed_at is None
    assert claimable.provisioning_error_type is None
    assert claimable.provisioning_error_code is None

    await pod_handlers._finish_provisioning(
        uuid4(),
        status=PodProvisioningStatus.FAILED,
        error_type="ConnectionError",
        error_code="DATASTORE_UNAVAILABLE",
    )
    assert claimable.provisioning_status == PodProvisioningStatus.FAILED.value
    assert claimable.provisioning_completed_at is not None
    assert claimable.provisioning_error_type == "ConnectionError"

    missing = _ProvisioningSession(None)
    monkeypatch.setattr(
        pod_handlers, "async_session_maker", lambda: _AsyncContext(missing)
    )
    await pod_handlers._finish_provisioning(uuid4(), status=PodProvisioningStatus.READY)


@pytest.mark.asyncio
async def test_reconciler_repairs_existing_schema_and_redrives_missing_schema(
    monkeypatch,
):
    existing_id, missing_id = uuid4(), uuid4()
    org_id, user_id = uuid4(), uuid4()
    rows = [
        (existing_id, org_id, user_id, "existing"),
        (missing_id, org_id, user_id, "missing"),
    ]
    query_session = _ProvisioningSession(rows=rows)
    monkeypatch.setattr(
        pod_handlers,
        "async_session_maker",
        lambda: _AsyncContext(query_session),
    )

    manager = SimpleNamespace(
        datastore_schema_exists=AsyncMock(side_effect=[True, False])
    )
    monkeypatch.setattr(pod_handlers, "SchemaManager", lambda: manager)
    finish = AsyncMock()
    monkeypatch.setattr(pod_handlers, "_finish_provisioning", finish)

    model = SimpleNamespace(provisioning_status=PodProvisioningStatus.UNKNOWN.value)
    uow = SimpleNamespace(
        session=SimpleNamespace(get=AsyncMock(return_value=model)),
        collect_events=Mock(),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        pod_handlers,
        "SessionUnitOfWorkFactory",
        lambda session_maker: lambda: _AsyncContext(uow),
    )

    await pod_handlers.reconcile_pod_provisioning()

    finish.assert_awaited_once_with(existing_id, status=PodProvisioningStatus.READY)
    assert model.provisioning_status == PodProvisioningStatus.PROVISIONING.value
    event = uow.collect_events.call_args.args[0][0]
    assert event.pod_id == missing_id
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_join_request_wrapper_projects_inside_inbox(monkeypatch):
    event = PodJoinRequestedEvent(
        pod_id=uuid4(),
        organization_id=uuid4(),
        requester_user_id=uuid4(),
        join_request_id=uuid4(),
    ).model_dump(mode="json")
    process = AsyncMock()
    monkeypatch.setattr(pod_handlers, "_process_pod_join_requested", process)
    logger = Mock()
    uow_factory = Mock()
    email_port = Mock()

    await pod_handlers.on_pod_join_requested(
        event,
        logger,
        uow_factory=uow_factory,
        email_port=email_port,
        inbox=PassthroughEventInbox(),
    )

    parsed = process.await_args.args[0]
    assert str(parsed.join_request_id) == event["join_request_id"]
    assert process.await_args.kwargs == {
        "uow_factory": uow_factory,
        "email_port": email_port,
    }
