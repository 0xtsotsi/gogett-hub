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


pod_bundle_settings = PodBundleSettings()
