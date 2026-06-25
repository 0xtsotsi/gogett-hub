from __future__ import annotations

import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from PIL import Image, ImageDraw

from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.infrastructure.document_processor import (
    KreuzbergDocumentProcessor,
)
from app.modules.datastore.services.files.renderer import FilePageRenderer


def _make_pdf_bytes(pages: int = 2) -> bytes:
    imgs = []
    for i in range(1, pages + 1):
        img = Image.new("RGB", (800, 1000), "white")
        ImageDraw.Draw(img).text((50, 50), f"PAGE {i}", fill="black")
        imgs.append(img)
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


class _FakeStorage:
    """Minimal storage: an in-memory blob map + a fixed original PDF stream."""

    def __init__(self, pdf_bytes: bytes):
        self._pdf = pdf_bytes
        self.blobs: dict[str, bytes] = {}
        self.uploads = 0

    async def download_file(self, key: str) -> bytes:
        if key in self.blobs:
            return self.blobs[key]
        if key.endswith(".pdf"):
            # The source document key; rendered page keys end in .jpg.
            return self._pdf
        raise FileNotFoundError(key)  # cache miss

    async def upload_file(self, key: str, content: bytes) -> bool:
        self.blobs[key] = content
        self.uploads += 1
        return True


def _renderer(storage, entity):
    reader = SimpleNamespace()

    async def get_file_by_path(pod_id, path, user_id, ctx=None):
        return entity

    reader.get_file_by_path = get_file_by_path
    return FilePageRenderer(storage, reader, KreuzbergDocumentProcessor())


def _pdf_entity():
    return SimpleNamespace(
        pod_id=uuid4(),
        path="/pod/report.pdf",
        name="report.pdf",
        mime_type="application/pdf",
        is_folder=False,
    )


@pytest.mark.asyncio
async def test_renders_then_caches_then_serves_from_cache():
    storage = _FakeStorage(_make_pdf_bytes(2))
    entity = _pdf_entity()
    renderer = _renderer(storage, entity)
    user_id = uuid4()

    # First call: cache miss -> render + store.
    _e, pages = await renderer.render_document_page_images(
        entity.pod_id, entity.path, user_id, page_start=1, page_end=2
    )
    assert [p.page_number for p in pages] == [1, 2]
    assert all(not p.cached for p in pages)
    assert all(p.jpeg_bytes[:2] == b"\xff\xd8" for p in pages)  # JPEG magic
    assert storage.uploads == 2

    # Second call: cache hit -> no new uploads, served as cached.
    _e2, pages2 = await renderer.render_document_page_images(
        entity.pod_id, entity.path, user_id, page_start=1, page_end=2
    )
    assert all(p.cached for p in pages2)
    assert storage.uploads == 2


@pytest.mark.asyncio
async def test_non_pdf_is_rejected_with_helpful_error():
    storage = _FakeStorage(b"")
    entity = SimpleNamespace(
        pod_id=uuid4(),
        path="/pod/notes.docx",
        name="notes.docx",
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        is_folder=False,
    )
    renderer = _renderer(storage, entity)
    with pytest.raises(DatastoreValidationError, match="PDF"):
        await renderer.render_document_page_images(
            entity.pod_id, entity.path, uuid4(), page_start=1
        )


@pytest.mark.asyncio
async def test_out_of_range_pages_return_empty():
    storage = _FakeStorage(_make_pdf_bytes(2))
    entity = _pdf_entity()
    renderer = _renderer(storage, entity)
    _e, pages = await renderer.render_document_page_images(
        entity.pod_id, entity.path, uuid4(), page_start=9, page_end=10
    )
    assert pages == []


@pytest.mark.asyncio
async def test_render_pages_for_entity_does_no_db_resolution():
    """Regression (DB pool exhaustion): rendering from an already-resolved entity
    must NOT call the reader's DB-backed ``get_file_by_path``. The download
    endpoint resolves inside a short UoW and renders after it closes, so a pooled
    connection is never held during the (slow) render + client transfer."""
    storage = _FakeStorage(_make_pdf_bytes(1))
    entity = _pdf_entity()

    reader = SimpleNamespace()

    async def _must_not_resolve(*args, **kwargs):
        raise AssertionError("DB resolution must not happen during rendering")

    reader.get_file_by_path = _must_not_resolve
    renderer = FilePageRenderer(storage, reader, KreuzbergDocumentProcessor())

    pages = await renderer.render_pages_for_entity(entity, page_start=1, page_end=1)
    assert [p.page_number for p in pages] == [1]
    assert pages[0].jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic
