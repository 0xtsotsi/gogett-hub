"""Pod bundle module configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class PodBundleSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    pod_bundle_state_ttl_seconds: int = Field(
        default=6 * 60 * 60,
        description=(
            "TTL for ephemeral import/export/publish job state in Redis, "
            "refreshed on every write. Staged bundle archives in object storage "
            "are swept on the same horizon. Expired state is never an error to "
            "recover from — re-running plans a fresh diff against the pod."
        ),
    )
    pod_bundle_max_archive_bytes: int = Field(
        default=100 * 1024 * 1024,
        description="Maximum accepted size (bytes) of an uploaded/fetched bundle archive.",
    )
    pod_bundle_max_uncompressed_bytes: int = Field(
        default=500 * 1024 * 1024,
        description=(
            "Ceiling on the total uncompressed size of a bundle archive "
            "(zip-bomb guard applied during extraction)."
        ),
    )
    pod_bundle_staging_prefix: str = Field(
        default="pod-bundle-staging",
        description=(
            "Key prefix (and local-backend subdirectory) under which staged "
            "bundle archives live in object storage."
        ),
    )
    pod_bundle_github_api_base: str = Field(
        default="https://api.github.com",
        description=(
            "Base URL for the GitHub REST API used to fetch a public repo's "
            "zipball on import. Overridable (POD_BUNDLE_GITHUB_API_BASE) so tests "
            "can point it at a local fixture server."
        ),
    )
    pod_bundle_github_fetch_timeout_seconds: float = Field(
        default=30.0,
        description="HTTP timeout (seconds) for fetching a GitHub repo zipball.",
    )

    # --- Export download URL retention -----------------------------------
    pod_bundle_export_url_ttl_seconds: int = Field(
        default=24 * 60 * 60,
        description=(
            "Default lifetime (seconds) of an export's signed download URL, and "
            "the retention horizon of the staged export archive + its Redis "
            "state. Longer than the ~6h import horizon so a shared export stays "
            "fetchable; re-export is cheap when it expires."
        ),
    )
    pod_bundle_export_url_max_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        description="Hard ceiling (seconds) on a caller-requested export URL TTL.",
    )

    # --- Export data/asset caps (never dump GBs) -------------------------
    # The schema always exports in full; only row data and file/asset BYTES are
    # bounded, best-effort with warnings surfaced on the export status.
    pod_bundle_export_max_records_per_table: int = Field(
        default=5000,
        description="Max rows written to a table's data.csv seed (per table).",
    )
    pod_bundle_export_max_records_total: int = Field(
        default=50000,
        description=(
            "Max rows across all tables in one export; once reached, remaining "
            "tables export schema-only (no data.csv)."
        ),
    )
    pod_bundle_export_max_file_bytes: int = Field(
        default=10 * 1024 * 1024,
        description="Skip any single exported file/asset larger than this (bytes).",
    )
    pod_bundle_export_max_files_total_bytes: int = Field(
        default=100 * 1024 * 1024,
        description=(
            "Running byte budget for included files/assets in one export; stop "
            "adding once reached."
        ),
    )


pod_bundle_settings = PodBundleSettings()
