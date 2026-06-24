import re


# Split a slug on underscores, hyphens, and dots.
_SEPARATORS = re.compile(r"[_\-.]+")


def humanize_name(value: str | None) -> str:
    """Render a machine-style name as spaced Title Case for human display.

    ``"abc_def"`` -> ``"Abc Def"``, ``"my-cool-pod"`` -> ``"My Cool Pod"``.

    Only slug-like names (no whitespace) are transformed. A value that already
    contains a space is treated as a human-entered display name and returned
    unchanged, so intentional capitalization like ``"Acme Support AI"`` is
    preserved. Tokens that are already mixed case (``iOS``, ``OpenAI``) are also
    left intact.
    """
    if not value:
        return value or ""

    stripped = value.strip()
    if not stripped or " " in stripped or "\t" in stripped or "\n" in stripped:
        return stripped

    tokens = [token for token in _SEPARATORS.split(stripped) if token]
    if not tokens:
        return stripped

    humanized: list[str] = []
    for token in tokens:
        if token != token.lower() and token != token.upper():
            # Already mixed case — preserve as-is.
            humanized.append(token)
        else:
            humanized.append(token[:1].upper() + token[1:].lower())
    return " ".join(humanized)
