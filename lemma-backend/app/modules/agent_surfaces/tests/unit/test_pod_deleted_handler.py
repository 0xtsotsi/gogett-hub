"""Tests for the agent_surfaces pod-deletion cleanup handler."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from functools import partial
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.events import SurfaceWebhookReceivedEvent
from app.modules.agent_surfaces.domain.ingress_context import SurfaceReplyContext
from app.modules.agent_surfaces.events import handlers
from app.modules.test_support.fakes import PassthroughEventInbox


@asynccontextmanager
async def _mock_uow_factory(uow_mock):
    yield uow_mock


@pytest.mark.asyncio
async def test_on_pod_deleted_removes_pod_surfaces(monkeypatch):
    service = AsyncMock()
    service.delete_all_surfaces_for_pod.return_value = 2
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "get_surface_service", lambda uow: service)

    pod_id = uuid4()
    event = {
        "event_type": "pod.deleted",
        "pod_id": str(pod_id),
        "organization_id": str(uuid4()),
    }

    await handlers.on_pod_deleted(
        event,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        inbox=PassthroughEventInbox(),
    )

    service.delete_all_surfaces_for_pod.assert_awaited_once_with(pod_id)


@pytest.mark.asyncio
async def test_on_pod_deleted_ignores_non_delete_events(monkeypatch):
    service = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "get_surface_service", lambda uow: service)

    event = {
        "event_type": "pod.member.removed",
        "pod_id": str(uuid4()),
        "user_id": str(uuid4()),
    }

    await handlers.on_pod_deleted(
        event,
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        inbox=PassthroughEventInbox(),
    )

    service.delete_all_surfaces_for_pod.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_surface_webhook_enqueues_prepared_context(monkeypatch):
    handler = AsyncMock()
    context = _reply_context()
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = context
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        SurfaceWebhookReceivedEvent(source="telegram", payload={"update_id": 1}),
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    handler.try_handle_interaction.assert_awaited_once()
    handler.prepare_ingress.assert_awaited_once()
    job_queue.enqueue.assert_awaited_once()
    assert job_queue.enqueue.await_args.kwargs["payload"]["context"]["mode"] == "reply"


@pytest.mark.asyncio
async def test_handle_surface_webhook_skips_queue_when_interaction_was_handled(
    monkeypatch,
):
    handler = AsyncMock()
    handler.try_handle_interaction.return_value = True
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        SurfaceWebhookReceivedEvent(source="telegram", payload={"callback_query": {}}),
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    handler.prepare_ingress.assert_not_awaited()
    job_queue.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_surface_webhook_skips_queue_when_no_context(monkeypatch):
    handler = AsyncMock()
    handler.try_handle_interaction.return_value = False
    handler.prepare_ingress.return_value = None
    job_queue = AsyncMock()
    uow_mock = AsyncMock()
    monkeypatch.setattr(handlers, "build_surface_event_handler", lambda uow: handler)

    await handlers.handle_surface_webhook(
        SurfaceWebhookReceivedEvent(source="telegram", payload={"update_id": 2}),
        logging.getLogger("test"),
        uow_factory=partial(_mock_uow_factory, uow_mock),
        job_queue=job_queue,
        inbox=PassthroughEventInbox(),
    )

    handler.prepare_ingress.assert_awaited_once()
    job_queue.enqueue.assert_not_awaited()


def _reply_context() -> SurfaceReplyContext:
    return SurfaceReplyContext(
        platform=SurfacePlatform.TELEGRAM,
        event=ParsedInboundSurfaceEvent(
            platform=SurfacePlatform.TELEGRAM,
            conversation_type=ConversationType.EXTERNAL_DM,
            external_thread_id="123",
            sender_external_user_id="123",
            message_text="hi",
            is_dm=True,
            reply_target={"chat_id": "123"},
        ),
        reply_message="hello",
    )
