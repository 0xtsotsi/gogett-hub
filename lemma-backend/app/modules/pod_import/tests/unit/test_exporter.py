"""Unit tests for the bundle exporter's service-free seams: the preview's
asset skip (with_assets=False) and the per-export connector-account lookup
memoization. The full export round-trip lives in the e2e suite."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.pod_import.infrastructure.exporter import BundleExporter


class FakeAppService:
    """Serves one app's metadata and counts archive downloads — enough to see
    whether _export_apps pays for assets it was told to skip."""

    def __init__(self):
        self.archive_downloads = 0

    async def list_apps(self, pod_id, user_id, ctx=None):
        return [
            SimpleNamespace(name="mini", public_slug=None, description=None, visibility="POD")
        ], None

    async def resolve_dist_archive(self, pod_id, name, user_id, ctx=None):
        self.archive_downloads += 1
        return uuid4(), "dist.zip"

    async def resolve_source_archive(self, pod_id, name, user_id, ctx=None):
        self.archive_downloads += 1
        return uuid4(), "source.zip"

    async def read_archive(self, app_id, archive_path):
        return b"archive-bytes"


def _patch_app_service(monkeypatch, service):
    from app.modules.apps.api import dependencies as app_dependencies

    monkeypatch.setattr(app_dependencies, "build_app_service", lambda uow: service)


@pytest.mark.asyncio
async def test_export_apps_without_assets_writes_metadata_but_downloads_nothing(
    monkeypatch, tmp_path: Path
):
    service = FakeAppService()
    _patch_app_service(monkeypatch, service)

    await BundleExporter(uow=None)._export_apps(tmp_path, uuid4(), uuid4(), None, False)

    assert (tmp_path / "apps" / "mini" / "mini.json").is_file()
    assert service.archive_downloads == 0
    assert not (tmp_path / "apps" / "mini" / "dist.zip").exists()


@pytest.mark.asyncio
async def test_export_apps_with_assets_downloads_dist_and_source(monkeypatch, tmp_path: Path):
    service = FakeAppService()
    _patch_app_service(monkeypatch, service)

    await BundleExporter(uow=None)._export_apps(tmp_path, uuid4(), uuid4(), None, True)

    assert (tmp_path / "apps" / "mini" / "dist.zip").read_bytes() == b"archive-bytes"
    assert (tmp_path / "apps" / "mini" / "source.zip").read_bytes() == b"archive-bytes"


@pytest.mark.asyncio
async def test_connector_provider_lookup_is_memoized_per_exporter(monkeypatch):
    from app.core import crypto
    from app.modules.connectors.infrastructure.repositories import account_repository

    built: list[object] = []
    fetched: list[object] = []

    class FakeAccountRepo:
        def __init__(self, uow, encryption=None):
            built.append(self)

        async def get(self, account_id):
            fetched.append(account_id)
            return SimpleNamespace(connector_id="github")

    monkeypatch.setattr(account_repository, "AccountRepository", FakeAccountRepo)
    monkeypatch.setattr(crypto, "get_secret_cipher", lambda: None)

    exporter = BundleExporter(uow=None)
    account_id = uuid4()
    # The same account backs every grant that references it, and one export
    # replays many grants — one repository, one query per distinct account.
    assert await exporter._connector_provider_for_account(str(account_id)) == "github"
    assert await exporter._connector_provider_for_account(str(account_id)) == "github"
    assert len(built) == 1
    assert fetched == [account_id]


@pytest.mark.asyncio
async def test_connector_provider_is_none_for_a_non_uuid_account_name():
    # A grant keyed by something that isn't an account UUID can't be re-pointed
    # on import — the exporter drops it rather than guessing.
    assert await BundleExporter(uow=None)._connector_provider_for_account("not-a-uuid") is None
