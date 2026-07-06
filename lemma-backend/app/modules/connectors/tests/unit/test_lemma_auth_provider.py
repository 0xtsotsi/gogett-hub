from __future__ import annotations

from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from app.modules.connectors.domain.connector import ConnectorEntity, OAuth2Config
from app.modules.connectors.services.auth.lemma_auth_provider import LemmaAuthProvider

pytestmark = pytest.mark.asyncio


class FakeOAuth2Session:
    last_init: dict[str, object] = {}
    last_fetch_token: dict[str, object] = {}

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scope: list[str],
    ):
        FakeOAuth2Session.last_init = {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "scope": scope,
        }

    def fetch_token(self, **kwargs):
        FakeOAuth2Session.last_fetch_token = kwargs
        return {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "token_type": "Bearer",
        }


class FakeSlackOAuth2Session(FakeOAuth2Session):
    def fetch_token(self, **kwargs):
        FakeOAuth2Session.last_fetch_token = kwargs
        return {
            "access_token": "xoxp-user-token",
            "refresh_token": "refresh-token",
            "token_type": "bot",
            "authed_user": {
                "access_token": "xoxp-user-token",
                "token_type": "user",
            },
        }


def _connector(*, use_pkce: bool = False) -> ConnectorEntity:
    return ConnectorEntity(
        id="slack",
        oauth2_config=OAuth2Config(
            client_id="client-id",
            client_secret="client-secret",
            default_scopes=["chat:write"],
            authorization_url="https://slack.com/oauth/v2/authorize",
            token_url="https://slack.com/api/oauth.v2.access",
            use_pkce=use_pkce,
        ),
    )


async def test_exchange_code_uses_clean_redirect_uri_for_token_exchange():
    provider = LemmaAuthProvider(oauth_session_factory=FakeOAuth2Session)
    callback_url = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
        "?code=abc&state=xyz"
    )

    credentials = await provider.exchange_code_for_credentials(
        connector=_connector(),
        redirect_uri=callback_url,
        user_id=uuid4(),
    )

    expected_redirect_uri = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
    )
    assert FakeOAuth2Session.last_init["redirect_uri"] == expected_redirect_uri
    assert (
        FakeOAuth2Session.last_fetch_token["authorization_response"] == callback_url
    )
    assert "redirect_uri" not in FakeOAuth2Session.last_fetch_token
    assert credentials.access_token == "access-token"


async def test_exchange_code_normalizes_slack_token_type_to_bearer():
    provider = LemmaAuthProvider(oauth_session_factory=FakeSlackOAuth2Session)
    callback_url = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
        "?code=abc&state=xyz"
    )

    credentials = await provider.exchange_code_for_credentials(
        connector=_connector(),
        redirect_uri=callback_url,
        user_id=uuid4(),
    )

    assert credentials.access_token == "xoxp-user-token"
    assert credentials.token_type == "Bearer"


# --- PKCE (RFC 7636, S256) ---------------------------------------------------


async def test_authorization_url_adds_s256_challenge_and_returns_verifier():
    # Real authlib session so the actual S256 derivation is exercised.
    provider = LemmaAuthProvider()

    url, state, code_verifier = await provider.get_authorization_url(
        connector=_connector(use_pkce=True),
        user_id=uuid4(),
        state="csrf-state",
        redirect_uri="https://cb.example.com/callback",
    )

    assert state == "csrf-state"
    assert code_verifier is not None
    # RFC 7636 requires a 43–128 char verifier from the unreserved set.
    assert 43 <= len(code_verifier) <= 128

    params = parse_qs(urlsplit(url).query)
    assert params["code_challenge_method"] == ["S256"]
    # The challenge on the wire must be S256(verifier), never the raw verifier.
    assert params["code_challenge"] == [create_s256_code_challenge(code_verifier)]
    assert code_verifier not in params.get("code_challenge", [])


async def test_authorization_url_omits_challenge_when_pkce_disabled():
    provider = LemmaAuthProvider()

    url, _state, code_verifier = await provider.get_authorization_url(
        connector=_connector(use_pkce=False),
        user_id=uuid4(),
        state="csrf-state",
        redirect_uri="https://cb.example.com/callback",
    )

    assert code_verifier is None
    params = parse_qs(urlsplit(url).query)
    assert "code_challenge" not in params
    assert "code_challenge_method" not in params


async def test_exchange_code_replays_verifier_to_token_endpoint():
    provider = LemmaAuthProvider(oauth_session_factory=FakeOAuth2Session)
    callback_url = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
        "?code=abc&state=xyz"
    )

    await provider.exchange_code_for_credentials(
        connector=_connector(use_pkce=True),
        redirect_uri=callback_url,
        user_id=uuid4(),
        state="xyz",
        code_verifier="the-stored-verifier",
    )

    assert FakeOAuth2Session.last_fetch_token["code_verifier"] == "the-stored-verifier"
    # State is forwarded so authlib re-validates it against the callback URL.
    assert FakeOAuth2Session.last_fetch_token["state"] == "xyz"


async def test_exchange_code_omits_verifier_and_state_when_absent():
    provider = LemmaAuthProvider(oauth_session_factory=FakeOAuth2Session)
    callback_url = (
        "https://example.ngrok.app/connectors/connect-requests/oauth/callback"
        "?code=abc&state=xyz"
    )

    await provider.exchange_code_for_credentials(
        connector=_connector(use_pkce=False),
        redirect_uri=callback_url,
        user_id=uuid4(),
    )

    # Non-PKCE providers must see an unchanged token request.
    assert "code_verifier" not in FakeOAuth2Session.last_fetch_token
    assert "state" not in FakeOAuth2Session.last_fetch_token
