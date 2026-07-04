from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.modules.datastore.infrastructure import document_processor as dp_module
from app.modules.datastore.infrastructure.document_processor import (
    KreuzbergDocumentProcessor,
    create_document_processor,
)
from app.modules.datastore.infrastructure.markitdown_processor import (
    MarkItDownDocumentProcessor,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"


def _pretend_markitdown_installed(monkeypatch):
    """Make the adapter's importability check pass so the FACTORY ROUTING can be
    tested without the optional `markitdown` dep installed (CI runs without it).
    The adapter only imports markitdown lazily on extract(), which these tests
    never call."""
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name, *args, **kwargs):
        if name == "markitdown":
            return object()
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr("importlib.util.find_spec", fake_find_spec)


class _FakeResult:
    def __init__(self, text: str):
        self.text_content = text


class _FakeConverter:
    def __init__(self, text: str):
        self.text = text
        self.calls: list[str] = []

    def convert(self, path: str):
        self.calls.append(path)
        return _FakeResult(self.text)


# -- factory selection -----------------------------------------------------


def test_factory_returns_kreuzberg_when_selected(monkeypatch):
    monkeypatch.setattr(dp_module.datastore_settings, "document_processor", "kreuzberg")
    assert isinstance(create_document_processor(), KreuzbergDocumentProcessor)


def test_factory_returns_markitdown_when_selected(monkeypatch):
    _pretend_markitdown_installed(monkeypatch)
    monkeypatch.setattr(dp_module.datastore_settings, "document_processor", "markitdown")
    assert isinstance(create_document_processor(), MarkItDownDocumentProcessor)


def test_factory_auto_follows_kreuzberg_url(monkeypatch):
    _pretend_markitdown_installed(monkeypatch)
    monkeypatch.setattr(dp_module.datastore_settings, "document_processor", "auto")
    monkeypatch.setattr(dp_module.datastore_settings, "kreuzberg_url", "http://kreuzberg:8000")
    assert isinstance(create_document_processor(), KreuzbergDocumentProcessor)
    monkeypatch.setattr(dp_module.datastore_settings, "kreuzberg_url", "")
    assert isinstance(create_document_processor(), MarkItDownDocumentProcessor)


def test_factory_markitdown_missing_dep_raises_clear_error(monkeypatch):
    monkeypatch.setattr(dp_module.datastore_settings, "document_processor", "markitdown")
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    with pytest.raises(ImportError, match="markitdown"):
        create_document_processor()


# -- adapter behaviour (fake converter — no real markitdown needed) --------


@pytest.mark.asyncio
async def test_extract_builds_extraction_and_chunks_from_markdown():
    converter = _FakeConverter("# Title\n\nA body paragraph with content.")
    processor = MarkItDownDocumentProcessor(converter=converter)

    extraction = await processor.extract(b"docx-bytes", "notes.docx", mime_type=None)

    assert extraction.markdown == "# Title\n\nA body paragraph with content."
    assert extraction.extraction_mode == "markitdown"
    assert extraction.images == []
    assert extraction.pages == []  # not a PDF
    assert extraction.chunks and extraction.chunks[0].text
    # markitdown emits no page markers → chunks carry no page span.
    assert extraction.chunks[0].page_start is None
    assert converter.calls  # conversion actually ran


@pytest.mark.asyncio
async def test_extract_populates_pdf_page_count_for_page_renders():
    pdf_bytes = (_FIXTURES / "bert.pdf").read_bytes()
    processor = MarkItDownDocumentProcessor(converter=_FakeConverter("some text"))

    extraction = await processor.extract(
        pdf_bytes, "bert.pdf", mime_type="application/pdf"
    )

    # PDF page summaries come from pypdfium2 even though the markdown has no
    # page markers — this keeps page-image child artifacts addressable.
    assert extraction.page_count > 0
    assert [p.page_number for p in extraction.pages] == list(
        range(1, extraction.page_count + 1)
    )


def test_supports_page_rendering_inherited_from_mixin():
    processor = MarkItDownDocumentProcessor(converter=_FakeConverter(""))
    assert processor.supports_page_rendering("application/pdf", "a.pdf") is True
    assert processor.supports_page_rendering("text/plain", "a.txt") is False


# -- real markitdown integration (skipped if the extra is not installed) ---


@pytest.mark.asyncio
async def test_real_markitdown_converts_html(tmp_path):
    pytest.importorskip("markitdown")
    html = "<html><body><h1>Hello</h1><p>World paragraph.</p></body></html>"
    processor = MarkItDownDocumentProcessor()

    extraction = await processor.extract(
        html.encode("utf-8"), "page.html", mime_type="text/html"
    )

    assert "Hello" in extraction.markdown
    assert "World" in extraction.markdown
    assert extraction.extraction_mode == "markitdown"
