from __future__ import annotations

import pytest

from agentbox_client import AgentBoxClient


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]


@pytest.mark.asyncio
async def test_python_client_uses_current_manager_contract(agentbox_server, sandbox_id):
    client = AgentBoxClient(
        base_url=agentbox_server.base_url,
        api_key=agentbox_server.api_key,
        timeout_seconds=180,
    )

    sandbox = await client.ensure_sandbox(
        sandbox_id, env={"CLIENT_CONTRACT_MARK": "client-env"}
    )
    assert sandbox.id == sandbox_id
    assert sandbox.ready is True
    assert sandbox.status == "RUNNING"

    fetched = await client.get_sandbox(sandbox_id)
    assert fetched == sandbox

    session = await client.create_session(
        sandbox_id,
        "client-session",
        env={"CLIENT_SESSION_MARK": "session-env"},
        cwd="/workspace/client-contract",
    )
    assert session.session_id == "client-session"
    assert session.cwd == "/workspace/client-contract"
    assert "CLIENT_SESSION_MARK" in session.env_keys

    code = await client.execute_python(
        sandbox_id,
        "client-session",
        "client_value = 5\nclient_value * 2",
    )
    assert code.status == "completed"
    assert code.result == "10"

    command = await client.exec_command(
        sandbox_id,
        "client-session",
        cmd="pwd; printf '%s\\n' \"$CLIENT_SESSION_MARK\"",
        timeout=30,
    )
    assert command.success is True
    assert "/workspace/client-contract" in command.stdout
    assert "session-env" in command.stdout

    interactive = await client.exec_command(
        sandbox_id,
        "client-session",
        cmd="read value; printf 'value=%s\\n' \"$value\"",
        yield_time_ms=300,
        timeout=30,
    )
    assert interactive.completed is False
    assert interactive.process_id

    stdin = await client.write_stdin(
        sandbox_id,
        "client-session",
        process_id=interactive.process_id,
        chars="from-client\n",
        yield_time_ms=1000,
    )
    assert stdin.completed is True
    assert "value=from-client" in stdin.stdout

    sleeper = await client.exec_command(
        sandbox_id,
        "client-session",
        cmd="sleep 30",
        yield_time_ms=300,
        timeout=60,
    )
    assert sleeper.completed is False
    assert sleeper.process_id

    processes = await client.list_processes(sandbox_id, "client-session")
    assert any(
        process.process_id == sleeper.process_id and process.completed is False
        for process in processes.processes
    )

    terminated = await client.terminate_process(
        sandbox_id,
        "client-session",
        sleeper.process_id,
    )
    assert terminated.success is True
    assert terminated.completed is True

    assert await client.delete_session(sandbox_id, "client-session") is True
    deleted = await client.delete_sandbox(sandbox_id)
    assert deleted.deleted is True
    await client.close()
