from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from agentbox import auth
from agentbox.config import settings


def _call(x_api_key: str | None = None) -> None:
    asyncio.run(auth.require_api_key(x_api_key=x_api_key))


def test_accepts_matching_x_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "secret-key")
    _call(x_api_key="secret-key")  # does not raise


def test_rejects_when_only_authorization_header_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Manager auth is X-API-Key only. The Authorization header is reserved for
    # the function/lemma token and must NOT authenticate the manager key, even
    # if it carries the correct value.
    monkeypatch.setattr(settings, "agentbox_api_key", "secret-key")
    with pytest.raises(HTTPException) as exc:
        _call(x_api_key=None)
    assert exc.value.status_code == 401


def test_tolerates_trailing_newline_on_provided_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The classic `echo` vs `echo -n` base64 Secret trap: a stray newline must
    # not cause a silent 401.
    monkeypatch.setattr(settings, "agentbox_api_key", "secret-key")
    _call(x_api_key="secret-key\n")  # does not raise


def test_tolerates_whitespace_on_expected_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "  secret-key\n")
    _call(x_api_key="secret-key")  # does not raise


def test_rejects_wrong_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "secret-key")
    with pytest.raises(HTTPException) as exc:
        _call(x_api_key="wrong-key")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid AgentBox API key"


def test_rejects_missing_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "agentbox_api_key", "secret-key")
    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == 401


def test_rejects_when_expected_key_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    # A misconfigured/blank server key must never accept an (also blank) token.
    monkeypatch.setattr(settings, "agentbox_api_key", "   ")
    with pytest.raises(HTTPException) as exc:
        _call(x_api_key="   ")
    assert exc.value.status_code == 401
