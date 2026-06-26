"""Regression: the app storage phase touches no DB repository.

``AppStoragePhase`` is constructed with only the storage factory — it has no
repository attribute at all — so the asset/archive/bundle/delete storage sagas
are DB-free by construction and can run after the resolving UoW has closed.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.apps.services.app_storage_phase import (
    AppStoragePhase,
    _AppDeletionCleanup,
    _AssetReadInputs,
)


class _RecordingStorage:
    def __init__(self, blobs: dict[str, bytes] | None = None):
        self.blobs: dict[str, bytes] = dict(blobs or {})
        self.deleted: list[str] = []
        self.deleted_prefixes: list[str] = []

    async def read_file(self, key: str) -> bytes:
        if key in self.blobs:
            return self.blobs[key]
        raise FileNotFoundError(key)

    async def write_file(self, key: str, content: bytes) -> None:
        self.blobs[key] = content

    async def delete_file(self, key: str) -> None:
        self.deleted.append(key)

    async def delete_prefix(self, prefix: str) -> None:
        self.deleted_prefixes.append(prefix)


def _phase(storage):
    return AppStoragePhase(lambda app_id: storage)


def test_app_storage_phase_holds_no_repository():
    sp = _phase(_RecordingStorage())
    assert not hasattr(sp, "repository")
    assert not hasattr(sp, "file_repository")


@pytest.mark.asyncio
async def test_read_archive_without_db():
    storage = _RecordingStorage({"releases/v1/archive.zip": b"ZIPBYTES"})
    content = await _phase(storage).read_archive(uuid4(), "releases/v1/archive.zip")
    assert content == b"ZIPBYTES"


@pytest.mark.asyncio
async def test_read_asset_serves_index_without_db():
    storage = _RecordingStorage({"releases/v1/dist/index.html": b"<html>"})
    inputs = _AssetReadInputs(
        app_id=uuid4(),
        pod_id=uuid4(),
        dist_root_path="releases/v1/dist/",
        normalized_asset_path="",
        quoted_etag='"v1"',
    )
    doc = await _phase(storage).read_asset(inputs)
    assert doc.is_entrypoint is True
    assert doc.etag == '"v1"'


@pytest.mark.asyncio
async def test_cleanup_storage_purges_without_db():
    storage = _RecordingStorage()
    cleanup = _AppDeletionCleanup(
        app_id=uuid4(),
        source_archive_path="source/archive.zip",
        releases=(),
    )
    await _phase(storage).cleanup_storage(cleanup)
    assert "source/archive.zip" in storage.deleted
    assert "" in storage.deleted_prefixes
