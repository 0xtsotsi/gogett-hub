"""Shared pod-bundle logic for the guided import experience.

One source of truth across lemma-cli and lemma-backend for: requirements /
capabilities extraction, table-column diffing, and tolerant JSON parsing.
"""

from lemma_pod_bundle.bundle import (
    RESOURCE_KINDS,
    list_resource_names,
    read_manifest,
    read_table_data,
)
from lemma_pod_bundle.diff import TableDiff, diff_table_columns
from lemma_pod_bundle.jsonc import loads_jsonc
from lemma_pod_bundle.requirements import (
    CAPABILITY_TIER_ORDER,
    extract_requirements,
    read_requirements,
    unresolved_requirements,
)

__all__ = [
    "CAPABILITY_TIER_ORDER",
    "RESOURCE_KINDS",
    "TableDiff",
    "diff_table_columns",
    "extract_requirements",
    "list_resource_names",
    "loads_jsonc",
    "read_manifest",
    "read_table_data",
    "read_requirements",
    "unresolved_requirements",
]
