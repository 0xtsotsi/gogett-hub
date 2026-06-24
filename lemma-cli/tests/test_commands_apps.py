from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import apps

runner = CliRunner()


def _make_client_and_captured():
    captured = {}

    class FakeApps:
        def list(self, *, limit=100):
            captured["limit"] = limit
            return {"items": [{"id": "app-1", "name": "my-app", "url": "https://app.example.com"}]}

        def get(self, name):
            captured["get"] = name
            return {"id": "app-1", "name": name}

        def create(self, request):
            captured["create"] = request.to_dict() if hasattr(request, "to_dict") else request
            return {"id": "app-1", "name": "my-app"}

        def update(self, name, request):
            captured["update"] = name
            captured["update_payload"] = request.to_dict() if hasattr(request, "to_dict") else request
            return {"id": "app-1", "name": name}

        def delete(self, name):
            captured["deleted"] = name

    class FakePod:
        def __init__(self):
            self.apps = FakeApps()

    class FakeClient:
        def pod(self, pod_id):
            captured["pod_id"] = pod_id
            return FakePod()

    return FakeClient(), captured


def _patch(monkeypatch, client):
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="pretty",
        full=False,
    )
    monkeypatch.setattr(apps, "run_with_client", lambda ctx, fn: fn(client, state))


def test_apps_list_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "apps", "list"])

    assert result.exit_code == 0, result.stdout
    assert "my-app" in result.stdout
    assert captured["pod_id"] == "pod-1"


def test_apps_list_limit_flag(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["--pod", "pod-1", "apps", "list", "--limit", "5"])

    assert result.exit_code == 0, result.stdout
    assert captured["limit"] == 5


def test_apps_list_json_output(monkeypatch):
    client, _ = _make_client_and_captured()
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="json",
        full=False,
    )
    monkeypatch.setattr(apps, "run_with_client", lambda ctx, fn: fn(client, state))

    result = runner.invoke(app, ["--json", "--pod", "pod-1", "apps", "list"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "items" in payload


def test_apps_get_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["apps", "get", "my-app", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured["get"] == "my-app"


def test_apps_create_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"name": "my-app"})
    result = runner.invoke(app, ["--pod", "pod-1", "apps", "create", "--data", payload])

    assert result.exit_code == 0, result.stdout
    assert captured["create"]["name"] == "my-app"


def test_apps_update_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"description": "updated"})
    result = runner.invoke(
        app, ["apps", "update", "my-app", "--data", payload, "--pod", "pod-1"]
    )

    assert result.exit_code == 0, result.stdout
    assert captured["update"] == "my-app"
    assert captured["update_payload"]["description"] == "updated"


def test_apps_delete_with_yes_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["apps", "delete", "my-app", "--yes", "--pod", "pod-1"])

    assert result.exit_code == 0, result.stdout
    assert captured.get("deleted") == "my-app"


def test_apps_delete_without_yes_refuses_noninteractive(monkeypatch):
    client, _ = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["apps", "delete", "my-app", "--pod", "pod-1"])

    assert result.exit_code != 0
    assert "--yes" in result.stdout or "non-interactive" in result.stdout
