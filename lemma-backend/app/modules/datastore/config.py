"""Datastore module configuration.

Field names are unchanged from the former monolithic ``Settings`` so the
environment variables resolve identically (``DATASTORE_QUERY_MAX_ROWS``,
``PDF_RENDER_DPI``, ``KREUZBERG_URL``, …).

NOTE: ``datastore_database_url`` deliberately stays in core ``Settings`` — it is
a second database URL (infrastructure, parallel to ``database_url``) and the e2e
test infra mutates it on the shared settings object. Embedding settings also stay
in core (consumed by ``app/core/embeddings``).
"""

from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatastoreSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    # Ad-hoc SQL query guardrails
    datastore_query_role: str = Field(
        default="lemma_datastore_query",
        description=(
            "Non-superuser, NOBYPASSRLS database role that ad-hoc datastore SQL "
            "queries run under (via SET LOCAL ROLE) so row-level security is "
            "enforced. Must be a plain SQL identifier."
        ),
    )
    datastore_query_statement_timeout_ms: int = Field(
        default=5000,
        description="Per-statement timeout (ms) applied to ad-hoc datastore SQL queries (query.execute).",
    )
    datastore_query_max_rows: int = Field(
        default=1000,
        description="Maximum rows returned by an ad-hoc datastore SQL query; extra rows are truncated.",
    )
    datastore_query_max_cost: float = Field(
        default=1_000_000.0,
        description="Reject ad-hoc datastore SQL queries whose EXPLAIN total cost exceeds this ceiling.",
    )
    datastore_query_max_plan_rows: int = Field(
        default=5_000_000,
        description="Reject ad-hoc datastore SQL queries whose EXPLAIN estimated row count exceeds this ceiling.",
    )

    # Document processing
    document_processing_max_concurrency: int = Field(
        default=2,
        description=(
            "Maximum concurrent document extraction jobs per worker process. This "
            "is the one guaranteed lever on Kreuzberg's peak RAM: each extraction "
            "carries a ~1.5GB model+runtime floor plus a per-doc working set, so "
            "the multiplier matters. Kept at 2 so a mixed native+scanned load "
            "stays under a 4GB kreuzberg instance (measured ~3.9GB peak at 2; OOM "
            "at higher concurrency or 4 CPU). Lower to 1 for more headroom; pair "
            "with the kreuzberg container held at cpus=2."
        ),
    )
    document_processing_debounce_seconds: int = Field(
        default=300,
        description="Debounce window for datastore file content updates before enqueueing document processing.",
    )
    pdf_ocr_detection_sample_pages: int = Field(
        default=5,
        description=(
            "How many pages to sample (spread across the document) when probing a "
            "PDF with pypdfium2 to decide scanned-vs-native before extraction."
        ),
    )
    pdf_ocr_detection_min_chars_per_page: int = Field(
        default=100,
        description=(
            "If a sampled PDF averages fewer than this many extracted text "
            "characters per page it is treated as scanned (force OCR, 300-DPI "
            "images); otherwise native (no forced OCR, 150-DPI images). The "
            "layout/table config is applied to both so every doc gets rich "
            "markdown — only force_ocr and image DPI differ. Only consulted when "
            "``document_processing_ocr_enabled`` is true."
        ),
    )
    document_processing_ocr_enabled: bool = Field(
        default=False,
        description=(
            "Opt-in switch for the heavy scanned-PDF path (Kreuzberg only). When "
            "false (the default), every document is extracted with the fast "
            "digital-first config — layout + tables + 150-DPI images, no OCR — so "
            "processing stays ~10-20s and bounded in RAM: the up-front pypdfium "
            "scanned-vs-native probe AND the reactive forced-OCR retry are both "
            "skipped. When true, scanned PDFs are detected and OCR'd at 300 DPI "
            "(Tesseract), which is the real resource/latency spike. Scanned docs "
            "under the default degrade to their text layer; bring your own "
            "markdown for those, or flip this on. Env: "
            "``DOCUMENT_PROCESSING_OCR_ENABLED``."
        ),
    )

    # Document-processor adapter selection
    document_processor: Literal["auto", "kreuzberg", "markitdown", "docling"] = Field(
        default="auto",
        description=(
            "Which document-processor adapter converts non-markdown files to "
            "markdown. 'markitdown' runs IN-PROCESS (optional dep; MIT; light, no "
            "models; strongest on office formats, weaker on PDFs). 'docling' calls "
            "a Docling Serve container OVER HTTP (MIT; beautiful research-paper/"
            "book markdown with tables; ML-heavy + GPU-oriented, so it runs as "
            "its own service and the backend stays lean — opt-in only, set this "
            "to 'docling' + DOCLING_SERVE_URL). 'kreuzberg' calls the Kreuzberg "
            "REST container. 'auto' (the default) uses 'kreuzberg' when "
            "KREUZBERG_URL is set, else the in-process 'markitdown'; it never "
            "auto-selects docling. Env: ``DOCUMENT_PROCESSOR``."
        ),
    )

    # Docling Serve (over-HTTP document processor; MIT-licensed alternative to
    # Kreuzberg — runs as its own container, keeps the backend torch-free).
    docling_serve_url: Optional[str] = Field(
        default=None,
        description=(
            "Docling Serve base URL (e.g. http://localhost:5001) for the 'docling' "
            "document processor. Unset by default; set it (or DOCUMENT_PROCESSOR="
            "docling) to route conversion to a Docling Serve container."
        ),
    )
    docling_request_timeout_seconds: float = Field(
        default=300.0,
        description=(
            "HTTP timeout (seconds) for a Docling Serve /v1/convert/file request. "
            "Higher than Kreuzberg's since Docling's layout+table models are CPU-"
            "heavier per document."
        ),
    )

    # Kreuzberg
    kreuzberg_url: Optional[str] = Field(
        default="http://localhost:8002",
        description="Kreuzberg API URL for document processing",
    )
    kreuzberg_request_timeout_seconds: float = Field(
        default=180.0,
        description="HTTP timeout (seconds) for Kreuzberg extract and chunk requests",
    )
    kreuzberg_transient_retry_attempts: int = Field(
        default=5,
        description=(
            "Attempts for transient (connection/timeout) Kreuzberg failures before "
            "giving up. Bump in resource-constrained CI/e2e where a single shared "
            "Kreuzberg can briefly stall under concurrent load."
        ),
    )
    kreuzberg_transient_retry_base_delay_seconds: float = Field(
        default=0.5,
        description="Base delay (seconds) for exponential backoff between Kreuzberg retries.",
    )

    # PDF page rendering (on-demand, in-backend via pypdfium2 + Pillow)
    pdf_render_dpi: int = Field(
        default=150, description="DPI used when rasterizing PDF pages to images."
    )
    pdf_render_max_long_edge: int = Field(
        default=1568,
        description=(
            "Max long-edge in pixels for a rendered page image. ~1568px matches "
            "the resolution vision models consume, so larger renders are wasted."
        ),
    )
    pdf_render_jpeg_quality: int = Field(
        default=80, description="JPEG quality (1-100) for rendered/cached page images."
    )
    pdf_render_max_pages_per_call: int = Field(
        default=10,
        description="Max pages a single render request may produce, to bound payload + memory.",
    )
    pdf_render_concurrency: int = Field(
        default=2,
        description=(
            "Max concurrent in-process PDF rasterizations. PDF rendering is "
            "CPU/memory-heavy; this gate prevents bursts from stacking renders and "
            "exhausting memory."
        ),
    )

    # Signed datastore file URLs.
    # Tokens are signed by the unified app/core/crypto signer (HKDF off the
    # required SECRET_ENCRYPTION_KEY) — no per-feature secret is configured here.
    datastore_file_url_expiry_seconds: int = Field(
        default=3600,
        description="Default lifetime (seconds) of a signed datastore file URL.",
    )

    # Public (short) signed datastore URLs
    datastore_signed_url_default_expiry_seconds: int = Field(
        default=10800,
        description=(
            "Default lifetime (seconds) of a public, hit-capped datastore signed "
            "(short) URL. Used when a caller does not specify an expiry."
        ),
    )
    datastore_signed_url_max_expiry_seconds: int = Field(
        default=86400,
        description=(
            "Hard ceiling (seconds) on a public datastore signed URL's lifetime. "
            "Requests above this are clamped down. Defaults to 24 hours."
        ),
    )
    datastore_signed_url_default_max_hits: int = Field(
        default=50,
        description=(
            "Default maximum number of times a public datastore signed URL may be "
            "fetched before it is rejected. Bounds egress from link misuse."
        ),
    )
    datastore_signed_url_max_hits: int = Field(
        default=100,
        description=(
            "Hard ceiling on the per-link hit cap for public datastore signed URLs. "
            "Requests above this are clamped down."
        ),
    )
    datastore_signed_url_code_bytes: int = Field(
        default=9,
        description=(
            "Entropy (bytes) for a public datastore signed URL's short code; "
            "secrets.token_urlsafe(9) yields a 12-character code."
        ),
    )


    def effective_document_processor(self) -> str:
        """Resolve ``document_processor`` to a concrete adapter name.

        'auto' uses Kreuzberg when a Kreuzberg URL is configured, otherwise the
        in-process 'markitdown' adapter — so a stack that drops the Kreuzberg
        container (KREUZBERG_URL="") still converts documents in-process.

        Docling is intentionally NOT auto-selected: it is GPU-oriented (slow on
        CPU) so it is opt-in only via an explicit ``DOCUMENT_PROCESSOR=docling``
        (plus ``DOCLING_SERVE_URL``).
        """
        if self.document_processor != "auto":
            return self.document_processor
        return "kreuzberg" if (self.kreuzberg_url or "").strip() else "markitdown"


datastore_settings = DatastoreSettings()
