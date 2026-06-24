"""FunctionExecutorClient.wait_until_ready polls the in-sandbox app's readiness
endpoint (falling back to /health) before the caller posts an execute."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from agentbox_client.apps.function_executor import FunctionExecutorClient
from agentbox_client.errors import AgentBoxError

READINESS = "/sandboxes/sb/apps/function_executor/readiness"
HEALTH = "/sandboxes/sb/apps/function_executor/health"


def _client(handler) -> FunctionExecutorClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(base_url="http://agentbox", transport=transport)
    return FunctionExecutorClient(
        manager_base_url="http://agentbox",
        manager_api_key="k",
        lemma_token="t",
        client=http,
    )


def test_wait_until_ready_returns_on_first_200():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"status": "ready"})

    client = _client(handler)
    asyncio.run(
        client.wait_until_ready(sandbox_id="sb", timeout_seconds=1, poll_interval_seconds=0)
    )
    assert calls == [READINESS]


def test_wait_until_ready_retries_502_then_succeeds():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        # First readiness probe is a cold-start 502; the next is ready.
        if calls.count(READINESS) == 1:
            return httpx.Response(502, text="connection refused")
        return httpx.Response(200, json={"status": "ready"})

    client = _client(handler)
    asyncio.run(
        client.wait_until_ready(sandbox_id="sb", timeout_seconds=2, poll_interval_seconds=0)
    )
    assert calls.count(READINESS) == 2


def test_wait_until_ready_falls_back_to_health_when_readiness_404():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == READINESS:
            return httpx.Response(404, text="not found")
        return httpx.Response(200, json={"status": "ok"})

    client = _client(handler)
    asyncio.run(
        client.wait_until_ready(sandbox_id="sb", timeout_seconds=1, poll_interval_seconds=0)
    )
    assert calls == [READINESS, HEALTH]


def test_wait_until_ready_returns_when_both_endpoints_missing():
    # Old server build exposing neither endpoint: don't block; let the caller's
    # execute retry backstop handle cold start.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = _client(handler)
    asyncio.run(
        client.wait_until_ready(sandbox_id="sb", timeout_seconds=1, poll_interval_seconds=0)
    )


def test_wait_until_ready_raises_on_timeout():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="warming up")

    client = _client(handler)
    with pytest.raises(AgentBoxError):
        asyncio.run(
            client.wait_until_ready(
                sandbox_id="sb", timeout_seconds=0, poll_interval_seconds=0
            )
        )


def test_execute_drives_proxy_and_client_timeouts_from_request_budget():
    """A sync execute tells the proxy how long to wait (function timeout +
    headroom) and waits a little longer itself, so a long-but-legitimate
    function is not cut off at the proxy's short default."""
    from uuid import uuid4

    from agentbox_client.apps.function_executor import FunctionExecuteRequest

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["header"] = request.headers.get("X-Agentbox-Upstream-Timeout")
        captured["timeout"] = request.extensions.get("timeout")
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output_data": {},
                "logs": [],
                "code_hash": "abc",
                "duration_ms": 1,
            },
        )

    client = _client(handler)
    asyncio.run(
        client.execute(
            sandbox_id="sb",
            pod_id=uuid4(),
            function_name="hello",
            request=FunctionExecuteRequest(
                run_id=uuid4(),
                input_data={},
                async_job=False,
                timeout_seconds=200,
            ),
        )
    )
    # proxy upstream timeout = timeout_seconds + 30; client read timeout = +15 more.
    assert captured["header"] == "230"
    assert captured["timeout"]["read"] == 245.0
