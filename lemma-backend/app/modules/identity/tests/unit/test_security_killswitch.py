"""When delegated tokens are disabled, verify_auth must reject impersonation /
delegation tokens rather than silently honoring them as full user sessions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core import security
from app.core.authorization.delegation import CLAIM_ACTOR_ID


class _FakeSession:
    def __init__(self, user_id: str, payload: dict):
        self._user_id = user_id
        self._payload = payload

    def get_user_id(self) -> str:
        return self._user_id

    def get_access_token_payload(self) -> dict:
        return self._payload


def _connection() -> SimpleNamespace:
    return SimpleNamespace(
        url=SimpleNamespace(path="/pods/does-not-matter"),
        scope={"type": "http"},
        state=SimpleNamespace(),
    )


def _patch(monkeypatch, *, flag: bool, payload: dict):
    monkeypatch.setattr(
        security.settings, "authz_delegated_tokens_enabled", flag, raising=False
    )
    monkeypatch.setattr(
        security,
        "get_session",
        AsyncMock(return_value=_FakeSession(str(uuid4()), payload)),
    )


@pytest.mark.asyncio
async def test_impersonation_token_rejected_when_delegation_disabled(monkeypatch):
    _patch(monkeypatch, flag=False, payload={"isImpersonation": True})

    with pytest.raises(HTTPException) as exc:
        await security.verify_auth(_connection())

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "IMPERSONATION_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_delegation_claim_token_rejected_when_delegation_disabled(monkeypatch):
    _patch(monkeypatch, flag=False, payload={CLAIM_ACTOR_ID: str(uuid4())})

    with pytest.raises(HTTPException) as exc:
        await security.verify_auth(_connection())

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "IMPERSONATION_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_plain_user_token_accepted_when_delegation_disabled(monkeypatch):
    conn = _connection()
    _patch(monkeypatch, flag=False, payload={"client": "lemma-cli"})

    await security.verify_auth(conn)

    assert conn.state.user is not None
    assert conn.state.delegation_claims is None
