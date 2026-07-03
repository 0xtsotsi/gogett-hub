"""Use-case e2e: the ``moderation_queue`` fixture bundle.

The richest bundle — table + function + agent + workflow. Proves the imported
``flag_content`` function really screens submissions and writes records (its
grants were applied on import), that the ``reviewer_agent`` imports with its
toolsets + grants, and that the ``screen_flow`` workflow imports with its
function cross-reference intact.
"""

from __future__ import annotations

import json

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    import_and_apply,
    pack_fixture_bundle,
    provision_workspace,
    run_function,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker, pytest.mark.workspace]


async def test_moderation_queue_imports_and_screens_submissions(
    authenticated_client, test_pod, worker, workspace_api
):
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("moderation_queue")

    await provision_workspace(authenticated_client, pod_id)
    await import_and_apply(authenticated_client, pod_id, zip_bytes)

    # --- resources exist -------------------------------------------------
    for path in (
        "datastore/tables/submissions",
        "functions/flag_content",
        "agents/reviewer_agent",
        "workflows/screen_flow",
    ):
        resp = await authenticated_client.get(f"/pods/{pod_id}/{path}")
        assert resp.status_code == status.HTTP_200_OK, (path, resp.text)

    # --- agent imported with toolsets + grants ---------------------------
    agent = await authenticated_client.get(f"/pods/{pod_id}/agents/reviewer_agent")
    assert set(agent.json().get("toolsets") or []) >= {"POD", "USER_INTERACTION"}
    perms = await authenticated_client.get(
        f"/pods/{pod_id}/agents/reviewer_agent/permissions"
    )
    grant_by_resource = {
        (g["resource_type"], g["resource_name"]): set(g["permission_ids"])
        for g in perms.json()["grants"]
    }
    assert grant_by_resource.get(("function", "flag_content"), set()) >= {
        "function.execute"
    }

    # --- imported function screens for real (record grants applied) ------
    flagged = await run_function(
        authenticated_client,
        pod_id,
        "flag_content",
        {"author": "bot123", "content": "Buy now, this is not a scam at all"},
    )
    flagged_out = flagged["output_data"]
    assert flagged_out["denied"] is False, flagged_out
    assert flagged_out["status"] == "FLAGGED", flagged_out
    assert "scam" in flagged_out["reason"], flagged_out

    clean = await run_function(
        authenticated_client,
        pod_id,
        "flag_content",
        {"author": "alice", "content": "Thanks for the helpful walkthrough!"},
    )
    clean_out = clean["output_data"]
    assert clean_out["denied"] is False, clean_out
    assert clean_out["status"] == "APPROVED", clean_out

    rows = (
        await authenticated_client.get(
            f"/pods/{pod_id}/datastore/tables/submissions/records"
        )
    ).json()["items"]
    assert {r["status"] for r in rows} == {"FLAGGED", "APPROVED"}, rows

    # --- workflow imported referencing flag_content ----------------------
    wf = await authenticated_client.get(f"/pods/{pod_id}/workflows/screen_flow")
    wf_json = wf.json()
    assert "FUNCTION" in {n.get("type") for n in wf_json.get("nodes", [])}, wf_json
    assert "flag_content" in json.dumps(wf_json), wf_json
