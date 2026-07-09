from __future__ import annotations

import asyncio
import json
from io import BytesIO
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import UploadFile

from app.app import RequestBodyLimitMiddleware
from app.core.api.uploads import (
    UPLOAD_MEMORY_SPOOL_BYTES,
    UploadBudget,
    read_upload_limited,
    stage_upload_limited,
)
from app.core.domain.errors import DomainError, PayloadTooLargeError
from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.inbox import (
    InboxConsumer,
    InboxStatus,
    stable_event_id,
)
from app.core.infrastructure.events.outbox import ClaimedEvent, OutboxDispatcher
from app.core.redaction import REDACTED, redact_text, redact_value


class _TestEvent(DomainEvent):
    event_type: str = "test.created"
    value: str

    @classmethod
    def stream_name(cls) -> str:
        return "test_events"


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list[object] = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement):
        self.statements.append(statement)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_uow_stages_event_before_database_commit() -> None:
    session = _FakeSession()
    uow = SqlAlchemyUnitOfWork(session)  # type: ignore[arg-type]
    event = _TestEvent(value="committed")
    uow.collect_events([event])

    await uow.commit()

    assert session.committed is True
    assert len(session.statements) == 1
    params = session.statements[0].compile().params
    assert params["event_type"] == "test.created"
    assert params["stream"] == "test_events"
    assert params["id"] == event.event_id
    assert params["payload"]["value"] == "committed"
    assert not uow.has_pending_events()


class _MemoryInbox(InboxConsumer):
    def __init__(self, attempt: int = 1) -> None:
        super().__init__(AsyncMock(), max_attempts=10)  # type: ignore[arg-type]
        self.attempt = attempt
        self.finished: list[tuple[InboxStatus, str | None]] = []

    async def _claim(self, consumer, event_id, event_type):
        return self.attempt

    async def _finish(
        self, consumer, event_id, status, *, error_type: str | None = None
    ) -> None:
        self.finished.append((status, error_type))


@pytest.mark.asyncio
async def test_inbox_classifies_success_retry_terminal_and_dead_letter() -> None:
    event = _TestEvent(value="one")

    success = _MemoryInbox()
    await success.process("consumer", event, AsyncMock(return_value=None))
    assert success.finished == [(InboxStatus.COMPLETED, None)]

    retry = _MemoryInbox()

    async def infrastructure_failure() -> None:
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        await retry.process("consumer", event, infrastructure_failure)
    assert retry.finished == [(InboxStatus.RETRYING, "RuntimeError")]

    terminal = _MemoryInbox()

    async def validation_failure() -> None:
        raise DomainError("invalid", status_code=422)

    await terminal.process("consumer", event, validation_failure)
    assert terminal.finished == [(InboxStatus.TERMINAL, "DomainError")]

    dead_letter = _MemoryInbox(attempt=10)
    await dead_letter.process("consumer", event, infrastructure_failure)
    assert dead_letter.finished == [(InboxStatus.DEAD_LETTER, "RuntimeError")]


def test_legacy_event_id_is_deterministic() -> None:
    payload = {"event_type": "legacy", "record_id": "42", "payload": {"x": 1}}
    assert stable_event_id(payload) == stable_event_id(dict(payload))
    assert stable_event_id(payload) != stable_event_id({**payload, "record_id": "43"})


class _OutboxUnderTest(OutboxDispatcher):
    def __init__(self, bus) -> None:
        super().__init__(AsyncMock(), bus)  # type: ignore[arg-type]
        self.published: list[object] = []
        self.failed: list[tuple[object, str]] = []

    async def _mark_published(self, event_id) -> None:
        self.published.append(event_id)

    async def _mark_failed(self, event, exc) -> None:
        self.failed.append((event, type(exc).__name__))


@pytest.mark.asyncio
async def test_outbox_dispatcher_persists_publish_outcome() -> None:
    event = ClaimedEvent(
        id=uuid4(),
        stream="test_events",
        event_type="test.created",
        payload={"event_type": "test.created"},
        attempts=1,
        occurred_at=_TestEvent(value="x").occurred_at,
    )
    bus = AsyncMock()
    dispatcher = _OutboxUnderTest(bus)
    await dispatcher._publish(event)
    assert dispatcher.published == [event.id]
    assert dispatcher.failed == []

    bus.publish.side_effect = RuntimeError("redis down")
    await dispatcher._publish(event)
    assert dispatcher.failed == [(event, "RuntimeError")]


@pytest.mark.asyncio
async def test_outbox_run_recovers_from_infrastructure_failure(monkeypatch) -> None:
    dispatcher = _OutboxUnderTest(AsyncMock())
    calls = 0

    async def dispatch_once() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database migration not ready")
        raise asyncio.CancelledError

    dispatcher.dispatch_once = dispatch_once  # type: ignore[method-assign]
    sleep = AsyncMock()
    monkeypatch.setattr("app.core.infrastructure.events.outbox.asyncio.sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.run()

    assert calls == 2
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_reader_enforces_field_and_batch_limits() -> None:
    upload = UploadFile(filename="sample.bin", file=BytesIO(b"abcdef"))
    result = await read_upload_limited(upload, max_bytes=6, field="file")
    assert result.data == b"abcdef"
    assert result.size == 6
    assert len(result.sha256) == 64

    oversized = UploadFile(filename="large.bin", file=BytesIO(b"abcdefg"))
    with pytest.raises(PayloadTooLargeError):
        await read_upload_limited(oversized, max_bytes=6, field="file")

    budget = UploadBudget(max_bytes=5, field="batch")
    with pytest.raises(PayloadTooLargeError):
        budget.consume(6)


@pytest.mark.asyncio
async def test_upload_staging_spills_and_always_cleans_up(tmp_path, monkeypatch) -> None:
    staged_path = tmp_path / "upload.staged"
    monkeypatch.setattr(
        "app.core.api.uploads._new_staging_path", lambda: staged_path
    )
    content = b"x" * (UPLOAD_MEMORY_SPOOL_BYTES + 1)
    upload = UploadFile(filename="large.bin", file=BytesIO(content))

    with pytest.raises(RuntimeError, match="storage failed"):
        async with stage_upload_limited(
            upload,
            max_bytes=len(content),
            field="file",
        ) as staged:
            assert staged.path == staged_path
            assert staged.path.exists()
            assert staged.path.stat().st_size == len(content)
            assert await staged.read_bytes() == content
            raise RuntimeError("storage failed")

    assert not staged_path.exists()


@pytest.mark.asyncio
async def test_request_body_limit_rejects_chunked_body_without_content_length() -> None:
    async def app(scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app, max_bytes=5)
    chunks = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )
    sent: list[dict] = []

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    await middleware(
        {"type": "http", "method": "POST", "path": "/upload", "headers": []},
        receive,
        send,
    )
    assert sent[0]["status"] == 413
    body = json.loads(sent[1]["body"])
    assert body["code"] == "UPLOAD_TOO_LARGE"


def test_canary_secrets_are_redacted_recursively_and_in_text() -> None:
    canary = "CANARY-SECRET-123"
    value = {
        "authorization": f"Bearer {canary}",
        "provider": {
            "refresh_token": canary,
            "callback": f"https://provider.test/cb?code={canary}&state={canary}",
        },
    }
    rendered = json.dumps(redact_value(value))
    assert canary not in rendered
    assert REDACTED in rendered
    assert canary not in redact_text(f"provider failed api_key={canary}")
    assert canary not in redact_text(
        f"GET https://provider.test/cb?code={canary} failed"
    )
