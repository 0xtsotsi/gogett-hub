from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.modules.pod.domain.events import PodCreatedEvent, PodJoinRequestedEvent
from app.modules.pod.domain.pod_entities import PodProvisioningStatus
from app.modules.pod.events import pod_handlers
from app.modules.test_support.fakes import PassthroughEventInbox


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

    await pod_handlers.on_pod_created(
        event, logger, inbox=PassthroughEventInbox()
    )

    parsed = process.await_args.args[0]
    assert str(parsed.pod_id) == event["pod_id"]
    assert process.await_args.args[1] is logger


@pytest.mark.asyncio
async def test_pod_provisioning_noops_when_claim_is_not_available(monkeypatch):
    event = _created_event()
    monkeypatch.setattr(pod_handlers, "_begin_provisioning", AsyncMock(return_value=None))
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
    finish.assert_awaited_once_with(
        event.pod_id, status=PodProvisioningStatus.READY
    )


@pytest.mark.asyncio
async def test_pod_provisioning_persists_sanitized_failure_then_rethrows(monkeypatch):
    class DependencyFailure(RuntimeError):
        code = "DATASTORE_UNAVAILABLE"

    event = _created_event()
    monkeypatch.setattr(pod_handlers, "_begin_provisioning", AsyncMock(return_value=1))
    manager = SimpleNamespace(
        create_datastore_schema=AsyncMock(side_effect=DependencyFailure("secret details"))
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
