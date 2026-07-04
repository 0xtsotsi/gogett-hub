"""Minimal dotenv parsing shared across the CLI.

Kept dependency-free (no python-dotenv) and in ``cli_core`` so both the app
bundler (``cli_app``) and the project-env loader can use one implementation —
``cli_core`` must not import from ``cli_app``.
"""

from __future__ import annotations

from pathlib import Path


def _strip_env_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.replace(r"\"", '"').replace(r"\\", "\\")


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env``-style file into a dict.

    Blank lines, ``#`` comments and lines without ``=`` are skipped; an optional
    ``export `` prefix is stripped; values may be single/double quoted. Returns an
    empty dict when the file is absent.
    """
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if not key:
            continue
        values[key] = _strip_env_quotes(value)
    return values
