"""Bundle staging — where an uploaded archive lives between plan and apply.

A simple local-filesystem implementation keyed by import id: extract the archive
once on create, read it again on apply/resume. This is the storage seam — a
production deployment swaps it for blob storage so any instance can resume — but
the interface (stage / path_for) stays the same.
"""

from __future__ import annotations

import io
import re
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
        # Chunked files are glued back together here, at the storage seam, so
        # every ingestion path — upload, from-github, shared link — stages
        # whole files, not just the one that knows about GitHub publishing.
        if _is_zip(archive, filename or ""):
            archive = reassemble_chunked_entries(archive)
        _extract(archive, filename or "", dest)
        return self._bundle_root(dest)

    def path_for(self, import_id: UUID) -> Path | None:
        dest = self.root / str(import_id)
        return self._bundle_root(dest) if dest.is_dir() else None

    def _bundle_root(self, extracted: Path) -> Path:
        """The directory containing pod.json — the extraction root, or the
        shallowest descendant that has one. An export archive wraps everything
        in one folder, and a GitHub codeload zip adds its own wrapper on top of
        that, so two levels of nesting is normal for a repo-published bundle;
        this isn't limited to one level down."""
        if (extracted / "pod.json").is_file():
            return extracted
        matches = sorted(
            extracted.rglob("pod.json"), key=lambda p: len(p.relative_to(extracted).parts)
        )
        return matches[0].parent if matches else extracted


def _is_within(base: Path, target: Path) -> bool:
    try:
        target.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _is_zip(archive: bytes, filename: str) -> bool:
    lowered = filename.lower()
    return lowered.endswith(".zip") or (not lowered and _looks_zip(archive))


def _extract(archive: bytes, filename: str, dest: Path) -> None:
    """Extract a .zip or .tar(.gz) archive, guarding against path traversal."""
    lowered = filename.lower()
    if _is_zip(archive, filename):
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


# Format written by the publish side's _push_one_file_best_effort
# (github_controller.py) when a file exceeds Composio's request-size ceiling.
_CHUNK_SUFFIX_RE = re.compile(r"^(?P<base>.+)\.chunk(?P<index>\d{4})of(?P<total>\d{4})$")


def reassemble_chunked_entries(archive: bytes) -> bytes:
    """A repo published by Lemma may have large files split into
    ``<path>.chunkNNNNofMMMM`` pieces (see _push_one_file_best_effort in
    github_controller.py) — glue each complete set back into its original file
    before staging. An incomplete set (a stale leftover from a chunk size that
    got shrunk mid-publish) is dropped rather than reassembled wrong; missing
    one non-essential file degrades the same way a skipped one does on
    publish. A chunkless archive passes through byte-identical."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if not any(_CHUNK_SUFFIX_RE.match(info.filename) for info in infos):
            return archive
        chunk_groups: dict[tuple[str, int], dict[int, bytes]] = {}
        passthrough: dict[str, bytes] = {}
        for info in infos:
            match = _CHUNK_SUFFIX_RE.match(info.filename)
            if match:
                key = (match.group("base"), int(match.group("total")))
                chunk_groups.setdefault(key, {})[int(match.group("index"))] = zf.read(info)
            else:
                passthrough[info.filename] = zf.read(info)

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in passthrough.items():
            zf.writestr(name, content)
        for (base, total), parts in chunk_groups.items():
            if len(parts) == total:
                zf.writestr(base, b"".join(parts[i] for i in range(total)))
    return out.getvalue()


def pod_manifest_bytes(archive: bytes, filename: str | None = None) -> bytes | None:
    """The raw bytes of the shallowest ``pod.json`` in an archive, or ``None``
    if there isn't one — a single in-memory parse of the archive that answers
    both "is this a bundle at all?" (``None``) and "what should the new pod be
    called?" (parse the bytes), where a ``{}`` answer used to be ambiguous
    between the two."""
    return _read_archive_member(archive, filename or "", "pod.json")


def _read_archive_member(archive: bytes, filename: str, basename: str) -> bytes | None:
    """Return the bytes of the shallowest archive entry named ``basename``."""
    lowered = filename.lower()
    if _is_zip(archive, filename):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            names = [n for n in zf.namelist() if n.rsplit("/", 1)[-1] == basename]
            if not names:
                return None
            return zf.read(min(names, key=lambda n: n.count("/")))
    mode = "r:gz" if lowered.endswith((".tar.gz", ".tgz")) else "r:*"
    with tarfile.open(fileobj=io.BytesIO(archive), mode=mode) as tf:
        cand = [m for m in tf.getmembers() if m.isfile() and m.name.rsplit("/", 1)[-1] == basename]
        if not cand:
            return None
        member = min(cand, key=lambda m: m.name.count("/"))
        extracted = tf.extractfile(member)
        return extracted.read() if extracted else None
