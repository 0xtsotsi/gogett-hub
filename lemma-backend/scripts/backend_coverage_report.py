from __future__ import annotations

import argparse
import glob
import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


COMMENT_MARKER_PREFIX = "lemma-backend-coverage"


def comment_marker(phase: str) -> str:
    return f"<!-- {COMMENT_MARKER_PREFIX}:{phase} -->"


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


def _relative_app_file(filename: str) -> str:
    return filename.split("app/", 1)[-1] if "app/" in filename else filename


def _module_name(filename: str) -> str:
    rel = _relative_app_file(filename)
    parts = rel.split("/")
    if not parts:
        return "root"
    if parts[0] == "modules" and len(parts) > 1:
        return parts[1]
    if parts[0] == "core":
        return "core"
    return parts[0] or "root"


def _should_skip_file(filename: str) -> bool:
    rel = _relative_app_file(filename)
    name = rel.rsplit("/", 1)[-1]
    return "/tests/" in filename or name.startswith("test_") or name == "conftest.py"


def _accumulate_coverage(
    coverage: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    modules: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    files: list[dict[str, Any]] = []

    for filename, meta in coverage.get("files", {}).items():
        if _should_skip_file(filename):
            continue
        summary = meta["summary"]
        statements = int(summary["num_statements"])
        missing = int(summary["missing_lines"])
        covered = statements - missing
        module = _module_name(filename)
        modules[module][0] += statements
        modules[module][1] += missing
        files.append(
            {
                "file": _relative_app_file(filename),
                "module": module,
                "statements": statements,
                "missing": missing,
                "covered": covered,
                "coverage": _pct(covered, statements),
            }
        )

    module_rows = []
    for name, (statements, missing) in sorted(modules.items()):
        covered = statements - missing
        module_rows.append(
            {
                "name": name,
                "statements": statements,
                "missing": missing,
                "covered": covered,
                "coverage": _pct(covered, statements),
            }
        )

    lowest = sorted(files, key=lambda row: (row["coverage"], -row["statements"]))[:15]
    return module_rows, lowest


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
            "modules": [],
            "lowest_files": [],
        }

    modules, lowest = _accumulate_coverage(coverage)
    return {
        "phase": phase,
        "missing_coverage": False,
        "tests": tests,
        "totals": coverage.get("totals", {}),
        "modules": modules,
        "lowest_files": lowest,
    }


def _phase_title(phase: str) -> str:
    return phase.replace("-", " ")


def render_markdown(summary: dict[str, Any]) -> str:
    phase = summary["phase"]
    lines = [comment_marker(phase), f"## Backend Coverage ({_phase_title(phase)})", ""]
    lines.append(
        "_The aggregate PR gate requires 70% overall, 65% for schedule, and "
        "90% changed-code coverage._"
    )
    lines.append("")

    tests = summary["tests"]
    lines.append(
        "**Tests:** "
        f"{tests['tests']} run, "
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
    lines.append("### Module Coverage")
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
                for row in summary["modules"]
            ],
        )
    )
    lines.append("")
    lines.append("### Lowest-Covered Files")
    lines.append(
        _table(
            ["File", "Module", "Statements", "Missing", "Coverage"],
            [
                [
                    row["file"],
                    row["module"],
                    row["statements"],
                    row["missing"],
                    _format_pct(row["coverage"]),
                ]
                for row in summary["lowest_files"]
            ],
        )
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
    parser.add_argument("--phase", required=True)
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
