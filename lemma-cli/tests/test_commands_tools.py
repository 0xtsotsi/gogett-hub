from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import tools

runner = CliRunner()


def _make_client_and_captured():
    captured = {}

    class FakeTools:
        def web_search(self, *, query, max_results=10):
            captured["web_search"] = {"query": query, "max_results": max_results}
            return {"items": [{"title": "Top hit", "url": "https://example.com"}]}

        def report_feedback(self, **kwargs):
            captured["report_feedback"] = kwargs
            return {"ok": True}

    class FakeClient:
        def __init__(self):
            self.tools = FakeTools()

    return FakeClient(), captured


def _patch(monkeypatch, client):
    state = SimpleNamespace(
        config={"_runtime": {"pod": "pod-1"}, "defaults": {"org_id": "org-1"}},
        output="pretty",
        full=False,
    )
    monkeypatch.setattr(tools, "run_with_client", lambda ctx, fn: fn(client, state))


def test_tools_list_emits_known_tools(monkeypatch):
    client, _ = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["tools", "list", "--json"])

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    names = [item.get("name") for item in payload.get("items", [])]
    assert "web-search" in names
    assert "report-feedback" in names


def test_tools_web_search_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["tools", "web-search", "agent frameworks", "--limit", "3"])

    assert result.exit_code == 0, result.stdout
    assert captured["web_search"]["query"] == "agent frameworks"
    assert captured["web_search"]["max_results"] == 3


def test_tools_run_web_search_via_data(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    payload = json.dumps({"query": "rust async", "max_results": 5})
    result = runner.invoke(app, ["tools", "run", "web-search", "--data", payload])

    assert result.exit_code == 0, result.stdout
    assert captured["web_search"]["query"] == "rust async"
    assert captured["web_search"]["max_results"] == 5


def test_tools_report_feedback_dispatches_api(monkeypatch):
    client, captured = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(
        app,
        [
            "tools", "report-feedback",
            "--category", "cli",
            "--subject", "crash on startup",
            "--issue-encountered", "stack trace",
            "--expected-behavior", "starts",
            "--actual-behavior", "exits",
        ],
    )

    assert result.exit_code == 0, result.stdout
    sent = captured["report_feedback"]
    assert sent["category"] == "cli"
    assert sent["subject"] == "crash on startup"
    assert sent["suggested_next_steps"] is None


def test_tools_run_unknown_tool_fails(monkeypatch):
    client, _ = _make_client_and_captured()
    _patch(monkeypatch, client)

    result = runner.invoke(app, ["tools", "run", "nope", "--data", "{}"])

    assert result.exit_code != 0
    assert "Unknown tool" in result.stdout or "nope" in result.stdout
