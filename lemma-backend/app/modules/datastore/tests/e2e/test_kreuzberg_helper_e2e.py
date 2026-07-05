from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.datastore.infrastructure.kreuzberg_helper import KreuzbergHelper


pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_kreuzberg_extracts_markdown_for_pdf(e2e_settings, kreuzberg_wired):
    """Kreuzberg extracts markdown text and chunks from a real arXiv PDF.

    Depends on ``kreuzberg_wired``, which starts the shared session-scoped
    Kreuzberg container and points ``datastore_settings.kreuzberg_url`` at it.
    That fixture (rather than the session-wide ``e2e_settings``) is what pulls
    Kreuzberg in, so it only starts for the tests that actually extract a
    document. An earlier version started its OWN second container and overwrote
    the shared URL without restoring it — once this test's container was torn
    down, every later indexing test in the module pointed at a dead URL and
    failed with ConnectionRefused. Reusing the shared container keeps the
    setting intact for the rest of the suite.
    """
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures" / "arxiv" / "seq2seq.pdf"
    )

    helper = KreuzbergHelper()
    result = await helper.process_file(
        fixture_path.read_bytes(),
        fixture_path.name,
    )

    assert result.content.strip()
    assert "NEURAL MACHINE TRANSLATION" in result.content
    assert result.get_chunks()
