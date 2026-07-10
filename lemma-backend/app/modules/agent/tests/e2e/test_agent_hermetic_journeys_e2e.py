"""Required public-boundary agent journeys with deterministic model tokens."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.test_support.e2e.scripted_model import (
    script_text,
    script_tool_call,
)

pytestmark = pytest.mark.e2e

_RUNTIME_SECRET = "CANARY_AGENT_RUNTIME_SECRET_93a5"


async def _create_runtime_profile(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
) -> dict:
    response = await authenticated_client.post(
        f"/organizations/{fixed_test_org['id']}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Hermetic FunctionModel {uuid4().hex[:8]}",
            "base_url": f"{e2e_settings.agentbox_api_url}/v1",
            "api_key": _RUNTIME_SECRET,
            "default_model_name": "mock-safe-model",
            "model_names": ["mock-safe-model"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    payload = response.json()
    assert payload["has_credentials"] is True
    assert "credentials" not in payload
    assert _RUNTIME_SECRET not in response.text
    return payload


async def _create_pod(authenticated_client, fixed_test_org) -> dict:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Hermetic Agent Pod {uuid4().hex[:8]}",
            "description": "Public agent lifecycle E2E",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _collect_sse(response) -> list[dict]:
    events: list[dict] = []
    async with asyncio.timeout(30):
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line.removeprefix("data: "))
            events.append(event)
            if event["type"] in {"completed", "stopped", "error"}:
                break
    return events


async def _send_message(
    authenticated_client,
    pod_id: str,
    conversation_id: str,
    content: str,
) -> list[dict]:
    url = f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    async with authenticated_client.stream(
        "POST",
        url,
        json={"content": content, "metadata": {"client": "hermetic-e2e"}},
        timeout=60,
    ) as response:
        assert response.status_code == status.HTTP_200_OK, await response.aread()
        return await _collect_sse(response)


async def _wait_for_title(
    authenticated_client,
    pod_id: str,
    conversation_id: str,
) -> str:
    for _ in range(100):
        response = await authenticated_client.get(
            f"/pods/{pod_id}/conversations/{conversation_id}"
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        title = response.json().get("title")
        if title:
            return str(title)
        await asyncio.sleep(0.1)
    raise AssertionError("Worker did not persist a conversation title")


async def _wait_for_usage(
    authenticated_client,
    *,
    organization_id: str,
    pod_id: str,
    agent_id: str,
    run_id: str,
) -> dict:
    for _ in range(100):
        response = await authenticated_client.get(
            f"/usage/organizations/{organization_id}/events",
            params={
                "pod_id": pod_id,
                "agent_id": agent_id,
                "usage_kind": "LLM",
                "days": 1,
            },
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        event = next(
            (
                item
                for item in response.json()["items"]
                if item["agent_run_id"] == run_id
            ),
            None,
        )
        if event is not None:
            return event
        await asyncio.sleep(0.1)
    raise AssertionError(f"Usage for agent run {run_id} was not persisted")


@pytest.mark.asyncio
async def test_public_http_sse_lifecycle_persists_messages_title_usage_and_history(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    e2e_settings,
    worker,
):
    del worker  # session fixture keeps the production streaq worker alive
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"lifecycle_{uuid4().hex[:8]}"
    create_agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Reply using the scripted deterministic model.",
            "description": "Hermetic lifecycle agent",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
            "metadata": {"suite": "required-e2e"},
        },
    )
    assert create_agent.status_code == status.HTTP_201_CREATED, create_agent.text
    agent = create_agent.json()

    duplicate = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": agent_name, "instruction": "duplicate"},
    )
    assert duplicate.status_code == status.HTTP_409_CONFLICT, duplicate.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "instructions": "Use current UI context.",
            "metadata": {
                "mock_llm_script": [script_text("Hermetic lifecycle complete.")],
                "source": "public-http-e2e",
            },
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Verify the complete public lifecycle.",
    )
    assert events, "SSE returned no frames"
    assert events[-1]["type"] == "completed", events
    assert not [event for event in events if event["type"] == "error"], events
    token_text = "".join(
        str(event.get("data", ""))
        for event in events
        if event["type"] == "token" and event.get("kind") == "text"
    )
    assert "Hermetic lifecycle complete" in token_text
    run_id = events[-1]["agent_run_id"]

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    items = messages.json()["items"]
    assert [item["sequence"] for item in items] == sorted(
        item["sequence"] for item in items
    )
    assert any(
        item["role"] == "user"
        and item["text"] == "Verify the complete public lifecycle."
        and item["metadata"]["client"] == "hermetic-e2e"
        for item in items
    )
    assert any(
        item["role"] == "assistant"
        and item["text"] == "Hermetic lifecycle complete."
        and item["metadata"].get("is_final_answer")
        for item in items
    )

    title = await _wait_for_title(authenticated_client, pod_id, conversation_id)
    assert title == "Verify the complete public lifecycle."
    usage = await _wait_for_usage(
        authenticated_client,
        organization_id=fixed_test_org["id"],
        pod_id=pod_id,
        agent_id=agent["id"],
        run_id=run_id,
    )
    assert usage["conversation_id"] == conversation_id
    assert usage["user_id"] == fixed_test_user["id"]
    assert usage["status"] == "COMPLETED"

    idle_stream = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/stream"
    )
    assert idle_stream.status_code == status.HTTP_200_OK, idle_stream.text
    assert idle_stream.content == b""

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/conversations",
        params={"agent_name": agent_name, "metadata.source": "public-http-e2e"},
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [conversation_id]

    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/conversations/{conversation_id}",
        json={"instructions": "Updated after the completed run."},
    )
    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert updated.json()["instructions"] == "Updated after the completed run."

    deleted = await authenticated_client.delete(f"/pods/{pod_id}/agents/{agent_name}")
    assert deleted.status_code == status.HTTP_200_OK, deleted.text
    missing = await authenticated_client.get(f"/pods/{pod_id}/agents/{agent_name}")
    assert missing.status_code == status.HTTP_404_NOT_FOUND, missing.text


@pytest.mark.asyncio
async def test_scripted_todo_and_workspace_tools_stream_and_persist_real_results(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    worker,
):
    del worker
    runtime = await _create_runtime_profile(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"tools_{uuid4().hex[:8]}"
    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Execute the scripted tools.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [
                "TODO",
                "WORKSPACE_CLI",
                "VIEW_IMAGE",
                "SKILLS",
                "SPEECH",
            ],
        },
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text

    script = [
        script_tool_call(
            "write_todos",
            {"todos": ["- [ ] Inspect input", "- [x] Persist result"]},
            tool_call_id="todo-1",
        ),
        script_tool_call(
            "exec_command",
            {
                "cmd": "printf 'workspace-proof' > proof.txt && cat proof.txt",
                "comment": "Create deterministic workspace proof",
            },
            tool_call_id="shell-1",
        ),
        script_tool_call(
            "execute_python",
            {
                "code": (
                    "import base64\n"
                    "from pathlib import Path\n"
                    "Path('pixel.png').write_bytes(base64.b64decode("
                    "'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Z2S8AAAAASUVORK5CYII='))\n"
                    "print(21 * 2)"
                ),
                "comment": "Create an inspectable image and compute a value",
            },
            tool_call_id="python-1",
        ),
        script_tool_call(
            "view_image",
            {"workspace_file_path": "pixel.png"},
            tool_call_id="image-1",
        ),
        script_tool_call(
            "manage_process",
            {"action": "list", "comment": "Check tracked processes"},
            tool_call_id="process-list-1",
        ),
        script_tool_call(
            "manage_process",
            {"action": "input", "chars": ""},
            tool_call_id="process-invalid-1",
        ),
        script_tool_call(
            "exec_command",
            {"cmd": "exit 7", "comment": "Exercise a user-command failure"},
            tool_call_id="shell-failure-1",
        ),
        script_tool_call("list_skills", {}, tool_call_id="skills-list-1"),
        script_tool_call(
            "load_skill",
            {"name": "browser"},
            tool_call_id="skill-load-1",
        ),
        script_tool_call(
            "load_skill",
            {"name": "does-not-exist"},
            tool_call_id="skill-missing-1",
        ),
        script_tool_call(
            "say",
            {"text": "Hermetic spoken response"},
            tool_call_id="speech-say-1",
        ),
        script_tool_call(
            "listen",
            {"file_path": "missing-audio.ogg"},
            tool_call_id="speech-listen-1",
        ),
        script_text("Todo, workspace, skills, and speech tools completed."),
    ]
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "Tool execution",
            "metadata": {"mock_llm_script": script},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _send_message(
        authenticated_client,
        pod_id,
        conversation_id,
        "Run the todo and workspace proof steps.",
    )
    assert events[-1]["type"] == "completed", events
    assert {event.get("kind") for event in events if event["type"] == "token"} >= {
        "text",
        "tool",
    }

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == status.HTTP_200_OK, messages.text
    items = messages.json()["items"]
    tool_calls = {
        item["tool_name"]: item for item in items if item["kind"] == "TOOL_CALL"
    }
    tool_returns = {
        item["tool_name"]: item for item in items if item["kind"] == "TOOL_RETURN"
    }
    tool_returns_by_id = {
        item["tool_call_id"]: item for item in items if item["kind"] == "TOOL_RETURN"
    }
    assert {
        "write_todos",
        "exec_command",
        "execute_python",
        "view_image",
        "manage_process",
        "list_skills",
        "load_skill",
        "say",
        "listen",
    } <= tool_calls.keys()
    assert tool_returns["write_todos"]["tool_result"]["success"] is True
    assert "workspace-proof" in str(tool_returns_by_id["shell-1"]["tool_result"])
    assert tool_returns_by_id["shell-failure-1"]["tool_result"]["success"] is False
    assert "42" in str(tool_returns["execute_python"]["tool_result"])
    assert tool_returns["view_image"]["tool_result"]
    assert tool_returns["list_skills"]["tool_result"]["success"] is True
    assert tool_returns_by_id["skill-load-1"]["tool_result"]["success"] is True
    assert tool_returns_by_id["skill-missing-1"]["tool_result"]["success"] is False
    assert tool_returns_by_id["process-list-1"]["tool_result"]["success"] is True
    assert tool_returns_by_id["process-invalid-1"]["tool_result"]["success"] is False
    assert tool_returns["say"]["tool_result"]["success"] is False
    assert tool_returns["listen"]["tool_result"]["success"] is False

    persisted = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}"
    )
    assert persisted.status_code == status.HTTP_200_OK, persisted.text
    todos = persisted.json()["metadata"]["todos"]
    assert todos == [
        {"content": "Inspect input", "done": False},
        {"content": "Persist result", "done": True},
    ]
