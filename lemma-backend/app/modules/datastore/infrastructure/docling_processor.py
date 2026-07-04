"""``DocumentProcessorPort`` adapter backed by a Docling Serve HTTP service.

Docling (MIT, LF AI & Data) produces high-fidelity structured markdown — reading
order, heading hierarchy, TableFormer tables — for born-digital research papers
and books. It is ML-heavy (torch + models), so like Kreuzberg it runs as its OWN
container (``quay.io/docling-project/docling-serve``) and the backend talks to it
over HTTP. This keeps lemma-backend lean: NO torch, NO markitdown-style in-process
model deps — only an aiohttp call.

Fast digital-first config (matches the project default): ``do_ocr=false`` (no
OCR spike), ``table_mode=fast``. Page markers are reconstructed from Docling's
page-break placeholder so page-scoped reads + per-chunk page spans keep working.
Chunking is done in-process by ``markdown_chunker``; page images by the shared
pypdfium2 mixin.
"""

from __future__ import annotations

import asyncio
from io import BytesIO
import os
import tempfile

import aiohttp
import anyio

from app.core.log.log import get_logger
from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.document_processing import (
    DocumentExtraction,
    DocumentPage,
)
from app.modules.datastore.infrastructure.markdown_chunker import chunk_markdown
from app.modules.datastore.infrastructure.pdf_page_rendering import (
    PdfPageRenderingMixin,
)
from app.modules.datastore.infrastructure.pdf_renderer import get_pdf_page_count

logger = get_logger(__name__)

_PDF_MIME = "application/pdf"
# Sentinel we ask Docling to insert between pages, then rewrite to numbered
# ``<!-- PAGE n -->`` markers (the same format the reader/chunker expect).
_PAGE_BREAK_SENTINEL = "<!-- DOCLING_PAGE_BREAK -->"
_TRANSIENT_RETRY_ATTEMPTS = 5
_TRANSIENT_RETRY_BASE_DELAY_SECONDS = 0.5


class DoclingDocumentProcessor(PdfPageRenderingMixin):
    """Document processor: Docling Serve extraction (HTTP) + pypdfium rendering."""

    def __init__(self, base_url: str | None = None, *, request_timeout: float | None = None):
        url = base_url if base_url is not None else datastore_settings.docling_serve_url
        self.base_url = url.rstrip("/") if url else None
        self._timeout = aiohttp.ClientTimeout(
            total=request_timeout or datastore_settings.docling_request_timeout_seconds
        )

    async def extract(
        self,
        content: bytes,
        filename: str,
        *,
        mime_type: str | None = None,
    ) -> DocumentExtraction:
        if not self.base_url:
            raise ValueError("Docling serve URL not configured")
        raw_markdown = await self._convert(content, filename, mime_type)
        markdown = self._number_page_markers(raw_markdown)
        chunks = chunk_markdown(markdown) if markdown.strip() else []
        pages = await anyio.to_thread.run_sync(
            self._pdf_pages, content, mime_type, filename
        )
        return DocumentExtraction(
            markdown=markdown,
            chunks=chunks,
            images=[],
            pages=pages,
            detected_languages=[],
            extraction_mode="docling",
        )

    # -- HTTP --------------------------------------------------------------

    async def _convert(self, content: bytes, filename: str, mime_type: str | None) -> str:
        max_attempts = (
            datastore_settings.kreuzberg_transient_retry_attempts
            or _TRANSIENT_RETRY_ATTEMPTS
        )
        base_delay = (
            datastore_settings.kreuzberg_transient_retry_base_delay_seconds
            or _TRANSIENT_RETRY_BASE_DELAY_SECONDS
        )
        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            for attempt in range(max_attempts):
                form = self._build_form(content, filename, mime_type)
                try:
                    async with session.post(
                        f"{self.base_url}/v1/convert/file", data=form
                    ) as response:
                        await self._raise_for_status(response)
                        data = await response.json()
                        return self._markdown_from_response(data)
                except (aiohttp.ClientConnectionError, asyncio.TimeoutError, TimeoutError) as exc:
                    if attempt < max_attempts - 1:
                        delay = base_delay * (2**attempt)
                        logger.warning(
                            "Docling convert connection failed for %s (attempt %d/%d); "
                            "retrying in %.1fs",
                            filename,
                            attempt + 1,
                            max_attempts,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise RuntimeError("Docling convert request failed") from exc
                except aiohttp.ClientError as exc:
                    raise RuntimeError("Docling convert request failed") from exc
        raise RuntimeError("Docling convert request failed")

    def _build_form(
        self, content: bytes, filename: str, mime_type: str | None
    ) -> aiohttp.FormData:
        form = aiohttp.FormData()
        form.add_field(
            "files",
            BytesIO(content),
            filename=filename,
            content_type=mime_type or "application/pdf",
        )
        form.add_field("to_formats", "md")
        # Fast digital-first path: no OCR spike, fast table structure.
        form.add_field("do_ocr", "false")
        form.add_field("force_ocr", "false")
        form.add_field("do_table_structure", "true")
        form.add_field("table_mode", "fast")
        form.add_field("image_export_mode", "placeholder")
        form.add_field("md_page_break_placeholder", _PAGE_BREAK_SENTINEL)
        return form

    @staticmethod
    def _markdown_from_response(data: object) -> str:
        # Single-file sync response: {"document": {"md_content": ...}, ...}.
        if isinstance(data, dict):
            document = data.get("document")
            if isinstance(document, dict):
                return document.get("md_content") or ""
            # Some deployments return a list of per-file results.
            if isinstance(data.get("documents"), list) and data["documents"]:
                first = data["documents"][0]
                if isinstance(first, dict):
                    inner = first.get("document") or first
                    if isinstance(inner, dict):
                        return inner.get("md_content") or ""
        raise RuntimeError("Unexpected response from Docling convert endpoint")

    async def _raise_for_status(self, response: aiohttp.ClientResponse) -> None:
        if response.status < 400:
            return
        body = await response.text()
        raise RuntimeError(
            f"Docling request failed with status {response.status}: {body}"
        )

    # -- normalization -----------------------------------------------------

    @staticmethod
    def _number_page_markers(markdown: str) -> str:
        """Rewrite Docling's between-page sentinels into numbered
        ``<!-- PAGE n -->`` markers (the reader/chunker page format), prefixing
        the first page so all content is paged."""
        if not markdown or not markdown.strip():
            return markdown or ""
        pages = markdown.split(_PAGE_BREAK_SENTINEL)
        pieces: list[str] = []
        for index, page in enumerate(pages, start=1):
            pieces.append(f"\n\n<!-- PAGE {index} -->\n\n")
            pieces.append(page.strip("\n"))
        return "".join(pieces).strip()

    def _is_pdf(self, mime_type: str | None, filename: str) -> bool:
        base = (mime_type or "").split(";")[0].strip().lower()
        return base == _PDF_MIME or filename.lower().endswith(".pdf")

    def _pdf_pages(
        self, content: bytes, mime_type: str | None, filename: str
    ) -> list[DocumentPage]:
        if not self._is_pdf(mime_type, filename):
            return []
        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        try:
            tmp.write(content)
            tmp.flush()
            tmp.close()
            count = get_pdf_page_count(tmp.name)
        except Exception:
            logger.debug("docling: PDF page-count probe failed", exc_info=True)
            return []
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        return [DocumentPage(page_number=number) for number in range(1, count + 1)]
