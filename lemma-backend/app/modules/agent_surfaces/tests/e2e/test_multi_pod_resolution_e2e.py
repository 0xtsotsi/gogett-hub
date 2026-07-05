"""E2E for deterministic surface selection when one person is reachable via a
shared system bot across pods in *different orgs* (WS3 / concern #1).

The shared Telegram system bot fans one inbound DM in to every active Telegram
surface. These tests prove the selection is deterministic and sticky:
- the first message routes to exactly one surface and creates one conversation;
- a follow-up in the same chat reuses that surface (continuity — no split-brain);
- a user-set default overrides the tiebreak.

Uses ``prepare_ingress`` directly (the routing decision) rather than a full agent
run — a DM's identity resolution makes no platform API call, so no fake bot is
needed."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_surface,
    _seed_external_user,
    _telegram_payload,
)
from app.modules.test_support.e2e.builders import E2EScenario

pytestmark = pytest.mark.e2e


async def _prepare_telegram_dm(db_session: AsyncSession, payload: dict):
    """Run just the ingress routing for a Telegram DM and persist its link."""
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload=payload)
    )
    await uow.commit()
    return context


async def _two_orgs_with_telegram_surfaces(
    authenticated_client: AsyncClient, async_client: AsyncClient, owner_user: dict
):
    org_a = E2EScenario(
        owner_client=authenticated_client,
        async_client=async_client,
        owner_user=owner_user,
    )
    await org_a.create_org_with_pod(name_prefix="MultiPodA")
    org_b = E2EScenario(
        owner_client=authenticated_client,
        async_client=async_client,
        owner_user=owner_user,
    )
    await org_b.create_org_with_pod(name_prefix="MultiPodB")

    surface_a = await _create_surface(
        authenticated_client, org_a.pod_id, config={"type": "TELEGRAM"}
    )
    surface_b = await _create_surface(
        authenticated_client, org_b.pod_id, config={"type": "TELEGRAM"}
    )
    return org_a, org_b, surface_a, surface_b


async def test_shared_bot_multi_org_routing_is_deterministic_and_sticky(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_user,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "shared-native-bot")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)

    org_a, org_b, surface_a, surface_b = await _two_orgs_with_telegram_surfaces(
        authenticated_client, async_client, fixed_test_user
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="900900900",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    # First DM → routes to exactly one of the two surfaces (deterministic).
    ctx1 = await _prepare_telegram_dm(
        db_session, _telegram_payload(text="hi", message_id=1, sender_id=900900900)
    )
    assert isinstance(ctx1, SurfaceChatContext)
    assert str(ctx1.surface_id) in {surface_a["id"], surface_b["id"]}

    # Second DM in the same chat → continuity keeps it on the SAME surface and
    # the SAME conversation (no bounce, no split-brain).
    ctx2 = await _prepare_telegram_dm(
        db_session, _telegram_payload(text="again", message_id=2, sender_id=900900900)
    )
    assert ctx2.surface_id == ctx1.surface_id
    assert ctx2.conversation_id == ctx1.conversation_id

    # Exactly one conversation exists — the other org's pod has none.
    chosen_pod = str(ctx1.pod_id)
    other_pod = org_b.pod_id if chosen_pod == org_a.pod_id else org_a.pod_id
    here = await _conversation_by_external_thread(
        authenticated_client, pod_id=chosen_pod, external_thread_id="900900900"
    )
    assert here is not None
    there = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=other_pod,
        external_thread_id="900900900",
        timeout_seconds=1.0,
    )
    assert there is None


async def test_shared_bot_routes_to_user_default_surface(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_user,
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "shared-native-bot")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)

    org_a, org_b, surface_a, surface_b = await _two_orgs_with_telegram_surfaces(
        authenticated_client, async_client, fixed_test_user
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="911911911",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    # The user picks org B's surface as their Telegram default.
    put = await authenticated_client.put(
        "/surfaces/me/default",
        json={"platform": "TELEGRAM", "surface_id": surface_b["id"]},
    )
    assert put.status_code == 200, put.text

    # First DM (no prior conversation) → the default wins over the tiebreak.
    ctx = await _prepare_telegram_dm(
        db_session, _telegram_payload(text="hi", message_id=10, sender_id=911911911)
    )
    assert isinstance(ctx, SurfaceChatContext)
    assert ctx.surface_id == UUID(surface_b["id"])
    assert str(ctx.pod_id) == org_b.pod_id
