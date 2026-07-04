"""Rewrite markdown/HTML image references to their sibling child-artifact names.

Extracted figures (and user-provided companion images) are stored as child
artifacts keyed by basename in a document's hidden container. Markdown that
references an image by a path or URL (``![](images/fig1.png)``, ``<img
src="./fig1.png">``) must point at that basename so the children endpoint
resolves it. Shared by the Kreuzberg adapter and the bring-your-own-markdown
path so both normalize references identically.
"""

from __future__ import annotations

import re

_MARKDOWN_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)((?:\s+['\"][^)]*['\"])?\))")
_HTML_IMAGE_RE = re.compile(r"(<img\b[^>]*\bsrc=[\"'])([^\"']+)([\"'])")


def rewrite_image_references(markdown: str, image_names: set[str]) -> str:
    """Rewrite each image ``src`` to its basename when that basename is a known
    child-artifact name; leave unknown references (e.g. external URLs) untouched."""
    if not markdown or not image_names:
        return markdown

    def normalize_src(src: str) -> str:
        stripped = src.strip("<>")
        normalized = stripped.split("?", 1)[0].split("#", 1)[0]
        image_name = normalized.rsplit("/", 1)[-1]
        return image_name if image_name in image_names else src

    markdown = _MARKDOWN_IMAGE_RE.sub(
        lambda m: f"{m.group(1)}{normalize_src(m.group(2))}{m.group(3)}",
        markdown,
    )
    return _HTML_IMAGE_RE.sub(
        lambda m: f"{m.group(1)}{normalize_src(m.group(2))}{m.group(3)}",
        markdown,
    )
