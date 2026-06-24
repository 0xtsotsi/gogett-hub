from __future__ import annotations

import pytest
from types import SimpleNamespace

from typer.testing import CliRunner


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def pod_state():
    return SimpleNamespace(
        config={
            "_runtime": {"pod": "pod-1"},
            "defaults": {"org_id": "org-1"},
        },
        output="pretty",
        full=False,
    )


@pytest.fixture
def json_state():
    return SimpleNamespace(
        config={
            "_runtime": {"pod": "pod-1"},
            "defaults": {"org_id": "org-1"},
        },
        output="json",
        full=False,
    )


@pytest.fixture
def patch_run(monkeypatch):
    def _patch(module, *, client, state):
        monkeypatch.setattr(module, "run_with_client", lambda ctx, fn: fn(client, state))

    return _patch
