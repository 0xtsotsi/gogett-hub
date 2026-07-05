# OpenAPI specs for package-free connectors

Each JSON here is an OpenAPI description consumed at catalog-import time by
`scripts/import_connector_catalog.py` (`_sync_openapi_operations`) via
`app/modules/connectors/infrastructure/openapi/spec_import.py`. Operations are
executed at runtime by `OpenApiHttpExecutor` — there is no codegen and no
per-connector Python package.

A connector opts in by adding an `openapi` block to its entry in
`scripts/lemma_apps_config.json` (`spec_path`, `server_url`, `operation_allowlist`,
`overrides`, `include_raw`).

## github.json

A **curated subset** of the official GitHub REST API description
(https://github.com/github/rest-api-description, `descriptions/api.github.com/api.github.com.json`).
The full document is ~8 MB / 1000+ operations; we keep only the operations in the
connector's `operation_allowlist` plus the component schemas they reference.

To refresh or extend: take the upstream spec, keep the allowlisted `paths` and
every `components.schemas` entry reachable from them (follow `$ref`s transitively —
the parser inlines refs but leaves unresolved `$ref` stubs if a referenced schema is
missing), and drop everything else. The `repos/upload-release-asset` operation keeps
its operation-level `servers` override (`uploads.github.com`).
