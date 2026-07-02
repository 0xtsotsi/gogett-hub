"""Unit tests for BundleStaging — finding the bundle root inside an extracted
archive (at whatever nesting depth it shows up at), reassembling chunked files
from a published repo, and peeking at pod.json without staging."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from uuid import uuid4

from app.modules.pod_import.infrastructure.staging import (
    BundleStaging,
    pod_manifest_bytes,
    reassemble_chunked_entries,
)


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_bundle_root_is_the_extraction_root_when_pod_json_is_at_the_top(tmp_path: Path):
    archive = _zip_with({"pod.json": b"{}", "tables/widgets/widgets.json": b"{}"})
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "bundle.zip")
    assert (root / "pod.json").is_file()


def test_bundle_root_unwraps_a_single_export_wrapper_folder(tmp_path: Path):
    # What a downloaded/uploaded bundle looks like: one wrapper folder.
    archive = _zip_with({"trumpet/pod.json": b"{}", "trumpet/tables/widgets/widgets.json": b"{}"})
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "bundle.zip")
    assert root.name == "trumpet"
    assert (root / "pod.json").is_file()


def test_bundle_root_unwraps_two_levels_of_nesting(tmp_path: Path):
    # What a GitHub codeload zipball of a published repo looks like: GitHub's
    # own "<repo>-<ref>/" wrapper around the bundle's "<pod_name>/" wrapper.
    # This is exactly the shape that used to make a re-imported GitHub pod
    # come back with an empty plan (bundle_root pointed at the outer folder,
    # which has no pod.json and no tables/agents/etc. directly inside it).
    archive = _zip_with(
        {
            "repo-main/README.md": b"# hi",
            "repo-main/trumpet/pod.json": b"{}",
            "repo-main/trumpet/tables/widgets/widgets.json": b"{}",
        }
    )
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "repo.zip")
    assert (root / "pod.json").is_file()
    assert (root / "tables" / "widgets" / "widgets.json").is_file()


def test_bundle_root_falls_back_to_extraction_root_when_no_pod_json_exists(tmp_path: Path):
    archive = _zip_with({"readme.txt": b"hello"})
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "bundle.zip")
    assert not (root / "pod.json").is_file()


def test_path_for_returns_none_for_an_unstaged_import(tmp_path: Path):
    staging = BundleStaging(root=tmp_path)
    assert staging.path_for(uuid4()) is None


def test_stage_reassembles_chunked_files_from_a_published_repo(tmp_path: Path):
    # A GitHub-published repo splits large files into `.chunkNNNNofMMMM` pieces
    # (Composio's request-size ceiling); stage() glues them back so EVERY
    # ingestion path — upload, from-github, CLI-uploaded zips — stages whole
    # files, not just the endpoint that knows about GitHub publishing.
    archive = _zip_with(
        {
            "repo-main/pod.json": b"{}",
            "repo-main/apps/mini/dist.zip.chunk0000of0002": b"AAA",
            "repo-main/apps/mini/dist.zip.chunk0001of0002": b"BBB",
        }
    )
    staging = BundleStaging(root=tmp_path)
    root = staging.stage(uuid4(), archive, "repo.zip")
    assert (root / "apps" / "mini" / "dist.zip").read_bytes() == b"AAABBB"
    assert not (root / "apps" / "mini" / "dist.zip.chunk0000of0002").exists()


def test_reassemble_drops_an_incomplete_chunk_set():
    # Only 2 of 3 chunks present (e.g. a chunk-size shrink left a stale partial
    # set behind) -- must not silently reassemble corrupt/truncated content.
    archive = _zip_with(
        {
            "dist.zip.chunk0000of0003": b"AAA",
            "dist.zip.chunk0001of0003": b"BBB",
            "pod.json": b"{}",
        }
    )
    with zipfile.ZipFile(io.BytesIO(reassemble_chunked_entries(archive))) as zf:
        assert "dist.zip" not in zf.namelist()
        assert zf.read("pod.json") == b"{}"


def test_reassemble_passes_a_chunkless_archive_through_byte_identical():
    archive = _zip_with({"pod.json": b"{}", "tables/widgets/widgets.json": b"{}"})
    assert reassemble_chunked_entries(archive) == archive


def test_pod_manifest_bytes_returns_the_shallowest_pod_json():
    archive = _zip_with(
        {
            "repo-main/pod.json": b'{"name": "trumpet"}',
            "repo-main/vendored/other/pod.json": b'{"name": "inner"}',
        }
    )
    assert pod_manifest_bytes(archive, "repo.zip") == b'{"name": "trumpet"}'


def test_pod_manifest_bytes_is_none_when_the_archive_has_no_pod_json():
    # None (not b"{}") so a caller can tell "not a bundle at all" from "a
    # bundle whose pod.json happens to be empty" in one archive parse.
    archive = _zip_with({"readme.txt": b"hello"})
    assert pod_manifest_bytes(archive, "bundle.zip") is None
