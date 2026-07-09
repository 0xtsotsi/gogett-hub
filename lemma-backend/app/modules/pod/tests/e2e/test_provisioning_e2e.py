from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from app.modules.pod.domain.pod_entities import PodProvisioningStatus
from app.modules.pod.events.pod_handlers import _begin_provisioning
from app.modules.pod.infrastructure.models import Pod

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def _create_pod(client, organization_id: str) -> dict:
    response = await client.post(
        "/pods",
        json={
            "name": f"Provisioning E2E {uuid4()}",
            "organization_id": organization_id,
        },
        follow_redirects=True,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _wait_for_status(client, pod_id: str, expected: str, timeout: int = 30) -> dict:
    for _ in range(timeout * 5):
        response = await client.get(f"/pods/{pod_id}")
        assert response.status_code == 200, response.text
        body = response.json()
        if body["provisioning_status"] == expected:
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(
        f"Pod provisioning remained {body['provisioning_status']}; wanted {expected}"
    )


async def test_failed_provisioning_retry_reaches_ready(
    authenticated_client,
    fixed_test_org,
    worker,
    db_session,
):
    pod = await _create_pod(authenticated_client, fixed_test_org["id"])
    pod_id = pod["id"]
    await _wait_for_status(authenticated_client, pod_id, "READY")

    await db_session.execute(
        update(Pod)
        .where(Pod.id == UUID(pod_id))
        .values(
            provisioning_status=PodProvisioningStatus.FAILED.value,
            provisioning_attempts=3,
            provisioning_error_type="ConnectionError",
            provisioning_error_code="DATASTORE_UNAVAILABLE",
        )
    )
    await db_session.commit()

    retry = await authenticated_client.post(f"/pods/{pod_id}/provisioning/retry")
    assert retry.status_code == 202, retry.text
    accepted = retry.json()
    assert accepted["provisioning_status"] == "PROVISIONING"
    assert accepted["provisioning_attempts"] == 0
    assert accepted["provisioning_started_at"] is None

    repaired = await _wait_for_status(authenticated_client, pod_id, "READY")
    assert repaired["provisioning_attempts"] == 1
    assert repaired["provisioning_error_type"] is None
    assert repaired["provisioning_error_code"] is None
    assert repaired["provisioning_completed_at"] is not None


async def test_concurrent_duplicate_provisioning_claims_only_once(
    authenticated_client,
    fixed_test_org,
    db_session,
):
    pod = await _create_pod(authenticated_client, fixed_test_org["id"])
    pod_id = UUID(pod["id"])

    await db_session.execute(
        update(Pod)
        .where(Pod.id == pod_id)
        .values(
            provisioning_status=PodProvisioningStatus.UNKNOWN.value,
            provisioning_attempts=0,
            provisioning_started_at=None,
            provisioning_completed_at=None,
        )
    )
    await db_session.commit()

    claims = await asyncio.gather(
        _begin_provisioning(pod_id),
        _begin_provisioning(pod_id),
    )

    assert sorted(claim for claim in claims if claim is not None) == [1]
    assert claims.count(None) == 1
