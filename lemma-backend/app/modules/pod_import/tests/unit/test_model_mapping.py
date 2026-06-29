"""Pure round-trip test for the PodImport entity<->model mapping (no DB session
needed — just object construction and attribute reads)."""

from __future__ import annotations

from uuid import uuid7

from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.domain.value_objects import (
    ImportAction,
    ImportStatus,
    ImportStep,
    ImportStepStatus,
)
from app.modules.pod_import.infrastructure.models import PodImportModel


def _entity() -> PodImportEntity:
    imp = PodImportEntity.create(
        pod_id=uuid7(),
        user_id=uuid7(),
        source_name="acme-crm",
        plan=[
            ImportStep(resource_type="tables", resource_name="contacts",
                       action=ImportAction.CREATE),
            ImportStep(resource_type="tables", resource_name="orders",
                       action=ImportAction.UPDATE, destructive=True),
        ],
        requirements={"connectors": [{"key": "slack"}]},
        capabilities=[{"tier": "data", "summary": "Creates 2 tables"}],
    )
    imp.begin_apply()
    imp.record_step_completed(imp.plan[0])  # a checkpoint to round-trip
    return imp


def test_entity_to_model_to_entity_round_trips():
    original = _entity()

    model = PodImportModel.from_entity(original)
    restored = model.to_entity()

    assert restored.id == original.id
    assert restored.pod_id == original.pod_id
    assert restored.user_id == original.user_id
    assert restored.source_name == "acme-crm"
    assert restored.status is ImportStatus.APPLYING
    assert restored.requirements == {"connectors": [{"key": "slack"}]}
    assert restored.capabilities == [{"tier": "data", "summary": "Creates 2 tables"}]

    # Plan + per-step checkpoint survive the JSONB round-trip.
    assert [s.key for s in restored.plan] == [("tables", "contacts"), ("tables", "orders")]
    assert restored.plan[0].status is ImportStepStatus.COMPLETED
    assert restored.plan[1].destructive is True
    # The resume brain still reads the right next step after a round-trip.
    assert restored.next_pending_step().resource_name == "orders"


def test_apply_entity_copies_mutable_state_onto_existing_row():
    entity = _entity()
    model = PodImportModel.from_entity(entity)

    # Simulate progress + a later checkpoint, then persist onto the same row.
    entity.record_step_completed(entity.plan[1])
    entity.complete()
    model.apply_entity(entity)

    assert model.status == ImportStatus.COMPLETED.value
    assert model.completed_at is not None
    assert all(step["status"] == ImportStepStatus.COMPLETED.value for step in model.plan)
