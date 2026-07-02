import re


def slugify(text: str) -> str:
    return text.lower().replace(" ", "-")


def normalize_resource_name(name: str) -> str:
    """Normalize a resource name to lowercase with underscores instead of spaces."""
    return name.strip().lower().replace(" ", "_")


def normalize_public_slug(value: str) -> str:
    """Normalize a public DNS-safe slug."""
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    return normalized


def sanitize_ascii_slug(value: str, *, fallback: str, max_length: int = 100) -> str:
    """Sanitize a name into ``[A-Za-z0-9._-]`` — safe as a GitHub repo name or a
    download filename (both cap at/above 100; GitHub's repo limit is exactly that)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return (slug or fallback)[:max_length]
