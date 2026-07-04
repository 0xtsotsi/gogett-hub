from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.file_entities import (
    DatastoreFileEntity,
    FileKind,
    FileStatus,
)
from app.modules.datastore.services.files.path_resolver import PathResolver
from app.modules.datastore.services.files.writer import FileWriter


def _entity(*, mime: str = "application/pdf", metadata: dict | None = None):
    return DatastoreFileEntity(
        pod_id=uuid4(),
        owner_user_id=uuid4(),
        kind=FileKind.FILE,
        path="/docs/a.pdf",
        name="a.pdf",
        mime_type=mime,
        search_enabled=True,
        status=FileStatus.COMPLETED,
        metadata=metadata,
    )


def _writer(entity: DatastoreFileEntity) -> FileWriter:
    reader = AsyncMock()
    reader.get_file_by_path = AsyncMock(return_value=entity)
    authorizer = AsyncMock()
    file_repository = AsyncMock()
    file_repository.update = AsyncMock(side_effect=lambda e: e)
    return FileWriter(
        file_repository,
        AsyncMock(),  # storage
        lambda: None,  # search_factory_provider
        SimpleNamespace(),  # system_skill_files (unused here)
        authorizer,
        PathResolver(),
        AsyncMock(),  # projection
        AsyncMock(),  # lookup
        reader,
    )


def _source_md_key(entity: DatastoreFileEntity) -> str:
    return f"pods/{entity.pod_id}/files/docs/.a.pdf/source.md"


@pytest.mark.asyncio
async def test_attach_user_markdown_stores_source_and_requeues():
    entity = _entity()
    writer = _writer(entity)

    result = await writer.attach_user_markdown(
        entity.pod_id, "/docs/a.pdf", b"# Hand written", uuid4()
    )

    writer.storage.upload_file.assert_awaited_once_with(
        _source_md_key(entity), b"# Hand written"
    )
    assert entity.metadata["markdown_source"] == "user"
    assert entity.status == FileStatus.PENDING  # re-queued for processing
    writer.file_repository.update.assert_awaited_once()
    assert result is entity


@pytest.mark.asyncio
async def test_attach_user_markdown_stores_companion_images():
    entity = _entity()
    writer = _writer(entity)

    await writer.attach_user_markdown(
        entity.pod_id,
        "/docs/a.pdf",
        b"# Doc\n\n![](fig1.png)",
        uuid4(),
        images=[("fig1.png", b"PNGDATA"), ("assets/fig2.png", b"PNG2")],
    )

    keys = {c.args[0]: c.args[1] for c in writer.storage.upload_file.await_args_list}
    base = f"pods/{entity.pod_id}/files/docs/.a.pdf"
    assert keys[f"{base}/source.md"] == b"# Doc\n\n![](fig1.png)"
    assert keys[f"{base}/fig1.png"] == b"PNGDATA"
    # Path in the upload filename is reduced to a safe basename.
    assert keys[f"{base}/fig2.png"] == b"PNG2"
    assert entity.metadata["markdown_source"] == "user"
    assert entity.metadata["markdown_asset_names"] == ["fig1.png", "fig2.png"]


@pytest.mark.asyncio
async def test_attach_rejects_traversal_image_name():
    entity = _entity()
    writer = _writer(entity)

    with pytest.raises(DatastoreValidationError):
        await writer.attach_user_markdown(
            entity.pod_id, "/docs/a.pdf", b"# Doc", uuid4(), images=[("..", b"x")]
        )


@pytest.mark.asyncio
async def test_attach_rejects_markdown_and_text_documents():
    entity = _entity(mime="text/markdown")
    writer = _writer(entity)

    with pytest.raises(DatastoreValidationError):
        await writer.attach_user_markdown(
            entity.pod_id, "/docs/a.md", b"# already markdown", uuid4()
        )
    writer.storage.upload_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_attach_rejects_empty_markdown():
    entity = _entity()
    writer = _writer(entity)

    with pytest.raises(DatastoreValidationError):
        await writer.attach_user_markdown(entity.pod_id, "/docs/a.pdf", b"   ", uuid4())


@pytest.mark.asyncio
async def test_detach_user_markdown_deletes_source_clears_flag_and_requeues():
    entity = _entity(metadata={"markdown_source": "user", "keep": "me"})
    writer = _writer(entity)

    result = await writer.detach_user_markdown(entity.pod_id, "/docs/a.pdf", uuid4())

    writer.storage.delete_file.assert_awaited_once_with(_source_md_key(entity))
    assert "markdown_source" not in (entity.metadata or {})
    assert entity.metadata["keep"] == "me"  # other metadata preserved
    assert entity.status == FileStatus.PENDING
    writer.file_repository.update.assert_awaited_once()
    assert result is entity


@pytest.mark.asyncio
async def test_detach_is_noop_when_no_user_markdown_attached():
    entity = _entity(metadata={"other": "value"})
    writer = _writer(entity)

    result = await writer.detach_user_markdown(entity.pod_id, "/docs/a.pdf", uuid4())

    # Still attempts to remove any stray source.md, but does not re-queue.
    assert entity.status == FileStatus.COMPLETED
    writer.file_repository.update.assert_not_awaited()
    assert result is entity
