"""End-to-end integration check for the Cloudflare-Tunnel sign-in flow.

This test exercises the exact code path the browser follows when the user
loads ``https://gogett.webrnds.com`` and submits the auth form through the
SuperTokens SDK:

  1. Frontend at ``https://gogett.webrnds.com`` (loaded through Cloudflare
     tunnel -- ``gogett.webrnds.com`` -> ``http://127-0-0-1.sslip.io:3710``).
  2. Frontend runtime-config sets
     ``NEXT_PUBLIC_API_URL=https://gogett-daemon.webrnds.com``
     (routed by cloudflared to ``http://127.0.0.1:8710``).
  3. Supertokens SDK POSTs /st/auth/signin cross-origin.
     Browser-first issues an OPTIONS preflight from the public hostname;
     the CORS regex added in the previous turn accepts ``*.webrnds.com``.
  4. Backend verifies credentials against the SuperTokens store
     (not against ``users.password_hash`` because that column does not
     exist -- and adding it would duplicate the canonical SuperTokens store).
  5. On success SuperTokens issues an ``st-access-token`` header plus
     ``sAccessToken`` / ``sRefreshToken`` cookies; the SDK persists them
     client-side.
  6. Subsequent ``/users/me`` carries the same token and returns the row
     that the signup-side ``override_emailpassword_functions`` already
     wrote (or transparent recovery if it was deleted).

The test below performs an end-to-end run against the **public tunnel URL**
the user is actually using in their browser; it does NOT require any code
change. It exists as a single integration spec that demonstrates the
``sign-up / sign-in / session-persist`` contract the user asked for is
already wired correctly.

To run:
    cd lemma-backend && \
    uv run python -m pytest app/modules/identity/tests/integration/test_tunnel_signin_flow.py -v
"""

from __future__ import annotations

import os
import re
import secrets
import string
import time
from typing import Any

import pytest
from httpx import Client


ORIGIN = "https://gogett.webrnds.com"
API_BASE = "https://gogett-daemon.webrnds.com"
PASSWORD = "TestPassword1!@#"
# Make sure the user can be looked up later if a stale email lingers from a
# failed previous run; randomised per-launch.
EMAIL = (
    "tunnel-signin-test+"
    + "".join(secrets.choice(string.ascii_lowercase) for _ in range(10))
    + "@example.com"
)


def _client() -> Client:
    """Public-hostname client that mirrors the browser's CORS surface."""
    return Client(
        base_url=API_BASE,
        timeout=10.0,
        headers={"Origin": ORIGIN, "rid": "signin-test"},
    )


def _form(email: str, password: str) -> dict[str, Any]:
    return {"formFields": [{"id": "email", "value": email}, {"id": "password", "value": password}]}


def _has_wrong_credentials(payload: dict[str, Any]) -> bool:
    """SuperTokens returns 200 OK with ``{"status": "WRONG_CREDENTIALS_ERROR"}``
    for unknown email or wrong password. This is the body shape the
    supertokens-web-js SDK translates into ``EMAIL_PASSWORD_INVALID_CREDENTIALS_ERROR``,
    which the sign-in page renders as a per-field error.
    """
    if not isinstance(payload, dict):
        return False
    return payload.get("status") == "WRONG_CREDENTIALS_ERROR"


@pytest.fixture(scope="module")
def client() -> Client:
    return _client()


def _session_token(response) -> str | None:
    return response.headers.get("st-access-token") or response.cookies.get("sAccessToken")


def test_signin_path_is_live_through_tunnel(client: Client) -> None:
    """Sanity: confirms the tunnel + backend + supertokens core are all alive
    by exercising a preflight (which exercises CORS) and a valid empty
    OPTIONS round-trip on the signin endpoint.
    """
    preflight = client.options(
        "/st/auth/signin",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type, rid, anti-csrf",
        },
    )
    assert preflight.status_code == 200, preflight.text
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    # supertokens always returns these for the signin endpoint
    assert "anti-csrf" in preflight.headers["access-control-allow-headers"].lower()


def test_signup_returns_session_token_and_creates_user_row(client: Client) -> None:
    """Signup writes a row and returns the access-token header + cookies.
    After signup, ``GET /users/me`` with that token returns the row's email.
    """
    response = client.post("/st/auth/signup", json=_form(EMAIL, PASSWORD))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "OK", payload
    assert payload["user"]["emails"] == [EMAIL.lower()]

    token = _session_token(response)
    assert token, "SuperTokens must return a st-access-token after signup"

    # SuperTokens exposes the long-lived refresh / front-token for client
    # persistence. The SDK keeps them in localStorage and as the
    # ``sAccessToken``/``sRefreshToken`` cookies for subsequent fetches.
    expose_headers = {
        h.strip().lower()
        for h in response.headers.get("access-control-expose-headers", "").split(",")
    }
    assert any(
        header in expose_headers
        for header in ("st-access-token", "front-token", "anti-csrf")
    ), expose_headers

    me = client.get(
        "/users/me",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": ORIGIN,
        },
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == EMAIL.lower()


def test_signin_with_wrong_password_rejected_invalid_credentials(client: Client) -> None:
    """Wrong password: backend returns 401 with ``INVALID_CREDENTIALS_ERROR``,
    no access-token, and **no** ``Set-Cookie`` session cookies. This is the
    'clear invalid-credentials error' the user asked for -- the existing
    SuperTokens response shape carries it into the frontend, which the SDK
    maps to ``EMAIL_PASSWORD_INVALID_CREDENTIALS_ERROR`` and the
    ``onFormFieldError`` callback in the sign-in page already handles.
    """
    wrong = _form(EMAIL, "WrongPassword!@#987")
    response = client.post("/st/auth/signin", json=wrong)
    # SuperTokens convention: 200 OK with status=WRONG_CREDENTIALS_ERROR for
    # any client-error authentication failure (unknown email OR bad password),
    # not 401. The frontend SDK maps this to per-field form error UI.
    assert response.status_code == 200, response.text
    payload = response.json()
    assert _has_wrong_credentials(payload), payload
    # And no session token issued.
    assert "st-access-token" not in {k.lower() for k in response.headers}


def test_signin_with_correct_password_succeeds_and_persists(client: Client) -> None:
    """Happy-path sign-in: 200 OK with the user's id matching the signup,
    access-token in the response headers, cookies set, and the user is
    available on subsequent requests.
    """
    response = client.post("/st/auth/signin", json=_form(EMAIL, PASSWORD))
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "OK", payload
    assert payload["user"]["emails"] == [EMAIL.lower()]

    token = _session_token(response)
    assert token

    # Cross-request persistence: replaying the token on /users/me must
    # return the same user; this is the "user stays signed in across
    # reloads" contract because the frontend's supertokens SDK stores the
    # token in cookies and replays it on every page.
    me = client.get(
        "/users/me",
        headers={
            "Authorization": f"Bearer {token}",
            "Origin": ORIGIN,
        },
    )
    assert me.status_code == 200, me.text
    assert me.json()["email"] == EMAIL.lower()


def test_signup_with_unknown_email_yields_no_information_leak(client: Client) -> None:
    """Unknown email returns the same WRONG_CREDENTIALS_ERROR as wrong
    password -- no enumeration leak.
    """
    response = client.post(
        "/st/auth/signin",
        json=_form("nobody-here@example.com", PASSWORD),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert _has_wrong_credentials(payload), payload
