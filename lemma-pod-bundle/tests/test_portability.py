from __future__ import annotations

import json
from pathlib import Path

from lemma_pod_bundle.portability import (
    build_replacements,
    declared_variables,
    extract_portable_variables,
    resolve_placeholders,
)


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _bundle(tmp_path: Path) -> Path:
    root = tmp_path / "pod"
    _write(root / "pod.json", {"name": "pod"})
    _write(root / "surfaces" / "slack" / "slack.json",
           {"platform": "SLACK", "account_id": "acc-123"})
    _write(root / "schedules" / "nightly" / "nightly.json",
           {"name": "nightly", "schedule_type": "WEBHOOK", "account_id": "acc-123"})
    _write(root / "workflows" / "review" / "review.json",
           {"name": "review", "nodes": [{"assignee_pod_member_id": "pm-9"}]})
    return root


def test_export_templates_non_portable_ids_and_records_variables(tmp_path):
    root = _bundle(tmp_path)
    variables = extract_portable_variables(root)

    # account_id (shared value) collapses to one variable; member is its own.
    types = sorted({(v["type"]) for v in variables.values()})
    assert types == ["account", "pod_member"]

    surface = json.loads((root / "surfaces" / "slack" / "slack.json").read_text())
    schedule = json.loads((root / "schedules" / "nightly" / "nightly.json").read_text())
    workflow = json.loads((root / "workflows" / "review" / "review.json").read_text())
    # The raw ids are gone, replaced by ${name} placeholders.
    assert surface["account_id"].startswith("${") and surface["account_id"].endswith("}")
    # The same source account id reuses one variable across surface + schedule.
    assert surface["account_id"] == schedule["account_id"]
    assert workflow["nodes"][0]["assignee_pod_member_id"].startswith("${")

    assert declared_variables(root) == variables


def test_resolution_substitutes_supplied_and_defaults_members():
    variables = {
        "slack_account": {"type": "account", "source_value": "acc-123"},
        "review_assignee": {"type": "pod_member", "source_value": "pm-9"},
    }
    replacements = build_replacements(
        variables,
        supplied={"slack_account": "acc-new"},
        pod_member_default="pm-self",
    )
    surface = resolve_placeholders({"account_id": "${slack_account}", "platform": "SLACK"}, replacements)
    workflow = resolve_placeholders({"assignee_pod_member_id": "${review_assignee}"}, replacements)

    assert surface["account_id"] == "acc-new"      # supplied value wins
    assert workflow["assignee_pod_member_id"] == "pm-self"  # member defaults to importer


def test_unresolved_account_is_dropped_not_leaked():
    variables = {"ghost_account": {"type": "account"}}
    replacements = build_replacements(variables, supplied={}, pod_member_default="pm-self")
    out = resolve_placeholders({"account_id": "${ghost_account}", "keep": "x"}, replacements)
    assert "account_id" not in out  # unsupplied account -> field dropped
    assert out["keep"] == "x"


def test_export_resolution_round_trip(tmp_path):
    root = _bundle(tmp_path)
    extract_portable_variables(root)
    variables = declared_variables(root)
    # Resolve everything to the importing member (account supplied, member default).
    account_var = next(n for n, s in variables.items() if s["type"] == "account")
    replacements = build_replacements(
        variables, supplied={account_var: "acc-target"}, pod_member_default="pm-target"
    )
    surface = resolve_placeholders(
        json.loads((root / "surfaces" / "slack" / "slack.json").read_text()), replacements
    )
    assert surface["account_id"] == "acc-target"
