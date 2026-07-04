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
