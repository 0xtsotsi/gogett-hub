# lemma-pod-bundle

Shared, environment-independent pod-bundle logic so the import experience has
**one source of truth** across the CLI and the backend.

- `requirements` — derive a bundle's `requirements` (connectors, people, variables,
  data) and tier-ordered `capabilities` from what it carries.
- `diff` — compare table columns to detect additive vs destructive (data-losing)
  schema changes.
- `jsonc` — tolerant JSON parsing (comments + trailing commas) for hand-edited
  bundle manifests.

Consumed by `lemma-cli` (export/`pods requirements`) and `lemma-backend`
(the `pod_import` plan builder) via local `[tool.uv.sources]` path deps.
