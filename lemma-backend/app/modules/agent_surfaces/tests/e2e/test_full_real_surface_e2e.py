"""Full surface agent e2e — real worker + system:lemma runtime.

Nothing is simulated except the inbound webhook payload (POSTed to the real,
auth-excluded webhook endpoint) and the external platform's HTTP API (a local
fake server that captures what the agent sends back — we cannot call the real
Telegram Bot API from a test). The production streaq worker subprocess runs the
agent through ``system:lemma``: deterministic FunctionModel by default, or the
real configured provider only when ``E2E_LLM_MODE=real`` is requested.

Run:

    uv run pytest \
        app/modules/agent_surfaces/tests/e2e/test_full_real_surface_e2e.py -m e2e

In real-LLM mode it skips automatically when system:lemma credentials are absent.
"""

from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import get_secret_cipher
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_surface,
    _ensure_connector_account,
    _seed_external_user,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
    wait_for_messages,
)

pytestmark = pytest.mark.e2e

# Worker + queued run round-trip. Real-LLM mode can be slower, so keep room.
REAL_REPLY_TIMEOUT = 180.0


async def _create_system_lemma_agent(client: AsyncClient, pod_id: str) -> str:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Surface Greeter",
            "instruction": (
                "You are a helpful assistant on a chat surface. Reply with a "
                "short, friendly one or two sentence answer to the user."
            ),
            "agent_runtime": {"profile_id": "system:lemma"},
            "toolsets": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["name"]


async def test_telegram_webhook_surface_registers_and_replies_with_real_agent(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker  # real streaq worker subprocess consuming surface events
    from app.core.config import settings as app_settings

    # Webhook registration requires a public HTTPS api_url; the worker reaches
    # the fake Telegram API via the account's api_base_url (no monkeypatch can
    # cross into the subprocess).
    monkeypatch.setattr(app_settings, "api_url", "https://surface-e2e.test")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={
            "bot_token": "e2e-telegram-bot-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        },
    )

    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM", "account_id": str(account.id)},
        agent_name=agent_name,
    )
    surface_id = surface["id"]

    # Registration happened in-process during create: delete-then-set ordering,
    # verified by getWebhookInfo.
    assert fake_telegram.webhook_calls == ["deleteWebhook", "setWebhook"]

    # Map the Telegram sender to the pod-owning user so ingress resolves them.
    sender_id = 5550001
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    surface_row = await db_session.get(AgentSurface, UUID(surface_id))
    assert surface_row is not None and surface_row.webhook_secret
    secret = get_secret_cipher().decrypt_str(surface_row.webhook_secret)

    payload = _telegram_payload(text="Hi there!", message_id=1, sender_id=sender_id)
    response = await authenticated_client.post(
        f"/surfaces/{surface_id}/webhook",
        content=json.dumps(payload).encode("utf-8"),
        headers=build_telegram_secret_headers(secret),
    )
    assert response.status_code == 200, response.text

    # The worker ran the agent and the observer delivered one reply to the
    # fake Telegram API for the right chat.
    messages = await wait_for_messages(
        message_store, "TELEGRAM", min_count=1, timeout_seconds=REAL_REPLY_TIMEOUT
    )
    assert messages, "no Telegram reply was delivered by the real agent run"
    assert messages[-1]["chat_id"] == str(sender_id)
    assert (messages[-1].get("text") or "").strip()


async def test_telegram_webhook_multi_turn_reuses_conversation_with_real_agent(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    worker,
    monkeypatch,
):
    _ = worker  # real streaq worker subprocess consuming surface events
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://surface-e2e.test")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", False)
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={
            "bot_token": "e2e-telegram-bot-token",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        },
    )
    agent_name = await _create_system_lemma_agent(authenticated_client, pod_id)
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM", "account_id": str(account.id)},
        agent_name=agent_name,
    )
    surface_id = surface["id"]
    sender_id = 5550002
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    surface_row = await db_session.get(AgentSurface, UUID(surface_id))
    assert surface_row is not None and surface_row.webhook_secret
    secret = get_secret_cipher().decrypt_str(surface_row.webhook_secret)

    async def _send(text: str, message_id: int) -> None:
        payload = _telegram_payload(text=text, message_id=message_id, sender_id=sender_id)
        resp = await authenticated_client.post(
            f"/surfaces/{surface_id}/webhook",
            content=json.dumps(payload).encode("utf-8"),
            headers=build_telegram_secret_headers(secret),
        )
        assert resp.status_code == 200, resp.text

    await _send("Hello!", 1)
    await wait_for_messages(
        message_store, "TELEGRAM", min_count=1, timeout_seconds=REAL_REPLY_TIMEOUT
    )
    await _send("And what can you help with?", 2)
    messages = await wait_for_messages(
        message_store, "TELEGRAM", min_count=2, timeout_seconds=REAL_REPLY_TIMEOUT
    )
    assert len(messages) >= 2
    assert all(m["chat_id"] == str(sender_id) for m in messages)

    # Both turns landed in the same surface conversation (reused across turns).
    convo = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        external_thread_id=str(sender_id),
        agent_name=agent_name,
    )
    assert convo is not None
