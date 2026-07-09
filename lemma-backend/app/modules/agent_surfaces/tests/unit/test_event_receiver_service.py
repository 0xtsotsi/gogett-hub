from __future__ import annotations

import asyncio

import httpx
import pytest
from uuid import UUID

from app.modules.agent_surfaces.services import event_receiver_service
from app.modules.agent_surfaces.domain.entities import (
    SurfaceCredentialMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.telegram.client import normalize_bot_base_url
from app.modules.agent_surfaces.services.event_receiver_service import (
    NativeReceiverCandidate,
    TelegramPollingReceiverRunner,
    _candidate_from_surface,
    _publish_native_receiver_event,
    _receiver_key,
)
from app.modules.agent_surfaces.tests.unit.test_surface_service import _surface_entity


def test_normalize_telegram_base_url_appends_bot_token():
    assert (
        normalize_bot_base_url("https://api.telegram.org/bot", "token-1")
        == "https://api.telegram.org/bottoken-1"
    )
    assert (
        normalize_bot_base_url("https://api.telegram.org/bottoken-1", "token-1")
        == "https://api.telegram.org/bottoken-1"
    )


def test_slack_candidate_uses_app_token_and_account_scoped_key():
    account_id = UUID("019eadff-0000-7000-8000-000000000001")
    surface = _surface_entity(
        surface_type=SurfacePlatform.SLACK,
        account_id=account_id,
        credential_mode=SurfaceCredentialMode.CUSTOM,
    )
    candidate = _candidate_from_surface(
        surface,
        {
            "app_token": "xapp-custom",
            "bot_token": "xoxb-workspace",
        },
    )

    assert isinstance(candidate, NativeReceiverCandidate)
    assert candidate.platform is SurfacePlatform.SLACK
    assert candidate.credential_label == str(account_id)
    assert candidate.key.startswith(f"slack:{account_id}:")


@pytest.mark.asyncio
async def test_telegram_polling_retries_transient_conflict_after_resetting_webhook(
    monkeypatch,
):
    runner = TelegramPollingReceiverRunner(
        NativeReceiverCandidate(
            key=_receiver_key("telegram", "system", "token"),
            platform=SurfacePlatform.TELEGRAM,
            surface_ids=(),
            credential_label="system",
            credentials={"bot_token": "token"},
        )
    )
    calls: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(event_receiver_service.asyncio, "sleep", fake_sleep)

    async def fake_telegram_api(client, base_url, method, params):
        calls.append(method)
        if method == "deleteWebhook":
            assert params == {"drop_pending_updates": False}
            return {"ok": True}
        if calls.count("getUpdates") > 1:
            raise asyncio.CancelledError
        request = httpx.Request("POST", f"{base_url}/{method}")
        response = httpx.Response(409, request=request)
        raise httpx.HTTPStatusError("conflict", request=request, response=response)

    runner._telegram_api = fake_telegram_api  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await runner.run()

    assert calls == ["deleteWebhook", "getUpdates", "getUpdates"]
    assert sleeps == [5]


@pytest.mark.asyncio
async def test_publish_native_receiver_event_emits_surface_webhook_event(monkeypatch):
    published = []

    async def publish(stream, event):
        published.append((stream, event))

    monkeypatch.setattr(
        event_receiver_service.EventPublisher,
        "publish",
        publish,
    )

    await _publish_native_receiver_event(
        source="telegram",
        payload={"update_id": 123},
        receiver_key=None,
    )

    assert len(published) == 1
    stream, event = published[0]
    assert stream == "surface_events"
    assert event.source == "telegram"
    assert event.payload == {"update_id": 123}
    assert event.headers == {"x-lemma-surface-event-mode": "native_receiver"}
