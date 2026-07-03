"""Use-case e2e: a bundled connector (surface) is planned as an importable step
whose account is a REQUIRED variable, and apply enforces it.

The exporter tokenizes a surface's ``account_id`` into a ``${..._account}``
variable because the source org's account id is meaningless in the target org.
This test proves the plan surfaces that variable as required and that applying
without it is rejected — so a connector is never silently imported unbound. (The
actual surface create + account binding is covered by the applier unit test, which
does not need a live connector account.)
"""

from __future__ import annotations

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    pack_fixture_bundle,
    start_and_plan_import,
)

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def test_connector_account_is_required_and_enforced(
    authenticated_client, test_pod, worker
):
    pod_id = test_pod["id"]
    zip_bytes = pack_fixture_bundle("with_connectors")
    import_id = await start_and_plan_import(authenticated_client, pod_id, zip_bytes)

    got = await authenticated_client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert got.status_code == status.HTTP_200_OK, got.text
    plan = got.json()["plan"]

    # The connector account is surfaced as a REQUIRED variable...
    account_var = next(
        (v for v in plan["variables"] if v["name"] == "slack_account"), None
    )
    assert account_var is not None, plan["variables"]
    assert account_var["kind"] == "account"
    assert account_var["required"] is True

    # ...and there is a SURFACE step to apply.
    assert any(s["kind"] == "SURFACE" for s in plan["steps"]), plan["steps"]

    # Applying without the required account is rejected before anything runs.
    apply = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={"variables": {}}
    )
    assert apply.status_code == 422, apply.text
    assert "slack_account" in apply.text
