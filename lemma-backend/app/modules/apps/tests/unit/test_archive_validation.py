from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from app.modules.apps.domain.errors import AppValidationError
from app.modules.apps.services.archive_validation import inspect_app_archive


def _archive(entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path, value in entries.items():
            archive.writestr(path, value)
    return output.getvalue()


def test_archive_inspection_accepts_bounded_regular_zip() -> None:
    result = inspect_app_archive(
        _archive({"index.html": b"ok", "assets/app.js": b"js"}),
        label="Dist archive",
    )
    assert result.entry_count == 2
    assert result.uncompressed_bytes == 4


@pytest.mark.parametrize("path", ["../secret", "/absolute", "C:/windows", "a\\b"])
def test_archive_inspection_rejects_traversal_and_platform_paths(path: str) -> None:
    with pytest.raises(AppValidationError):
        inspect_app_archive(_archive({path: b"x"}), label="Source archive")


def test_archive_inspection_rejects_symlink() -> None:
    output = BytesIO()
    info = ZipInfo("link")
    info.create_system = 3
    info.external_attr = 0o120777 << 16
    with ZipFile(output, "w") as archive:
        archive.writestr(info, "target")
    with pytest.raises(AppValidationError, match="symbolic link"):
        inspect_app_archive(output.getvalue(), label="Source archive")
