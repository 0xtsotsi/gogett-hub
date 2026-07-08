"""E2E: the universal widget submit bridge endpoint.

A standalone widget (e.g. opened from a surface) POSTs its submission to the
signed submit endpoint, which injects it into the conversation as a new user
message and starts a run. The web-embedded path posts to its parent instead and
never hits this endpoint.
"""

from __future__ import annotations

from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import status
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.modules.agent.domain.value_objects import MessageRole
from app.modules.agent.infrastructure.models import ConversationModel, MessageModel
from app.modules.test_support.e2e import fixtures as e2e_fixtures

test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org

pytestmark = pytest.mark.e2e

WIDGET_CONTENT = '<form id="f"><button onclick="lemma.submit({choice:\'B\'})">Go</button></form>'


@pytest_asyncio.fixture
async def test_pod(authenticated_client, fixed_test_org):
    payload = {
        "name": f"Widget Submit Pod {uuid4()}",
        "slug": f"widget-submit-pod-{uuid4()}",
        "type": "ASSISTANT",
        "organization_id": fixed_test_org["id"],
    }
    response = await authenticated_client.post("/pods", json=payload, follow_redirects=True)
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _create_agent(authenticated_client, pod_id: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"Widget Agent {uuid4().hex[:8]}",
            "instruction": "Reply briefly.",
            "agent_runtime": {"profile_id": "system:lemma"},
            "toolsets": [],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _seed_interactive_widget(
    db_session, *, pod_id: UUID, user_id: UUID, agent_id: UUID
) -> tuple[UUID, str]:
    tool_call_id = f"tc_widget_{uuid4().hex[:8]}"
    conversation = ConversationModel(user_id=user_id, pod_id=pod_id, agent_id=agent_id)
    db_session.add(conversation)
    await db_session.flush()
    db_session.add(
        MessageModel(
            conversation_id=conversation.id,
            sequence=0,
            role="ASSISTANT",
            kind="TOOL_CALL",
            tool_call_id=tool_call_id,
            tool_name="display_resource",
            tool_args={
                "type": "WIDGET",
                "content": WIDGET_CONTENT,
                "name": "Choice Widget",
                "interactive": True,
            },
        )
    )
    await db_session.commit()
    return conversation.id, tool_call_id


def _serve_path(absolute_url: str) -> str:
    parsed = urlparse(absolute_url)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


async def _user_messages(db_session, conversation_id: UUID) -> list[MessageModel]:
    db_session.expire_all()
    rows = (
        await db_session.execute(
            select(MessageModel).where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == MessageRole.USER.value,
            )
        )
    ).scalars().all()
    return list(rows)


@pytest.mark.asyncio
async def test_widget_submit_injects_message_via_session(
    authenticated_client, test_app, test_pod, fixed_test_user, db_session
):
    """A session-authenticated submit injects the text as a new user message."""
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    agent = await _create_agent(authenticated_client, str(pod_id))
    conv_id, tool_call_id = await _seed_interactive_widget(
        db_session, pod_id=pod_id, user_id=user_id, agent_id=UUID(agent["id"])
    )

    submit = await authenticated_client.post(
        f"/widgets/serve/{conv_id}/{tool_call_id}/submit",
        json={"text": "I choose option B"},
    )
    assert submit.status_code == status.HTTP_200_OK, submit.text
    assert submit.json() == {"ok": True}

    users = await _user_messages(db_session, conv_id)
    assert any("I choose option B" in (m.text or "") for m in users), users
    matched = next(m for m in users if "I choose option B" in (m.text or ""))
    assert (matched.message_metadata or {}).get("source") == "widget_submit"


@pytest.mark.asyncio
async def test_widget_submit_with_payload_and_signed_token(
    authenticated_client, test_app, test_pod, fixed_test_user, db_session
):
    """A standalone widget submit authenticates with the per-view signed token
    (no session cookie) and renders the payload into the message."""
    pod_id = UUID(test_pod["id"])
    user_id = UUID(fixed_test_user["id"])
    agent = await _create_agent(authenticated_client, str(pod_id))
    conv_id, tool_call_id = await _seed_interactive_widget(
        db_session, pod_id=pod_id, user_id=user_id, agent_id=UUID(agent["id"])
    )

    # Mint the same per-view token the serve URL uses.
    mint = await authenticated_client.post(
        f"/pods/{pod_id}/widgets/{conv_id}/{tool_call_id}/embed-token"
    )
    assert mint.status_code == status.HTTP_200_OK, mint.text
    query = urlparse(mint.json()["url"]).query  # token=...

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://testserver"
    ) as anon:
        submit = await anon.post(
            f"/widgets/serve/{conv_id}/{tool_call_id}/submit?{query}",
            json={"payload": {"choice": "B", "rating": 5}},
        )
        assert submit.status_code == status.HTTP_200_OK, submit.text

        # A tampered token is rejected.
        bad = await anon.post(
            f"/widgets/serve/{conv_id}/{tool_call_id}/submit?token=not-a-token",
            json={"text": "nope"},
        )
        assert bad.status_code == status.HTTP_401_UNAUTHORIZED, bad.text

    users = await _user_messages(db_session, conv_id)
    assert any("choice" in (m.text or "") and "B" in (m.text or "") for m in users), users
