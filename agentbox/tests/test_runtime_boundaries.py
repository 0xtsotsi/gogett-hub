from __future__ import annotations

import http.client
import io
import json
import logging
import sys
import time
import types
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import uuid4
from urllib import error, request

import pytest
from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.requests import Request as StarletteRequest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "kubernetes" not in sys.modules:
    kubernetes_module = types.ModuleType("kubernetes")
    kubernetes_client_module = types.ModuleType("kubernetes.client")
    kubernetes_config_module = types.ModuleType("kubernetes.config")
    kubernetes_stream_module = types.ModuleType("kubernetes.stream")
    kubernetes_client_rest_module = types.ModuleType("kubernetes.client.rest")

    class _ApiException(Exception):
        def __init__(self, status=None, reason=None, body=None):
            super().__init__(reason)
            self.status = status
            self.reason = reason
            self.body = body

    class _ConfigException(Exception):
        pass

    kubernetes_client_rest_module.ApiException = _ApiException
    kubernetes_config_module.ConfigException = _ConfigException
    kubernetes_module.client = kubernetes_client_module
    kubernetes_module.config = kubernetes_config_module
    kubernetes_module.stream = kubernetes_stream_module

    sys.modules["kubernetes"] = kubernetes_module
    sys.modules["kubernetes.client"] = kubernetes_client_module
    sys.modules["kubernetes.config"] = kubernetes_config_module
    sys.modules["kubernetes.stream"] = kubernetes_stream_module
    sys.modules["kubernetes.client.rest"] = kubernetes_client_rest_module

from agentbox import kubernetes, runtime_server  # noqa: E402
from agentbox.api import apps  # noqa: E402
from agentbox.schemas import (  # noqa: E402
    ExecCommandRequest,
    SandboxInternalAppStatus,
    SandboxInternalStatus,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _FakeProxyProvider:
    async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
        return SandboxInternalStatus(
            id=sandbox_id,
            status="RUNNING",
            ready=True,
            pod_ip="127.0.0.1",
            apps={
                "function_executor": SandboxInternalAppStatus(
                    name="function_executor",
                    public_slug="function",
                    port=8090,
                    ready=True,
                    private_url="http://function-executor",
                )
            },
        )


class _FakeUrlResponse:
    def __init__(
        self,
        status: int = 200,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


def _starlette_request(
    *,
    method: str = "POST",
    body: bytes = b'{"input": {"value": 1}}',
    query_string: bytes = b"",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> StarletteRequest:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return StarletteRequest(
        {
            "type": "http",
            "method": method,
            "path": "/pods/pod/functions/fn/execute",
            "app": SimpleNamespace(state=SimpleNamespace()),
            "headers": [
                (b"content-type", b"application/json"),
                (b"authorization", b"Bearer lemma-token"),
                *(headers or []),
            ],
            "query_string": query_string,
        },
        receive,
    )


def test_runtime_http_error_is_logged_and_returned_bounded(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=kubernetes.__name__)
    oversized_body = {"detail": "x" * 4000}

    def fake_urlopen(req, timeout):
        del req, timeout
        raise error.HTTPError(
            url="http://runtime/sessions/s1/exec-command",
            code=500,
            msg="Internal Server Error",
            hdrs={},
            fp=io.BytesIO(json.dumps(oversized_body).encode("utf-8")),
        )

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    req = request.Request("http://runtime/sessions/s1/exec-command", method="POST")

    with pytest.raises(HTTPException) as exc_info:
        kubernetes._request_runtime_json(
            req,
            timeout=1,
            operation="process command request",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["runtime_status"] == 500
    assert exc_info.value.detail["runtime_body"]["truncated"] is True
    assert len(exc_info.value.detail["runtime_body"]["preview"]) <= (
        kubernetes._MAX_RUNTIME_ERROR_BODY_LENGTH + len("... [truncated]")
    )
    assert "returned HTTP 500" in caplog.text


def test_sandbox_app_url_base_uses_agentbox_api_url(monkeypatch):
    monkeypatch.setattr(apps.settings, "agentbox_api_url", "https://agentbox.test/")
    monkeypatch.setattr(apps.settings, "agentbox_app_domain", "apps.agentbox.test")

    url = apps.sandbox_app_public_host(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
    )

    assert url == "sandbox-1-browser.apps.agentbox.test"


def test_sandbox_app_url_uses_resolvable_loopback_domain_by_default(monkeypatch):
    monkeypatch.setattr(apps.settings, "agentbox_api_url", "http://127.0.0.1:8721")
    monkeypatch.setattr(apps.settings, "agentbox_app_domain", None)

    host = apps.sandbox_app_public_host(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
    )
    url = apps.sandbox_app_public_url(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        "token-value",
    )

    assert host == "sandbox-1-browser.127-0-0-1.sslip.io:8721"
    assert url == "http://sandbox-1-browser.127-0-0-1.sslip.io:8721/?token=token-value"
    assert apps.sandbox_app_from_host(host)[1] == "sandbox-1"


def test_app_access_token_is_bound_to_app_and_sandbox(monkeypatch):
    monkeypatch.setattr(apps.settings, "agentbox_api_key", "secret")
    expires_at = int(time.time()) + 60
    token = apps.create_app_access_token("sandbox-1", "browser", expires_at)

    assert apps.validate_app_access_token("sandbox-1", "browser", token)
    assert not apps.validate_app_access_token("sandbox-2", "browser", token)
    assert not apps.validate_app_access_token("sandbox-1", "function_executor", token)


def test_app_access_cookie_uses_token_ttl(monkeypatch):
    monkeypatch.setattr(apps.settings, "agentbox_api_url", "https://agentbox.test/")
    monkeypatch.setattr(apps.settings, "agentbox_api_key", "secret")
    expires_at = int(time.time()) + 123
    token = apps.create_app_access_token("sandbox-1", "browser", expires_at)
    response = Response()

    apps.set_app_access_cookie(
        response,
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        token,
    )

    cookie = response.headers["set-cookie"]
    assert "agentbox_app_access_browser_sandbox-1=" in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "Max-Age=123" in cookie or "Max-Age=122" in cookie


def test_sandbox_app_upstream_websocket_url_strips_access_token():
    status_obj = SandboxInternalStatus(
        id="sandbox-1",
        status="RUNNING",
        ready=True,
        apps={
            "browser": SandboxInternalAppStatus(
                name="browser",
                public_slug="browser",
                port=4848,
                ready=True,
                private_url="http://browser-upstream",
            )
        },
    )

    url = apps.sandbox_app_upstream_websocket_url(
        status_obj,
        apps.resolve_sandbox_app("browser"),
        "api/session/123/stream",
        "token=private-token&x=1",
    )

    assert url == "ws://browser-upstream/api/session/123/stream?x=1"


@pytest.mark.anyio
async def test_sandbox_app_proxy_waits_for_app_health_before_forwarding(monkeypatch):
    request_obj = _starlette_request(query_string=b"x=1&token=discard")
    request_obj.app.state.sandbox_app_ready_cache = set()
    monkeypatch.setattr(apps.settings, "agentbox_sandbox_app_ready_timeout_seconds", 1)

    async def fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(apps.asyncio, "sleep", fast_sleep)
    calls: list[str] = []
    health_attempts = 0

    def fake_urlopen(req, timeout):
        del timeout
        nonlocal health_attempts
        if req.full_url == "http://function-executor/health":
            calls.append("health")
            health_attempts += 1
            if health_attempts == 1:
                raise error.URLError("executor not ready")
            return _FakeUrlResponse(body=b'{"status":"ok"}')

        calls.append("forward")
        assert req.full_url == (
            "http://function-executor/pods/pod/functions/fn/execute?x=1"
        )
        assert req.get_method() == "POST"
        assert req.data == b'{"input": {"value": 1}}'
        return _FakeUrlResponse(
            body=b'{"output": {"ok": true}}',
            headers={"Content-Type": "application/json"},
        )

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("function_executor"),
        "sandbox-1",
        "pods/pod/functions/fn/execute",
        request_obj,
        _FakeProxyProvider(),
        forward_authorization=True,
    )

    assert response.status_code == 200
    assert response.body == b'{"output": {"ok": true}}'
    assert calls == ["health", "health", "forward"]


@pytest.mark.anyio
async def test_sandbox_app_proxy_rewrites_origin_and_referer_for_upstream(
    monkeypatch,
):
    public_origin = "http://sandbox-1-browser.127-0-0-1.sslip.io:8721"
    request_obj = _starlette_request(
        method="GET",
        body=b"",
        query_string=b"token=discard&x=1",
        headers=[
            (b"origin", public_origin.encode("utf-8")),
            (b"referer", f"{public_origin}/?token=discard".encode("utf-8")),
        ],
    )
    request_obj.app.state.sandbox_app_ready_cache = set()

    class BrowserProvider:
        async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
            return SandboxInternalStatus(
                id=sandbox_id,
                status="RUNNING",
                ready=True,
                apps={
                    "browser": SandboxInternalAppStatus(
                        name="browser",
                        public_slug="browser",
                        port=4848,
                        ready=True,
                        private_url="http://browser-upstream",
                    )
                },
            )

    def fake_urlopen(req, timeout):
        del timeout
        if req.full_url == "http://browser-upstream/health":
            return _FakeUrlResponse(body=b'{"status":"ok"}')

        assert req.full_url == "http://browser-upstream/api/session/41497/tabs?x=1"
        assert req.headers["Origin"] == "http://browser-upstream"
        assert req.headers["Referer"] == (
            "http://browser-upstream/api/session/41497/tabs?x=1"
        )
        assert "token=discard" not in req.full_url
        return _FakeUrlResponse(body=b'{"tabs":[]}')

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        "api/session/41497/tabs",
        request_obj,
        BrowserProvider(),
    )

    assert response.status_code == 200
    assert response.body == b'{"tabs":[]}'


@pytest.mark.anyio
async def test_browser_dashboard_proxy_rewrites_local_dashboard_origins(
    monkeypatch,
):
    public_origin = "http://sandbox-1-browser.127-0-0-1.sslip.io:8721"
    monkeypatch.setattr(apps.settings, "agentbox_api_key", "secret")
    access_token = apps.create_app_access_token(
        "sandbox-1",
        "browser",
        int(time.time()) + 60,
    )
    request_obj = _starlette_request(
        method="GET",
        body=b"",
        headers=[
            (b"host", b"sandbox-1-browser.127-0-0-1.sslip.io:8721"),
        ],
    )
    request_obj.app.state.sandbox_app_ready_cache = set()

    class BrowserProvider:
        async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
            return SandboxInternalStatus(
                id=sandbox_id,
                status="RUNNING",
                ready=True,
                apps={
                    "browser": SandboxInternalAppStatus(
                        name="browser",
                        public_slug="browser",
                        port=4848,
                        ready=True,
                        private_url="http://browser-upstream",
                    )
                },
            )

    dashboard_chunk = (
        b'fetch(`${td()}/api/chat/status`);'
        b'function td(){return "http://localhost:4848"};'
        b'const ws = "ws://localhost:4848/api/stream";'
        b'function e$(e){let t=`/api/session/${e}/stream`;return t}'
    )

    def fake_urlopen(req, timeout):
        del timeout
        if req.full_url == "http://browser-upstream/health":
            return _FakeUrlResponse(body=b'{"status":"ok"}')

        assert req.full_url == "http://browser-upstream/_next/static/chunks/dashboard.js"
        return _FakeUrlResponse(
            body=dashboard_chunk,
            headers={
                "Content-Type": "application/javascript; charset=utf-8",
                "ETag": "stale-etag",
            },
        )

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        "_next/static/chunks/dashboard.js",
        request_obj,
        BrowserProvider(),
        access_token=access_token,
    )

    assert response.status_code == 200
    assert b"http://localhost:4848" not in response.body
    assert b"ws://localhost:4848" not in response.body
    assert public_origin.encode("utf-8") in response.body
    assert b"ws://sandbox-1-browser.127-0-0-1.sslip.io:8721/api/stream" in response.body
    assert f"/api/session/${{e}}/stream?token={access_token}".encode("utf-8") in response.body
    assert "etag" not in {key.lower() for key in response.headers}


@pytest.mark.anyio
async def test_browser_dashboard_proxy_injects_focused_layout_style(monkeypatch):
    request_obj = _starlette_request(
        method="GET",
        body=b"",
        headers=[
            (b"host", b"sandbox-1-browser.127-0-0-1.sslip.io:8721"),
        ],
    )
    request_obj.app.state.sandbox_app_ready_cache = set()

    class BrowserProvider:
        async def get_status(self, sandbox_id: str) -> SandboxInternalStatus:
            return SandboxInternalStatus(
                id=sandbox_id,
                status="RUNNING",
                ready=True,
                apps={
                    "browser": SandboxInternalAppStatus(
                        name="browser",
                        public_slug="browser",
                        port=4848,
                        ready=True,
                        private_url="http://browser-upstream",
                    )
                },
            )

    def fake_urlopen(req, timeout):
        del timeout
        if req.full_url == "http://browser-upstream/health":
            return _FakeUrlResponse(body=b'{"status":"ok"}')

        assert req.full_url == "http://browser-upstream/"
        return _FakeUrlResponse(
            body=b"<!doctype html><html><head><title>agent-browser</title></head><body></body></html>",
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "ETag": "stale-etag",
            },
        )

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    response = await apps.proxy_sandbox_app_http_request(
        apps.resolve_sandbox_app("browser"),
        "sandbox-1",
        "",
        request_obj,
        BrowserProvider(),
    )

    assert response.status_code == 200
    assert b"agentbox-browser-dashboard-focus-style" in response.body
    assert b"#activity" in response.body
    assert response.body.index(b"agentbox-browser-dashboard-focus-style") < response.body.index(
        b"</head>"
    )
    assert "etag" not in {key.lower() for key in response.headers}


@pytest.mark.anyio
async def test_sandbox_app_proxy_maps_closed_upstream_connection_to_502(monkeypatch):
    request_obj = _starlette_request()
    request_obj.app.state.sandbox_app_ready_cache = {
        ("sandbox-1", "function_executor", "http://function-executor")
    }

    def fake_urlopen(req, timeout):
        del req, timeout
        raise http.client.RemoteDisconnected("closed during startup")

    monkeypatch.setattr(apps.urlrequest, "urlopen", fake_urlopen)

    with pytest.raises(HTTPException) as exc_info:
        await apps.proxy_sandbox_app_http_request(
            apps.resolve_sandbox_app("function_executor"),
            "sandbox-1",
            "pods/pod/functions/fn/execute",
            request_obj,
            _FakeProxyProvider(),
            forward_authorization=True,
        )

    assert exc_info.value.status_code == 502
    assert "Sandbox app proxy failed" in exc_info.value.detail


def test_runtime_shell_command_persists_cwd_and_hides_marker(tmp_path):
    session_id = f"test-{uuid4().hex}"
    nested = tmp_path / "nested"
    nested.mkdir()

    first = runtime_server.execute_shell_command(
        session_id,
        f"cd {nested} && printf hello",
        timeout_seconds=5,
        cwd=str(tmp_path),
    )
    second = runtime_server.execute_shell_command(
        session_id,
        "pwd",
        timeout_seconds=5,
    )

    assert first["ok"] is True
    assert first["stdout"] == "hello"
    assert "__AGENTBOX_CWD_" not in first["stdout"]
    assert runtime_server.get_or_create_session(session_id).cwd == str(nested)
    assert second["stdout"].strip() == str(nested)


def test_exec_command_request_rejects_shell_and_login_fields():
    with pytest.raises(ValidationError):
        ExecCommandRequest.model_validate({"cmd": "printf hi", "shell": "/bin/sh"})

    with pytest.raises(ValidationError):
        ExecCommandRequest.model_validate({"cmd": "printf hi", "login": True})


def test_runtime_python_session_behaves_like_notebook(tmp_path):
    session_id = f"test-{uuid4().hex}"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))

    first = runtime_server.execute_python(session_id, "x = 41")
    second = runtime_server.execute_python(session_id, "x + 1")

    assert first["ok"] is True
    assert second["ok"] is True
    assert second["result"] == "42"


def test_runtime_python_resolves_typing_annotations_for_schema_extraction(tmp_path):
    # Under Python 3.14 (PEP 649) annotations are evaluated lazily; pydantic
    # resolves a model's deferred annotations via sys.modules[__module__].
    # The session namespace must be a real registered module so imported names
    # like typing.Optional resolve at schema-build time, not just builtins.
    session_id = f"test-{uuid4().hex}"
    runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))

    source = (
        "from typing import Optional\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class Result(BaseModel):\n"
        "    a: Optional[int] = None\n"
        "    b: int | None = None\n"
        "\n"
        "import json\n"
        "json.dumps(Result.model_json_schema())"
    )

    result = runtime_server.execute_python(session_id, source)

    assert result["ok"] is True, result["stderr"]
    schema = json.loads(result["result"].strip("'\""))
    assert set(schema["properties"]) == {"a", "b"}


def test_runtime_python_session_module_is_unregistered_on_delete(tmp_path):
    session_id = f"test-{uuid4().hex}"
    session = runtime_server.get_or_create_session(session_id, cwd=str(tmp_path))
    module_name = session.globals["__name__"]

    assert sys.modules.get(module_name) is not None

    assert runtime_server.delete_session(session_id) is True
    assert module_name not in sys.modules


def test_malformed_runtime_json_is_logged_and_returned_bounded(monkeypatch, caplog):
    caplog.set_level(logging.WARNING, logger=kubernetes.__name__)

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"not json"

    def fake_urlopen(req, timeout):
        del req, timeout
        return _Response()

    monkeypatch.setattr(request, "urlopen", fake_urlopen)
    req = request.Request("http://runtime/sessions/s1/exec-command", method="POST")

    with pytest.raises(HTTPException) as exc_info:
        kubernetes._request_runtime_json(
            req,
            timeout=1,
            operation="process command request",
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "runtime returned malformed JSON"
    assert exc_info.value.detail["runtime_body"] == "not json"
    assert "returned malformed JSON" in caplog.text


def test_runtime_handler_rejects_shell_and_login_exec_fields():
    server = ThreadingHTTPServer(("127.0.0.1", 0), runtime_server.RuntimeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for payload in [
            {"cmd": "printf hi", "shell": "/bin/sh"},
            {"cmd": "printf hi", "login": True},
        ]:
            connection = http.client.HTTPConnection(*server.server_address, timeout=5)
            connection.request(
                "POST",
                "/sessions/s1/exec-command",
                body=json.dumps(payload),
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            body = json.loads(response.read().decode("utf-8"))
            connection.close()

            assert response.status == 400
            assert "Unsupported field" in body["detail"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_runtime_handler_logs_and_returns_json_500_for_unhandled_exception(
    monkeypatch,
    caplog,
):
    caplog.set_level(logging.ERROR, logger=runtime_server.__name__)

    def failing_get_or_create_session(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("session exploded " + ("x" * 4000))

    monkeypatch.setattr(
        runtime_server,
        "get_or_create_session",
        failing_get_or_create_session,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), runtime_server.RuntimeHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        connection.request(
            "POST",
            "/sessions/s1",
            body=json.dumps({"cwd": "/workspace"}),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        body = json.loads(response.read().decode("utf-8"))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert response.status == 500
    assert body["detail"]["message"] == "Unhandled runtime server error"
    assert "session exploded" in body["detail"]["error"]
    assert len(body["detail"]["error"]) <= (
        runtime_server.MAX_RUNTIME_RESPONSE_ERROR_LENGTH + len("... [truncated]")
    )
    assert "Unhandled AgentBox runtime request error" in caplog.text
