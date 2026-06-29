"""Portable-variable templating and resolution — the manifest's portability layer.

Some resource fields hold ids only valid in the source pod/org and break a
re-import elsewhere: a workflow's ``assignee_pod_member_id`` and a
schedule/surface ``account_id``. On export each is replaced with a ``${name}``
placeholder and recorded under ``pod.json -> variables``; on import the
placeholders resolve (supplied value, or a per-type default) and any still
unresolved drop their field so the import still succeeds.

Shared so the CLI and backend template and resolve identically — one manifest
story across both.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from lemma_pod_bundle.jsonc import loads_jsonc

PLACEHOLDER_RE = re.compile(r"\$\{[A-Za-z0-9_]+\}")
# Legacy single-token placeholder for a pod-member assignee.
POD_MEMBER_TOKEN = "$POD_MEMBER"
# Fields whose values are non-portable ids, by variable type.
MEMBER_REF_FIELDS = frozenset({"assignee_pod_member_id"})
ACCOUNT_REF_FIELDS = frozenset({"account_id"})


def placeholder(name: str) -> str:
    return "${" + name + "}"


def _read_json(path: Path) -> dict[str, Any]:
    return loads_jsonc(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _slug_var_name(base: str, existing: dict[str, Any]) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(base).lower()).strip("_") or "var"
    name = cleaned
    index = 2
    while name in existing:
        name = f"{cleaned}_{index}"
        index += 1
    return name


def _tokenize_ref_fields(node: object, field_keys: frozenset[str], on_value) -> bool:
    """Recursively replace string values under ``field_keys`` with the placeholder
    returned by ``on_value(raw)``. Skips values already templated."""
    changed = False
    if isinstance(node, dict):
        for key, value in list(node.items()):
            if (
                key in field_keys
                and isinstance(value, str)
                and value
                and value != POD_MEMBER_TOKEN
                and not PLACEHOLDER_RE.fullmatch(value)
            ):
                node[key] = on_value(value)
                changed = True
            elif _tokenize_ref_fields(value, field_keys, on_value):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _tokenize_ref_fields(item, field_keys, on_value):
                changed = True
    return changed


def extract_portable_variables(bundle_root: Path, *, write: bool = True) -> dict[str, Any]:
    """Replace non-portable ids in workflows/schedules/surfaces with ``${name}``
    placeholders and record them under ``pod.json -> variables``. Returns the
    variables map (possibly empty)."""
    bundle_root = Path(bundle_root)
    pod_path = bundle_root / "pod.json"
    if not pod_path.is_file():
        return {}
    pod_data = _read_json(pod_path)
    variables: dict[str, Any] = dict(pod_data.get("variables") or {})
    by_value: dict[tuple[str, str], str] = {}

    def register(vtype: str, raw: str, base: str, meta: dict[str, Any]) -> str:
        key = (vtype, str(raw))
        if key in by_value:
            return placeholder(by_value[key])
        name = _slug_var_name(base, variables)
        variables[name] = {"type": vtype, "source_value": str(raw), **meta}
        by_value[key] = name
        return placeholder(name)

    def rewrite(resource_glob: str, field_keys, make_ref) -> None:
        for resource_json in sorted(bundle_root.glob(resource_glob)):
            data = loads_jsonc(resource_json.read_text(encoding="utf-8"))
            owner = resource_json.parent.name
            if _tokenize_ref_fields(data, field_keys, lambda raw, owner=owner: make_ref(owner, raw)):
                resource_json.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    rewrite(
        "workflows/*/*.json",
        MEMBER_REF_FIELDS,
        lambda owner, raw: register(
            "pod_member",
            raw,
            f"{owner}_assignee",
            {"description": f"Pod member assigned in workflow '{owner}'"},
        ),
    )
    rewrite(
        "schedules/*/*.json",
        ACCOUNT_REF_FIELDS,
        lambda owner, raw: register(
            "account",
            raw,
            f"{owner}_account",
            {"description": f"Connector account for schedule '{owner}'"},
        ),
    )
    rewrite(
        "surfaces/*/*.json",
        ACCOUNT_REF_FIELDS,
        lambda owner, raw: register(
            "account",
            raw,
            f"{owner}_account",
            {
                "description": f"Connector account for the {owner} surface",
                "platform": owner,
            },
        ),
    )

    if write and variables:
        pod_data["variables"] = variables
        _write_json(pod_path, pod_data)
    return variables


def declared_variables(bundle_root: Path) -> dict[str, Any]:
    """The ``variables`` block recorded in a bundle's pod.json (empty if none)."""
    pod_path = Path(bundle_root) / "pod.json"
    if not pod_path.is_file():
        return {}
    return dict(_read_json(pod_path).get("variables") or {})


def substitute_placeholders(node: object, replacements: dict[str, str]) -> object:
    """Return a copy of ``node`` with any string equal to a placeholder key
    replaced by its value (whole-value match, like the CLI)."""
    if isinstance(node, dict):
        return {key: substitute_placeholders(value, replacements) for key, value in node.items()}
    if isinstance(node, list):
        return [substitute_placeholders(item, replacements) for item in node]
    if isinstance(node, str) and node in replacements:
        return replacements[node]
    return node


def strip_unresolved_placeholders(node: object) -> object:
    """Drop dict entries whose value is still an unresolved ``${...}`` token so a
    literal placeholder never reaches a service (e.g. an unsupplied account)."""
    if isinstance(node, dict):
        return {
            key: strip_unresolved_placeholders(value)
            for key, value in node.items()
            if not (isinstance(value, str) and PLACEHOLDER_RE.fullmatch(value))
        }
    if isinstance(node, list):
        return [strip_unresolved_placeholders(item) for item in node]
    return node


def resolve_placeholders(node: object, replacements: dict[str, str]) -> object:
    """Substitute supplied placeholders, then drop any still unresolved."""
    return strip_unresolved_placeholders(substitute_placeholders(node, replacements))


def build_replacements(
    variables: dict[str, Any],
    *,
    supplied: dict[str, str] | None = None,
    pod_member_default: str | None = None,
) -> dict[str, str]:
    """Map each declared variable's ``${name}`` token to a concrete value:
    an explicitly supplied value wins; otherwise ``pod_member`` variables fall
    back to ``pod_member_default``. Unmapped variables are left out (and will be
    stripped). Also maps the legacy ``$POD_MEMBER`` token."""
    supplied = supplied or {}
    replacements: dict[str, str] = {}
    if pod_member_default is not None:
        replacements[POD_MEMBER_TOKEN] = pod_member_default
    for name, spec in variables.items():
        token = placeholder(name)
        if name in supplied:
            replacements[token] = supplied[name]
        elif str((spec or {}).get("type") or "") == "pod_member" and pod_member_default is not None:
            replacements[token] = pod_member_default
    return replacements
