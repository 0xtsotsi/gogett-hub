"""Unit tests for pod-bundle API request/response schemas."""

from __future__ import annotations

from app.modules.pod_bundle.api.schemas import ExportStartRequest


def test_export_defaults_to_resources_only_no_data():
    """Row data is opt-in: an export request with no explicit flag carries only
    pod resources (with_data defaults to False), matching the CLI."""
    request = ExportStartRequest()
    assert request.with_data is False


def test_export_with_data_can_be_opted_in():
    """The rare seed/setup-data case is still expressible via an explicit flag."""
    request = ExportStartRequest(with_data=True)
    assert request.with_data is True


def test_export_data_tables_defaults_to_none():
    """Per-table seeding is opt-in: no tables selected unless named."""
    assert ExportStartRequest().data_tables is None


def test_export_data_tables_selects_specific_tables():
    """A caller can seed only specific setup tables without dumping the pod."""
    request = ExportStartRequest(data_tables=["settings", "roles"])
    assert request.data_tables == ["settings", "roles"]
    assert request.with_data is False


def test_export_total_record_cap_is_conservative():
    """The whole-export row cap is kept low so a bundle can't dump the DB."""
    from app.modules.pod_bundle.config import pod_bundle_settings

    assert pod_bundle_settings.pod_bundle_export_max_records_total == 10000
