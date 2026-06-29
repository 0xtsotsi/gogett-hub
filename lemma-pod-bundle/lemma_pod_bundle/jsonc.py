"""Tolerant JSON parsing for hand-edited bundle manifests (comments + trailing
commas). Vendored verbatim from the CLI so both consumers parse identically."""

from __future__ import annotations

import json
from typing import Any


def strip_jsonc(text: str) -> str:
    """Remove `//` line comments and `/* */` block comments from JSONC text,
    leaving string literals (and any `//` inside them, e.g. URLs) untouched.
    Comment characters are replaced with spaces so byte offsets — and thus the
    line/column in any downstream json error — stay accurate."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escaped = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out.append(" ")
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                out.append("\n" if text[i] == "\n" else " ")
                i += 1
            i += 2
            out.append("  ")
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Blank a trailing comma (one before a closing `}`/`]`, ignoring whitespace)
    so a common hand-edit mistake still parses. Expects comment-free input."""
    out = list(text)
    n = len(out)
    in_string = False
    escaped = False
    for i, ch in enumerate(out):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == ",":
            j = i + 1
            while j < n and out[j] in " \t\r\n":
                j += 1
            if j < n and out[j] in "}]":
                out[i] = " "
    return "".join(out)


def loads_jsonc(text: str) -> Any:
    """json.loads that tolerates JSONC comments and trailing commas."""
    return json.loads(_strip_trailing_commas(strip_jsonc(text)))
