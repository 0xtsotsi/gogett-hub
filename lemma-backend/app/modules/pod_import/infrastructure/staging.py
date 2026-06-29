"""Bundle staging — where an uploaded archive lives between plan and apply.

A simple local-filesystem implementation keyed by import id: extract the archive
once on create, read it again on apply/resume. This is the storage seam — a
production deployment swaps it for blob storage so any instance can resume — but
the interface (stage / path_for) stays the same.
"""

from __future__ import annotations

import io
import tarfile
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

_DEFAULT_ROOT = Path(tempfile.gettempdir()) / "lemma-pod-imports"


class BundleStaging:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _DEFAULT_ROOT

    def stage(self, import_id: UUID, archive: bytes, filename: str | None) -> Path:
        """Extract an uploaded archive into this import's staging dir and return
        the bundle root (the directory holding pod.json)."""
        dest = self.root / str(import_id)
        dest.mkdir(parents=True, exist_ok=True)
        _extract(archive, filename or "", dest)
        return self._bundle_root(dest)

    def path_for(self, import_id: UUID) -> Path | None:
        dest = self.root / str(import_id)
        return self._bundle_root(dest) if dest.is_dir() else None

    def _bundle_root(self, extracted: Path) -> Path:
        """The directory containing pod.json — either the extraction root or the
        single top-level folder an export archive wraps everything in."""
        if (extracted / "pod.json").is_file():
            return extracted
        for child in sorted(extracted.iterdir()):
            if child.is_dir() and (child / "pod.json").is_file():
                return child
        return extracted


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _extract(archive: bytes, filename: str, dest: Path) -> None:
    """Extract a .zip or .tar(.gz) archive, guarding against path traversal."""
    lowered = filename.lower()
    if lowered.endswith(".zip") or (not lowered and _looks_zip(archive)):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            for member in zf.namelist():
                out = dest / member
                if not _is_within(dest, out):
                    raise ValueError(f"Unsafe path in archive: {member}")
            zf.extractall(dest)
        return
    mode = "r:gz" if lowered.endswith((".tar.gz", ".tgz")) else "r:*"
    with tarfile.open(fileobj=io.BytesIO(archive), mode=mode) as tf:
        for member in tf.getmembers():
            if not _is_within(dest, dest / member.name):
                raise ValueError(f"Unsafe path in archive: {member.name}")
        tf.extractall(dest)


def _looks_zip(archive: bytes) -> bool:
    return archive[:2] == b"PK"
