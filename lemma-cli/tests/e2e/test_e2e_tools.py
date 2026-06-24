from __future__ import annotations

import pytest

from .helpers import cli_json

pytestmark = pytest.mark.e2e


def test_tools_list_returns_ok(backend_server, test_user):
    """tools list should exit 0 even if no tools are enabled."""
    payload = cli_json(
        ["tools", "list"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
    )
    assert isinstance(payload, (list, dict))


def test_tools_list_produces_valid_json(backend_server, test_user):
    payload = cli_json(
        ["tools", "list"],
        base_url=backend_server["base_url"],
        token=test_user["token"],
    )
    assert isinstance(payload, (list, dict))
