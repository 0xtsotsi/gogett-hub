"""E2E tests for the datastore changes websocket.

Drives ``/pods/{pod_id}/datastore/changes`` over a raw ASGI websocket
(``ApplicationCommunicator``) and asserts the live record-change stream, table
filtering, RLS per-user row scoping, and auth rejection.
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from asgiref.testing import ApplicationCommunicator

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


def _ws_communicator(
    app,
    pod_id: str,
    token: str,
    *,
    table: str | None = None,
) -> ApplicationCommunicator:
    query = f"table={table}".encode() if table else b""
    path = f"/pods/{pod_id}/datastore/changes"
    headers = [(b"host", b"testserver")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    return ApplicationCommunicator(
        app,
        {
            "type": "websocket",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": headers,
            "scheme": "ws",
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
        },
    )


async def _expect_accept_and_ready(communicator: ApplicationCommunicator) -> dict:
    accepted = await communicator.receive_output(timeout=5)
    assert accepted["type"] == "websocket.accept", accepted
    ready = await _recv_json(communicator, timeout=5)
    assert ready["type"] == "ready", ready
    assert "since" in ready
    return ready


async def _recv_json(communicator: ApplicationCommunicator, timeout: float = 10) -> dict:
    message = await communicator.receive_output(timeout=timeout)
    assert message["type"] == "websocket.send", message
    return json.loads(message["text"])


async def _assert_no_frame(communicator: ApplicationCommunicator, timeout: float = 3) -> None:
    # receive_nothing polls the output queue without cancelling the app task,
    # unlike receive_output which kills the websocket on timeout.
    assert await communicator.receive_nothing(timeout=timeout, interval=0.05)


@pytest_asyncio.fixture
async def notes_pod(pod_api: DatastoreApi) -> DatastoreApi:
    await pod_api.create_table(
        {
            "name": "notes",
            "enable_rls": False,
            "columns": [{"name": "body", "type": "TEXT", "required": True}],
        }
    )
    return pod_api


async def test_changes_ws_streams_record_lifecycle(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"]
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    created = await notes_pod.create_record("notes", {"body": "hello"})
    insert = await _recv_json(communicator)
    assert insert["type"] == "datastore.record.insert"
    assert insert["table_name"] == "notes"
    assert insert["operation"] == "insert"
    assert insert["payload"]["body"] == "hello"
    assert insert["stream_id"]
    record_id = created["id"]

    await notes_pod.update_record("notes", record_id, {"body": "hello world"})
    update = await _recv_json(communicator)
    assert update["type"] == "datastore.record.update"
    assert update["payload"]["body"] == "hello world"

    await notes_pod.delete_record("notes", record_id)
    delete = await _recv_json(communicator)
    assert delete["type"] == "datastore.record.delete"
    assert delete["record_id"] == record_id

    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})


async def test_changes_ws_table_filter_excludes_other_tables(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    await notes_pod.create_table(
        {
            "name": "tasks",
            "enable_rls": False,
            "columns": [{"name": "title", "type": "TEXT", "required": True}],
        }
    )
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"], table="notes"
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    # A change to a table outside the filter must not be delivered.
    await notes_pod.create_record("tasks", {"title": "ignored"})
    await _assert_no_frame(communicator)

    # A change to the filtered table is delivered.
    await notes_pod.create_record("notes", {"body": "kept"})
    frame = await _recv_json(communicator)
    assert frame["table_name"] == "notes"
    assert frame["payload"]["body"] == "kept"

    await communicator.send_input({"type": "websocket.disconnect", "code": 1000})


async def test_changes_ws_rls_scopes_rows_to_owner(
    pod_api: DatastoreApi,
    member_users,
    async_client,
    fixed_test_user,
    test_app,
):
    await pod_api.create_table(
        {
            "name": "private_notes",
            "enable_rls": True,
            "columns": [{"name": "body", "type": "TEXT", "required": True}],
        }
    )
    editor = member_users["editor"]
    editor_api = DatastoreApi(async_client, pod_api.pod_id, user=editor)

    owner_ws = _ws_communicator(
        test_app, pod_api.pod_id, fixed_test_user["token"], table="private_notes"
    )
    await owner_ws.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(owner_ws)

    # The editor writes a row they own; the pod owner's stream must not see it,
    # because RLS scopes each row to its owner.
    await editor_api.create_record("private_notes", {"body": "editor secret"})
    await _assert_no_frame(owner_ws)

    # The editor's own stream does receive their row.
    editor_ws = _ws_communicator(
        test_app, pod_api.pod_id, editor["token"], table="private_notes"
    )
    await editor_ws.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(editor_ws)
    await editor_api.create_record("private_notes", {"body": "editor only"})
    frame = await _recv_json(editor_ws)
    assert frame["payload"]["body"] == "editor only"

    # The owner's own row IS delivered on their stream.
    await pod_api.create_record("private_notes", {"body": "owner row"})
    owner_frame = await _recv_json(owner_ws)
    assert owner_frame["payload"]["body"] == "owner row"

    await owner_ws.send_input({"type": "websocket.disconnect", "code": 1000})
    await editor_ws.send_input({"type": "websocket.disconnect", "code": 1000})


async def test_changes_ws_rejects_unauthenticated(
    notes_pod: DatastoreApi,
    test_app,
):
    communicator = _ws_communicator(test_app, notes_pod.pod_id, token="")
    await communicator.send_input({"type": "websocket.connect"})
    closed = await communicator.receive_output(timeout=5)
    assert closed["type"] == "websocket.close"


async def test_changes_ws_disconnect_immediately_after_connect(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    """Client that disconnects immediately after the handshake must not crash the server.

    Regression guard for the uvicorn accept-race: in production, a client that
    closes the TCP connection between the HTTP upgrade and the server's
    websocket.accept() causes uvicorn to raise RuntimeError.  The ASGI layer
    should always complete cleanly regardless of when the client goes away.
    """
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"]
    )
    # Connect, then immediately signal disconnect before consuming any output.
    await communicator.send_input({"type": "websocket.connect"})
    await communicator.send_input({"type": "websocket.disconnect", "code": 1001})

    # Drain whatever the server sent (accept + ready frame) before it noticed
    # the disconnect.  We don't assert on the exact sequence — both "accepted
    # then closed" and "rejected with close" are valid outcomes.  What matters
    # is that the application task finishes without raising.
    for _ in range(5):
        try:
            msg = await communicator.receive_output(timeout=2)
            if msg["type"] == "websocket.close":
                break
        except Exception:
            break

    # The ASGI app task must have exited cleanly (no unhandled exception).
    await communicator.wait(timeout=3)


async def test_changes_ws_disconnect_during_streaming(
    notes_pod: DatastoreApi,
    fixed_test_user,
    test_app,
):
    """Client that disconnects mid-stream must not leave a runaway server task.

    Once the server has accepted and started streaming, a sudden client
    disconnect should be detected by _wait_for_disconnect and cause both the
    forwarder task and the disconnect-watcher to be cancelled and awaited.
    """
    communicator = _ws_communicator(
        test_app, notes_pod.pod_id, fixed_test_user["token"]
    )
    await communicator.send_input({"type": "websocket.connect"})
    await _expect_accept_and_ready(communicator)

    # Disconnect mid-stream without sending a change event.
    await communicator.send_input({"type": "websocket.disconnect", "code": 1001})

    # Server should finish within a short window.
    await communicator.wait(timeout=3)
