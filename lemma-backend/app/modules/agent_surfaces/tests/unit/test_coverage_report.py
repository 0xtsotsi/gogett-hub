from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_reporter():
    script = (
        Path(__file__).resolve().parents[5]
        / "scripts"
        / "agent_surfaces_coverage_report.py"
    )
    spec = importlib.util.spec_from_file_location(
        "agent_surfaces_coverage_report", script
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_coverage_report_groups_modules_platforms_and_escapes_markdown(tmp_path):
    reporter = _load_reporter()
    coverage = {
        "files": {
            "app/modules/agent_surfaces/services/ingress_service.py": {
                "summary": {"num_statements": 10, "missing_lines": 2}
            },
            "app/modules/agent_surfaces/platforms/teams/service.py": {
                "summary": {"num_statements": 20, "missing_lines": 10}
            },
            "app/modules/agent_surfaces/platforms/slack/service.py": {
                "summary": {"num_statements": 5, "missing_lines": 0}
            },
            "app/modules/agent_surfaces/root|odd.py": {
                "summary": {"num_statements": 4, "missing_lines": 1}
            },
            "app/modules/agent_surfaces/tests/unit/test_ignored.py": {
                "summary": {"num_statements": 99, "missing_lines": 99}
            },
        },
        "totals": {
            "covered_lines": 26,
            "num_statements": 39,
            "percent_covered": 66.666,
            "missing_lines": 13,
        },
    }
    junit = tmp_path / "junit.xml"
    junit.write_text(
        '<testsuite tests="3" failures="0" errors="0" skipped="1" time="1.2" />'
    )

    summary = reporter.build_summary(coverage, phase="combined", junit_paths=[junit])
    markdown = reporter.render_markdown(summary)

    assert summary["top_level"] == [
        {
            "name": "platforms",
            "statements": 25,
            "missing": 10,
            "covered": 15,
            "coverage": 60.0,
        },
        {
            "name": "root",
            "statements": 4,
            "missing": 1,
            "covered": 3,
            "coverage": 75.0,
        },
        {
            "name": "services",
            "statements": 10,
            "missing": 2,
            "covered": 8,
            "coverage": 80.0,
        },
    ]
    assert [row["name"] for row in summary["platforms"]] == ["slack", "teams"]
    assert summary["tests"]["tests"] == 3
    assert summary["tests"]["skipped"] == 1
    assert "root\\|odd.py" in markdown
    assert "66.7%" in markdown
    assert reporter.COMMENT_MARKER in markdown


def test_coverage_report_missing_artifact_fallback(tmp_path):
    reporter = _load_reporter()
    summary = reporter.build_summary(None, phase="unit", junit_paths=[])

    markdown = reporter.render_markdown(summary)

    assert summary["missing_coverage"] is True
    assert "Coverage artifact was not found" in markdown


def test_coverage_report_cli_writes_markdown_and_summary(tmp_path):
    reporter = _load_reporter()
    coverage_json = tmp_path / "coverage.json"
    coverage_json.write_text(
        json.dumps(
            {
                "files": {
                    "app/modules/agent_surfaces/domain/entities.py": {
                        "summary": {"num_statements": 8, "missing_lines": 0}
                    }
                },
                "totals": {
                    "covered_lines": 8,
                    "num_statements": 8,
                    "percent_covered": 100.0,
                    "missing_lines": 0,
                },
            }
        )
    )
    markdown_out = tmp_path / "comment.md"
    summary_out = tmp_path / "summary.json"

    code = reporter.main.__globals__["argparse"]  # prove module loaded normally
    assert code is not None

    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(Path(reporter.__file__)),
            "--coverage-json",
            str(coverage_json),
            "--phase",
            "unit",
            "--markdown-out",
            str(markdown_out),
            "--summary-json-out",
            str(summary_out),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "100.0%" in result.stdout
    assert "100.0%" in markdown_out.read_text()
    assert json.loads(summary_out.read_text())["totals"]["covered_lines"] == 8
