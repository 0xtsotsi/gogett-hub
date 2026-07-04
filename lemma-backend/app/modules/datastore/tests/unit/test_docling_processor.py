from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.modules.datastore.infrastructure.docling_processor import (
    _PAGE_BREAK_SENTINEL,
    DoclingDocumentProcessor,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"


def test_number_page_markers_rewrites_sentinels():
    raw = f"page one body{_PAGE_BREAK_SENTINEL}page two body{_PAGE_BREAK_SENTINEL}page three"
    out = DoclingDocumentProcessor._number_page_markers(raw)
    assert "<!-- PAGE 1 -->" in out
    assert "<!-- PAGE 2 -->" in out
    assert "<!-- PAGE 3 -->" in out
    assert _PAGE_BREAK_SENTINEL not in out
    assert out.index("<!-- PAGE 1 -->") < out.index("<!-- PAGE 2 -->")


def test_markdown_from_response_reads_document_md_content():
    data = {"document": {"md_content": "# Hello\n\nBody"}, "status": "success"}
    assert DoclingDocumentProcessor._markdown_from_response(data) == "# Hello\n\nBody"


def test_markdown_from_response_rejects_unexpected_shape():
    with pytest.raises(RuntimeError, match="Unexpected response"):
        DoclingDocumentProcessor._markdown_from_response({"nope": 1})


def test_extract_requires_configured_url():
    processor = DoclingDocumentProcessor(base_url="")
    with pytest.raises(ValueError, match="Docling serve URL not configured"):
        import asyncio

        asyncio.run(processor.extract(b"x", "a.pdf", mime_type="application/pdf"))


@pytest.mark.asyncio
async def test_extract_builds_paged_extraction_from_docling_markdown(monkeypatch):
    processor = DoclingDocumentProcessor(base_url="http://docling:5001")
    raw = f"# Intro\n\nfirst page{_PAGE_BREAK_SENTINEL}## Methods\n\nsecond page"
    monkeypatch.setattr(processor, "_convert", AsyncMock(return_value=raw))

    pdf_bytes = (_FIXTURES / "bert.pdf").read_bytes()
    extraction = await processor.extract(pdf_bytes, "bert.pdf", mime_type="application/pdf")

    assert extraction.extraction_mode == "docling"
    assert "<!-- PAGE 1 -->" in extraction.markdown
    assert "<!-- PAGE 2 -->" in extraction.markdown
    assert extraction.chunks
    # Chunk page spans come from the reconstructed markers.
    assert extraction.chunks[0].page_start == 1
    # PDF page count populated via pypdfium2 (independent of docling markers).
    assert extraction.page_count > 0


def test_supports_page_rendering_inherited():
    processor = DoclingDocumentProcessor(base_url="http://docling:5001")
    assert processor.supports_page_rendering("application/pdf", "a.pdf") is True
    assert processor.supports_page_rendering("text/plain", "a.txt") is False
