"""The API response is what both renderers consume — verify it serializes the
entity faithfully (status, plan, progress, destructive flags)."""

from __future__ import annotations

from uuid import uuid7

from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.domain.value_objects import ImportAction, ImportStep
from app.modules.pod_import.api.schemas import PodImportResponse


def test_response_from_entity_reflects_plan_and_progress():
    imp = PodImportEntity.create(
        pod_id=uuid7(),
        user_id=uuid7(),
        source_name="acme",
        plan=[
            ImportStep(resource_type="tables", resource_name="a", action=ImportAction.CREATE),
            ImportStep(resource_type="tables", resource_name="b",
                       action=ImportAction.UPDATE, destructive=True),
        ],
        capabilities=[{"tier": "data", "summary": "Creates 2 tables"}],
    )
    imp.begin_apply()
    imp.record_step_completed(imp.plan[0])

    resp = PodImportResponse.from_entity(imp)

    assert resp.status == "APPLYING"
    assert resp.progress_done == 1 and resp.progress_total == 2
    assert [s.resource_name for s in resp.plan] == ["a", "b"]
    assert resp.plan[0].status == "COMPLETED"
    assert resp.plan[1].action == "UPDATE"
    assert resp.plan[1].destructive is True
    # Round-trips through JSON cleanly (what the SDK clients receive).
    assert resp.model_dump(mode="json")["capabilities"][0]["tier"] == "data"
