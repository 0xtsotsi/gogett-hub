from __future__ import annotations

from app.modules.datastore.infrastructure.markdown_chunker import chunk_markdown


def test_empty_or_whitespace_yields_no_chunks():
    assert chunk_markdown("") == []
    assert chunk_markdown("   \n\n\t ") == []


def test_short_markdown_is_a_single_chunk_without_markers_has_no_pages():
    chunks = chunk_markdown("# Title\n\nA short paragraph.", max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0].text == "# Title\n\nA short paragraph."
    assert chunks[0].page_start is None
    assert chunks[0].page_end is None


def test_long_text_splits_within_budget_and_overlaps():
    # Numbered tokens let us detect overlap by shared tokens between neighbours.
    tokens = [f"w{i}" for i in range(600)]
    md = " ".join(tokens)  # ~ 600 * ~5 chars
    chunks = chunk_markdown(md, max_chars=200, overlap=40)

    assert len(chunks) > 1
    # Each emitted chunk stays within the character budget.
    assert all(len(c.text) <= 200 for c in chunks)
    # Consecutive chunks overlap (share at least one token).
    for prev, nxt in zip(chunks, chunks[1:]):
        prev_tokens = set(prev.text.split())
        next_tokens = set(nxt.text.split())
        assert prev_tokens & next_tokens


def test_reassembled_chunks_cover_all_content():
    tokens = [f"t{i}" for i in range(300)]
    md = " ".join(tokens)
    chunks = chunk_markdown(md, max_chars=150, overlap=30)
    seen = set()
    for c in chunks:
        seen.update(c.text.split())
    assert seen == set(tokens)


def test_page_markers_are_stripped_from_chunk_text():
    md = "<!-- PAGE 1 -->\n\nAlpha.\n\n<!-- PAGE 2 -->\n\nBeta."
    chunks = chunk_markdown(md, max_chars=1000)
    assert len(chunks) == 1
    assert "<!-- PAGE" not in chunks[0].text
    assert "Alpha." in chunks[0].text and "Beta." in chunks[0].text


def test_single_chunk_spans_all_marked_pages():
    md = "<!-- PAGE 1 -->\n\nAlpha.\n\n<!-- PAGE 2 -->\n\nBeta.\n\n<!-- PAGE 3 -->\n\nGamma."
    chunks = chunk_markdown(md, max_chars=1000)
    assert len(chunks) == 1
    assert chunks[0].page_start == 1
    assert chunks[0].page_end == 3


def test_page_attribution_across_multiple_chunks():
    body_a = " ".join(f"a{i}" for i in range(60))
    body_b = " ".join(f"b{i}" for i in range(60))
    md = f"<!-- PAGE 1 -->\n\n{body_a}\n\n<!-- PAGE 2 -->\n\n{body_b}"
    chunks = chunk_markdown(md, max_chars=120, overlap=20)

    assert len(chunks) > 1
    assert chunks[0].page_start == 1
    assert chunks[-1].page_end == 2
    # A chunk drawn entirely from the page-1 body is attributed to page 1.
    page_ones = [c for c in chunks if "a0" in c.text and "b0" not in c.text]
    assert page_ones and all(c.page_start == 1 for c in page_ones)


def test_content_before_first_marker_is_attributed_to_first_page():
    md = "Frontmatter line.\n\n<!-- PAGE 1 -->\n\nBody."
    chunks = chunk_markdown(md, max_chars=1000)
    assert chunks[0].page_start == 1
