"""Use-case e2e: the ``lead_scoring`` fixture bundle.

Covers a richer bundle — table + function + workflow — and proves the imported
``score_lead`` function actually runs against the datastore (its record grants
were applied on import), while the workflow imports with its function
cross-reference intact.

(Schedules aren't exercised here: a TIME schedule's ``schedule_job`` needs a live
scheduler API the session-scoped e2e worker can't reach. Schedule *import
mapping* is covered by the applier unit tests, and schedule *firing* by the
schedule module's own full-stack e2e.)
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


async def test_lead_scoring_imports_function_workflow_and_schedule(
    authenticated_client, test_pod, worker, workspace_api
):
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("lead_scoring")

    await provision_workspace(authenticated_client, pod_id)
    await import_and_apply(authenticated_client, pod_id, zip_bytes)

    # --- resources exist -------------------------------------------------
    assert (
        await authenticated_client.get(f"/pods/{pod_id}/datastore/tables/leads")
    ).status_code == status.HTTP_200_OK
    assert (
        await authenticated_client.get(f"/pods/{pod_id}/functions/score_lead")
    ).status_code == status.HTTP_200_OK

    # --- the imported function runs (record grants applied on import) ----
    hot = await run_function(
        authenticated_client,
        pod_id,
        "score_lead",
        {"company": "BigCo", "employees": 300, "plan_interest": "enterprise"},
    )
    hot_out = hot["output_data"]
    assert hot_out["denied"] is False, hot_out
    assert hot_out["score"] == 100, hot_out  # min(60, 300//5) + 40, capped at 100
    assert hot_out["tier"] == "HIGH", hot_out

    cold = await run_function(
        authenticated_client,
        pod_id,
        "score_lead",
        {"company": "SmallCo", "employees": 10, "plan_interest": "free"},
    )
    cold_out = cold["output_data"]
    assert cold_out["denied"] is False, cold_out
    assert cold_out["score"] == 2, cold_out  # 10//5 + 0
    assert cold_out["tier"] == "LOW", cold_out

    rows = (
        await authenticated_client.get(f"/pods/{pod_id}/datastore/tables/leads/records")
    ).json()["items"]
    assert {r["tier"] for r in rows} == {"HIGH", "LOW"}, rows

    # --- workflow imported with its FUNCTION node referencing score_lead --
    wf = await authenticated_client.get(f"/pods/{pod_id}/workflows/score_flow")
    assert wf.status_code == status.HTTP_200_OK, wf.text
    wf_json = wf.json()
    node_types = {n.get("type") for n in wf_json.get("nodes", [])}
    assert "FUNCTION" in node_types, wf_json
    # The function cross-reference survived the round trip.
    assert "score_lead" in json.dumps(wf_json), wf_json
