from __future__ import annotations

import asyncio
import json
from http import HTTPStatus
from urllib import parse

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]


def assert_public_websocket_accepts(url: str) -> None:
    websockets = pytest.importorskip("websockets")

    async def connect_once() -> None:
        async with websockets.connect(url, open_timeout=10, close_timeout=1):
            return

    asyncio.run(connect_once())


def test_browser_access_uses_host_routing_signed_url_and_cookie(
    agentbox_server,
    sandbox_id,
):
    manager = agentbox_server.client
    created = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text

    session_id = "browser-dashboard-e2e"
    created_session = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}",
        body={},
        timeout=120,
    )
    assert created_session.status_code == HTTPStatus.OK, created_session.text

    opened_browser = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/sessions/{session_id}/exec-command",
        body={
            "cmd": "agent-browser open https://example.com",
            "tty": True,
            "yield_time_ms": 1500,
            "timeout": 45,
        },
        timeout=120,
    )
    assert opened_browser.status_code == HTTPStatus.OK, opened_browser.text
    assert opened_browser.json()["success"] is True

    access = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/browser/access",
        body={"ttl_seconds": 1800},
    )
    assert access.status_code == HTTPStatus.OK, access.text
    payload = access.json()
    assert payload["sandbox_id"] == sandbox_id
    assert payload["app"] == "browser"
    assert f"{sandbox_id}-browser.{agentbox_server.app_domain}" in payload["url"]
    assert "token=" in payload["url"]

    parsed = parse.urlsplit(payload["url"])
    no_token_url = parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    no_token = agentbox_server.public_get(no_token_url)
    assert no_token.status_code == HTTPStatus.FORBIDDEN

    first = agentbox_server.public_get(payload["url"])
    assert first.status_code == HTTPStatus.OK, first.text
    cookie = first.headers.get("set-cookie", "")
    assert f"agentbox_app_access_browser_{sandbox_id}" in cookie
    assert "HttpOnly" in cookie

    second = agentbox_server.public_get(no_token_url, cookie=cookie)
    assert second.status_code != HTTPStatus.FORBIDDEN
    assert second.status_code < 500

    sessions_url = parse.urlunsplit(
        (parsed.scheme, parsed.netloc, "/api/sessions", parsed.query, "")
    )
    sessions = agentbox_server.public_get(sessions_url, cookie=cookie)
    assert sessions.status_code == HTTPStatus.OK, sessions.text
    public_sessions = json.loads(sessions.text)
    assert public_sessions
    browser_port = public_sessions[0]["port"]
    assert isinstance(browser_port, int)

    stream_url = parse.urlunsplit(
        (
            "ws" if parsed.scheme == "http" else "wss",
            parsed.netloc,
            f"/api/session/{browser_port}/stream",
            parsed.query,
            "",
        )
    )
    assert_public_websocket_accepts(stream_url)


def test_only_workspace_user_apps_get_public_access(agentbox_server, sandbox_id):
    created = agentbox_server.client.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text

    for app_name in ["runtime", "function_executor"]:
        response = agentbox_server.client.request_json(
            "POST",
            f"/sandboxes/{sandbox_id}/apps/{app_name}/access",
            body={"ttl_seconds": 1800},
        )
        assert response.status_code == HTTPStatus.NOT_FOUND
