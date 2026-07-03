"""Per-app account identity derivation (provider_account_id + email + display_name)."""

from __future__ import annotations

import pytest

import app.modules.connectors.services.account_identity as account_identity
from app.modules.connectors.services.account_identity import resolve_account_identity

pytestmark = pytest.mark.asyncio


async def test_gmail_identity_from_profile_email():
    identity = await resolve_account_identity(
        connector_id="gmail",
        credentials={},
        profile={"email_address": "rahul@gmail.com"},
    )
    assert identity.provider_account_id == "rahul@gmail.com"
    assert identity.email == "rahul@gmail.com"
    assert identity.display_name == "rahul@gmail.com"


async def test_slack_identity_uses_team_name_and_user_id():
    identity = await resolve_account_identity(
        connector_id="slack",
        credentials={
            "raw_response": {
                "team": {"id": "T123", "name": "Acme"},
                "authed_user": {"id": "U777"},
                "bot_user_id": "B999",
            }
        },
    )
    assert identity.provider_account_id == "U777"
    assert identity.display_name == "Acme"


async def test_whatsapp_identity_from_phone_number_id():
    identity = await resolve_account_identity(
        connector_id="whatsapp",
        credentials={"phone_number_id": "PN42", "display_phone_number": "+1 555 0100"},
    )
    assert identity.provider_account_id == "PN42"
    assert identity.display_name == "+1 555 0100"


async def test_resend_identity_from_address():
    identity = await resolve_account_identity(
        connector_id="resend",
        credentials={"from_address": "ops@pod.lemma.work"},
    )
    assert identity.provider_account_id == "ops@pod.lemma.work"
    assert identity.email == "ops@pod.lemma.work"
    assert identity.display_name == "ops@pod.lemma.work"


async def test_generic_identity_falls_back_to_raw_user_id():
    identity = await resolve_account_identity(
        connector_id="airtable",
        credentials={"raw_response": {"user": {"id": "acc-1"}}},
    )
    assert identity.provider_account_id == "acc-1"


async def test_telegram_identity_calls_getme(monkeypatch):
    class _FakeResp:
        def raise_for_status(self):  # noqa: D401
            return None

        def json(self):
            return {"ok": True, "result": {"id": 123, "username": "lemmabot", "first_name": "Lemma"}}

    class _FakeClient:
        def __init__(self, *a, **k):
            self.posted_url = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url):
            _FakeClient.posted_url = url
            return _FakeResp()

    monkeypatch.setattr(account_identity.httpx, "AsyncClient", _FakeClient)

    identity = await resolve_account_identity(
        connector_id="telegram",
        credentials={"bot_token": "111:AAA", "api_base_url": "http://fake/bot"},
    )
    assert identity.provider_account_id == "123"
    assert identity.display_name == "@lemmabot"
    assert _FakeClient.posted_url == "http://fake/bot111:AAA/getMe"


async def test_telegram_missing_token_returns_empty():
    identity = await resolve_account_identity(connector_id="telegram", credentials={})
    assert identity.provider_account_id is None
    assert identity.display_name is None


async def test_telegram_getme_failure_is_swallowed(monkeypatch):
    class _BoomClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url):
            raise RuntimeError("network down")

    monkeypatch.setattr(account_identity.httpx, "AsyncClient", _BoomClient)
    identity = await resolve_account_identity(
        connector_id="telegram", credentials={"bot_token": "111:AAA"}
    )
    assert identity == account_identity.AccountIdentity()
