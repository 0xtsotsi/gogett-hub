from __future__ import annotations

from lemma_pod_bundle.diff import diff_table_columns


def _cols(*names):
    return [{"name": n, "type": "text"} for n in names]


def test_additive_change_is_not_destructive():
    existing = {"primary_key_column": "id", "columns": _cols("id", "name")}
    desired = {"primary_key_column": "id", "columns": _cols("id", "name", "email")}
    diff = diff_table_columns(existing, desired)
    assert [c["name"] for c in diff.to_add] == ["email"]
    assert diff.to_remove == []
    assert diff.is_destructive is False


def test_dropped_column_is_destructive():
    existing = {"primary_key_column": "id", "columns": _cols("id", "name", "email")}
    desired = {"primary_key_column": "id", "columns": _cols("id", "name")}
    diff = diff_table_columns(existing, desired)
    assert diff.to_remove == ["email"]
    assert diff.is_destructive is True


def test_type_change_is_incompatible_and_destructive():
    existing = {"primary_key_column": "id", "columns": [
        {"name": "id", "type": "text"}, {"name": "age", "type": "text"}]}
    desired = {"primary_key_column": "id", "columns": [
        {"name": "id", "type": "text"}, {"name": "age", "type": "number"}]}
    diff = diff_table_columns(existing, desired)
    assert diff.incompatible == ["age"]
    assert diff.is_destructive is True


def test_system_and_primary_key_columns_are_ignored():
    existing = {"primary_key_column": "id", "columns": [
        {"name": "id", "type": "text"},
        {"name": "created_at", "type": "datetime"},
        {"name": "name", "type": "text"}]}
    desired = {"primary_key_column": "id", "columns": _cols("id", "name")}
    diff = diff_table_columns(existing, desired)
    # Dropping the system column is not flagged; id (pk) never removed.
    assert diff.to_remove == []
    assert diff.is_destructive is False


def test_a_drifted_primary_key_is_never_marked_incompatible():
    # A UI-materialized pk carries required/unique flags a bare bundle pk
    # doesn't — the pk is immutable, so drift must not become a rebuild step.
    existing = {"primary_key_column": "id", "columns": [
        {"name": "id", "type": "uuid", "required": True, "unique": True},
        {"name": "name", "type": "text"}]}
    desired = {"primary_key_column": "id", "columns": [
        {"name": "id", "type": "uuid"}, {"name": "name", "type": "text"}]}
    diff = diff_table_columns(existing, desired)
    assert diff.incompatible == []
    assert diff.is_destructive is False
