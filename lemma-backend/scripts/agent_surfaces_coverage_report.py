from __future__ import annotations

import argparse
import glob
import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


COMMENT_MARKER = "<!-- lemma-agent-surfaces-coverage -->"
MODULE_PREFIX = "app/modules/agent_surfaces/"
PLATFORM_ROOT = "platforms"


def _pct(covered: int, statements: int) -> float:
    if statements <= 0:
        return 100.0
    return (covered / statements) * 100


def _format_pct(value: float) -> str:
    return f"{value:.1f}%"


def _escape_cell(value: object) -> str:
    text = html.escape(str(value), quote=False)
    return text.replace("|", "\\|")


def _table(headers: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_escape_cell(item) for item in row) + " |")
    return "\n".join(lines)


def _load_coverage(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def _module_name(filename: str) -> str:
    rel = filename.split(MODULE_PREFIX, 1)[-1]
    if "/" not in rel:
        return "root"
    return rel.split("/", 1)[0]


def _platform_name(filename: str) -> str | None:
    rel = filename.split(MODULE_PREFIX, 1)[-1]
    parts = rel.split("/")
    if not parts or parts[0] != PLATFORM_ROOT:
        return None
    if len(parts) == 2:
        return "platforms_core"
    return parts[1]


def _accumulate_coverage(
    coverage: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    top_level: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    platforms: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    files: list[dict[str, Any]] = []

    for filename, meta in coverage.get("files", {}).items():
        if "/tests/" in filename:
            continue
        summary = meta["summary"]
        statements = int(summary["num_statements"])
        missing = int(summary["missing_lines"])
        covered = statements - missing
        module = _module_name(filename)
        top_level[module][0] += statements
        top_level[module][1] += missing

        platform = _platform_name(filename)
        if platform:
            platforms[platform][0] += statements
            platforms[platform][1] += missing

        files.append(
            {
                "file": filename.split(MODULE_PREFIX, 1)[-1],
                "statements": statements,
                "missing": missing,
                "covered": covered,
                "coverage": _pct(covered, statements),
            }
        )

    def _rows(source: dict[str, list[int]]) -> list[dict[str, Any]]:
        rows = []
        for name, (statements, missing) in sorted(source.items()):
            covered = statements - missing
            rows.append(
                {
                    "name": name,
                    "statements": statements,
                    "missing": missing,
                    "covered": covered,
                    "coverage": _pct(covered, statements),
                }
            )
        return rows

    lowest = sorted(files, key=lambda row: (row["coverage"], -row["statements"]))[:10]
    return _rows(top_level), _rows(platforms), lowest


def _read_junit(path: Path) -> dict[str, float | int]:
    if not path.exists():
        return {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    root = ElementTree.fromstring(path.read_text())
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    for suite in suites:
        totals["tests"] += int(suite.attrib.get("tests", 0))
        totals["failures"] += int(suite.attrib.get("failures", 0))
        totals["errors"] += int(suite.attrib.get("errors", 0))
        totals["skipped"] += int(suite.attrib.get("skipped", 0))
        totals["time"] += float(suite.attrib.get("time", 0.0))
    return totals


def _read_junits(paths: list[Path]) -> dict[str, float | int]:
    totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0, "time": 0.0}
    for path in paths:
        one = _read_junit(path)
        for key in totals:
            totals[key] += one[key]  # type: ignore[operator]
    return totals


def build_summary(
    coverage: dict[str, Any] | None,
    *,
    phase: str,
    junit_paths: list[Path],
) -> dict[str, Any]:
    tests = _read_junits(junit_paths)
    if coverage is None:
        return {
            "phase": phase,
            "missing_coverage": True,
            "tests": tests,
            "totals": {},
            "top_level": [],
            "platforms": [],
            "lowest_files": [],
        }

    top_level, platforms, lowest = _accumulate_coverage(coverage)
    return {
        "phase": phase,
        "missing_coverage": False,
        "tests": tests,
        "totals": coverage.get("totals", {}),
        "top_level": top_level,
        "platforms": platforms,
        "lowest_files": lowest,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    phase = summary["phase"]
    title = "unit" if phase == "unit" else "combined unit + mocked e2e"
    lines = [COMMENT_MARKER, f"## Agent Surfaces Coverage ({title})", ""]
    lines.append("_Report-only: this PR does not fail on coverage thresholds yet._")
    lines.append("")

    tests = summary["tests"]
    lines.append(
        "**Tests:** "
        f"{tests['tests']} collected, "
        f"{tests['failures']} failed, "
        f"{tests['errors']} errors, "
        f"{tests['skipped']} skipped"
    )

    if summary["missing_coverage"]:
        lines.extend(
            [
                "",
                "Coverage artifact was not found, so module coverage could not be rendered.",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    totals = summary["totals"]
    statements = int(totals["num_statements"])
    missing = int(totals["missing_lines"])
    covered = int(totals["covered_lines"])
    percent = float(totals["percent_covered"])
    lines.append(
        f"**Total:** {_format_pct(percent)} ({covered}/{statements} lines, "
        f"{missing} missing)"
    )
    lines.append("")
    lines.append("### Top-level modules")
    lines.append(
        _table(
            ["Module", "Statements", "Missing", "Coverage"],
            [
                [
                    row["name"],
                    row["statements"],
                    row["missing"],
                    _format_pct(row["coverage"]),
                ]
                for row in summary["top_level"]
            ],
        )
    )
    lines.append("")
    lines.append("### Platform areas")
    lines.append(
        _table(
            ["Area", "Statements", "Missing", "Coverage"],
            [
                [
                    row["name"],
                    row["statements"],
                    row["missing"],
                    _format_pct(row["coverage"]),
                ]
                for row in summary["platforms"]
            ],
        )
    )
    lines.append("")
    lines.append("### Lowest-covered files")
    lines.append(
        _table(
            ["File", "Statements", "Missing", "Coverage"],
            [
                [
                    row["file"],
                    row["statements"],
                    row["missing"],
                    _format_pct(row["coverage"]),
                ]
                for row in summary["lowest_files"]
            ],
        )
    )
    lines.append("")
    lines.append(
        "Optional live-provider smoke tests remain separate behind "
        "`LEMMA_RUN_SURFACE_LIVE_E2E=1` / `surface-live`."
    )
    return "\n".join(lines).rstrip() + "\n"


def _paths_from_glob(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(Path(path) for path in sorted(glob.glob(pattern)))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-json", type=Path, required=True)
    parser.add_argument("--phase", choices=["unit", "combined"], required=True)
    parser.add_argument("--junit", action="append", type=Path, default=[])
    parser.add_argument("--junit-glob", action="append", default=[])
    parser.add_argument("--markdown-out", type=Path, required=True)
    parser.add_argument("--summary-json-out", type=Path, required=True)
    args = parser.parse_args()

    junit_paths = list(args.junit) + _paths_from_glob(args.junit_glob)
    summary = build_summary(
        _load_coverage(args.coverage_json),
        phase=args.phase,
        junit_paths=junit_paths,
    )
    markdown = render_markdown(summary)

    args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_json_out.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_out.write_text(markdown)
    args.summary_json_out.write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
