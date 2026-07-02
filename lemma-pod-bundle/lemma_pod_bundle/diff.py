"""Table-column diff — the additive-vs-destructive classifier shared by the CLI
import plan and the backend's plan builder. Vendored from the CLI so both detect
data loss identically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Columns Lemma manages itself; never part of a user-authored diff.
SYSTEM_TABLE_COLUMNS = frozenset({"created_at", "updated_at", "user_id"})


@dataclass
class TableDiff:
    to_add: list[dict[str, Any]]
    to_remove: list[str]
    incompatible: list[str]

    @property
    def is_destructive(self) -> bool:
        """True when applying this diff would drop or rebuild a column — i.e.
        lose data — as opposed to a purely additive change."""
        return bool(self.to_remove or self.incompatible)


def is_system_table_column(column: dict[str, Any]) -> bool:
    return bool(column.get("system")) or str(column.get("name") or "") in SYSTEM_TABLE_COLUMNS


def _normalize_column_for_diff(
    column: dict[str, Any], *, primary_key: bool = False
) -> dict[str, Any]:
    type_name = (column.get("type_params") or {}).get("type") or column.get("type")
    normalized = {
        "name": column.get("name"),
        "type": type_name,
        "required": bool(column.get("required", False)),
        "unique": bool(column.get("unique", False)),
    }
    if primary_key:
        normalized["primary_key"] = True
    return normalized


def diff_table_columns(existing: dict[str, Any], desired: dict[str, Any]) -> TableDiff:
    """Classify the column delta from ``existing`` to ``desired`` into additive
    (to_add), destructive-by-removal (to_remove), and destructive-by-mutation
    (incompatible — type/required/unique changed, not migratable in place)."""
    primary_key = str(
        existing.get("primary_key_column") or desired.get("primary_key_column") or "id"
    )
    existing_columns = {
        str(column.get("name")): _normalize_column_for_diff(
            column, primary_key=str(column.get("name")) == primary_key
        )
        for column in existing.get("columns") or []
        if not is_system_table_column(column)
    }
    desired_columns = {
        str(column.get("name")): _normalize_column_for_diff(
            column, primary_key=str(column.get("name")) == primary_key
        )
        for column in desired.get("columns") or []
        if not is_system_table_column(column)
    }
    desired_columns_raw = {
        str(column.get("name")): column
        for column in desired.get("columns") or []
        if not is_system_table_column(column)
    }

    to_add = [
        desired_columns_raw[name]
        for name in desired_columns_raw.keys() - existing_columns.keys()
    ]
    to_remove = sorted(
        name
        for name in existing_columns.keys() - desired_columns.keys()
        if name and name != primary_key
    )

    # The primary key is immutable, same as in to_remove above: an applier
    # acting on "incompatible" drops and re-adds the column, and dropping the
    # pk is rejected at the table layer — a pk whose manifest shape merely
    # drifted (e.g. a UI-materialized required/unique id vs a bare bundle id)
    # must not turn into a failing rebuild step.
    incompatible: list[str] = []
    for name in sorted(existing_columns.keys() & desired_columns.keys()):
        if name == primary_key:
            continue
        if existing_columns[name] != desired_columns[name]:
            incompatible.append(name)

    return TableDiff(
        to_add=sorted(to_add, key=lambda item: str(item.get("name", ""))),
        to_remove=to_remove,
        incompatible=incompatible,
    )
