"""Use-case e2e: the ``support_inbox`` fixture bundle imports into a real pod and
its resources actually *work*.

The import applies a ``tickets`` table, a ``triage_ticket`` function (with the
datastore grants that let it write records), and a ``support_agent`` (with its
toolsets + grants). The test then runs the imported function to prove the grants
were applied on import — without them the function's datastore write returns a
real 403 instead of a ticket.
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    import_and_apply,
    pack_fixture_bundle,
    provision_workspace,
    run_function,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker, pytest.mark.workspace]


async def test_support_inbox_imports_and_function_executes(
    authenticated_client, test_pod, worker, workspace_api
):
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("support_inbox")

    # Provision the workspace from the backend first (see helper docstring), then
    # import so the worker reuses a sandbox that can reach this test's backend.
    await provision_workspace(authenticated_client, pod_id)
    await import_and_apply(authenticated_client, pod_id, zip_bytes)

    # --- resources exist -------------------------------------------------
    tbl = await authenticated_client.get(f"/pods/{pod_id}/datastore/tables/tickets")
    assert tbl.status_code == status.HTTP_200_OK, tbl.text
    assert {c["name"] for c in tbl.json()["columns"]} >= {
        "subject",
        "body",
        "priority",
        "status",
    }

    func = await authenticated_client.get(f"/pods/{pod_id}/functions/triage_ticket")
    assert func.status_code == status.HTTP_200_OK, func.text

    agent = await authenticated_client.get(f"/pods/{pod_id}/agents/support_agent")
    assert agent.status_code == status.HTTP_200_OK, agent.text
    # Toolsets travelled with the agent on import.
    assert set(agent.json().get("toolsets") or []) >= {"POD", "USER_INTERACTION"}

    # --- the imported grants were applied --------------------------------
    agent_perms = await authenticated_client.get(
        f"/pods/{pod_id}/agents/support_agent/permissions"
    )
    assert agent_perms.status_code == status.HTTP_200_OK, agent_perms.text
    grant_by_resource = {
        (g["resource_type"], g["resource_name"]): set(g["permission_ids"])
        for g in agent_perms.json()["grants"]
    }
    assert grant_by_resource.get(("function", "triage_ticket"), set()) >= {
        "function.execute"
    }
    assert grant_by_resource.get(("datastore_table", "tickets"), set()) >= {
        "datastore.record.read"
    }

    # --- the imported function actually runs (proves its table grants) ---
    urgent = await run_function(
        authenticated_client,
        pod_id,
        "triage_ticket",
        {"subject": "URGENT: checkout is broken", "body": "Customers can't pay."},
    )
    urgent_out = urgent["output_data"]
    assert urgent_out["denied"] is False, urgent_out
    assert urgent_out["priority"] == "HIGH", urgent_out
    assert urgent_out["ticket_id"], urgent_out

    normal = await run_function(
        authenticated_client,
        pod_id,
        "triage_ticket",
        {"subject": "Question about billing", "body": "How do I update my card?"},
    )
    normal_out = normal["output_data"]
    assert normal_out["denied"] is False, normal_out
    assert normal_out["priority"] == "LOW", normal_out

    # --- both tickets landed in the table --------------------------------
    records = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/tickets/records"
    )
    assert records.status_code == status.HTTP_200_OK, records.text
    rows = records.json()["items"]
    by_priority = {r["priority"] for r in rows}
    assert by_priority == {"HIGH", "LOW"}, rows
    assert all(r["status"] == "OPEN" for r in rows), rows
