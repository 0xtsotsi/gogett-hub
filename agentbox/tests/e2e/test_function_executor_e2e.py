from __future__ import annotations

import time
from http import HTTPStatus
from uuid import uuid4

import pytest


pytestmark = [pytest.mark.e2e, pytest.mark.agentbox]


def test_private_function_executor_supports_sync_schema_and_async_job(
    agentbox_server,
    sandbox_id,
    fake_lemma_function_server,
):
    lemma_base_url, function = fake_lemma_function_server
    manager = agentbox_server.client

    created = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"LEMMA_BASE_URL": lemma_base_url}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text

    headers = {
        "Authorization": f"Bearer {function.token}",
        "X-API-Key": agentbox_server.api_key,
    }
    run_id = str(uuid4())
    execute = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": run_id,
            "input_data": {"text": "agentbox sync"},
            "async_job": False,
            "timeout_seconds": 120,
        },
        headers=headers,
        timeout=180,
    )
    assert execute.status_code == HTTPStatus.OK, execute.text
    result = execute.json()
    assert result["status"] == "completed"
    assert result["output_data"] == {
        "result": "AGENTBOX SYNC",
        "user_id": function.user_id,
        "base_url": lemma_base_url,
    }
    assert result["code_hash"]
    assert any(entry["stream"] == "stdout" for entry in result["logs"])

    schemas = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/schemas",
        body={"code_hash": result["code_hash"]},
        headers=headers,
        timeout=180,
    )
    assert schemas.status_code == HTTPStatus.OK, schemas.text
    schema_payload = schemas.json()
    assert schema_payload["input_schema"]["title"] == "AgentBoxInput"
    assert schema_payload["output_schema"]["title"] == "AgentBoxOutput"
    assert schema_payload["code_hash"] == result["code_hash"]

    job_run_id = str(uuid4())
    accepted = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": job_run_id,
            "input_data": {"text": "agentbox job"},
            "async_job": True,
            "timeout_seconds": 120,
        },
        headers=headers,
        timeout=180,
    )
    assert accepted.status_code == HTTPStatus.OK, accepted.text
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["run_id"] == job_run_id

    deadline = time.monotonic() + 30
    job_status = None
    while time.monotonic() < deadline:
        job_response = manager.request_json(
            "GET",
            f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{job_run_id}",
            headers={"X-API-Key": agentbox_server.api_key},
            timeout=60,
        )
        assert job_response.status_code == HTTPStatus.OK, job_response.text
        job_status = job_response.json()
        if job_status["status"] == "completed":
            break
        time.sleep(0.5)
    assert job_status is not None
    assert job_status["status"] == "completed"
    assert job_status["output_data"]["result"] == "AGENTBOX JOB"

    logs = manager.request_json(
        "GET",
        f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{job_run_id}/logs",
        headers={"X-API-Key": agentbox_server.api_key},
    )
    assert logs.status_code == HTTPStatus.OK, logs.text
    assert logs.json()["run_id"] == job_run_id
    assert any(entry["stream"] == "stdout" for entry in logs.json()["logs"])

    deleted = manager.request_json(
        "DELETE",
        f"/sandboxes/{sandbox_id}/apps/function_executor/runs/{job_run_id}",
        headers={"X-API-Key": agentbox_server.api_key},
    )
    assert deleted.status_code == HTTPStatus.OK, deleted.text
    assert deleted.json()["deleted"] is True


def test_private_function_executor_requires_function_token(
    agentbox_server,
    sandbox_id,
    fake_lemma_function_server,
):
    lemma_base_url, function = fake_lemma_function_server
    created = agentbox_server.client.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"LEMMA_BASE_URL": lemma_base_url}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text

    response = agentbox_server.client.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": str(uuid4()),
            "input_data": {"text": "missing token"},
            "async_job": False,
        },
        headers={"X-API-Key": agentbox_server.api_key},
        timeout=180,
    )
    assert response.status_code == HTTPStatus.UNAUTHORIZED


def test_private_function_executor_installs_declared_python_package(
    agentbox_server,
    sandbox_id,
    fake_lemma_package_function_server,
):
    """A function declaring `#python_packages: cowsay` and importing it at module
    top level executes only if the executor installed the dependency first."""
    lemma_base_url, function = fake_lemma_package_function_server
    manager = agentbox_server.client

    created = manager.request_json(
        "PUT",
        f"/sandboxes/{sandbox_id}",
        body={"env": {"LEMMA_BASE_URL": lemma_base_url}},
        timeout=180,
    )
    assert created.status_code == HTTPStatus.OK, created.text

    headers = {
        "Authorization": f"Bearer {function.token}",
        "X-API-Key": agentbox_server.api_key,
    }
    execute = manager.request_json(
        "POST",
        f"/sandboxes/{sandbox_id}/apps/function_executor/"
        f"pods/{function.pod_id}/functions/{function.name}/execute",
        body={
            "run_id": str(uuid4()),
            "input_data": {"text": "moo"},
            "async_job": False,
            "timeout_seconds": 180,
        },
        headers=headers,
        timeout=240,
    )
    assert execute.status_code == HTTPStatus.OK, execute.text
    result = execute.json()
    assert result["status"] == "completed", result
    assert "moo" in result["output_data"]["rendered"]

