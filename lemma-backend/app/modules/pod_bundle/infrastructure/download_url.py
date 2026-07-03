"""Signed, authenticated download URLs for staged bundle archives.

An export (or an uploaded bundle) is fetched via a URL that is gated **twice**:
the endpoint requires an authenticated lemma user (any logged-in user, not
pod-scoped) AND a valid signed token. The token is stateless — it embeds the
staged object's ``(kind, id)`` and an expiry, HMAC-signed by the unified
``app/core/crypto`` signer (HKDF off the required ``SECRET_ENCRYPTION_KEY``) —
so there is no Redis dependency and no forgeable dev fallback.

Because the token identifies a staged object, the import worker verifies it and
reads the object **directly from object storage** instead of doing a server-side
HTTP fetch — a lemma-origin bundle never leaves our storage, so ``kind=URL``
imports carry no SSRF surface.
"""

from __future__ import annotations

import base64
import json
import time
from urllib.parse import quote
from uuid import UUID

from app.core.config import settings
from app.core.crypto import get_secret_signer
from app.modules.pod_bundle.domain.errors import BundleJobExpiredError
from app.modules.pod_bundle.infrastructure.staging import StagingKind

#: HKDF subkey label for the unified signer — isolates this token's key material.
_PURPOSE = "pod-bundle-download-url"

#: Relative path of the authenticated download endpoint (see export_controller).
DOWNLOAD_PATH = "/pods/bundle/download"

_VALID_KINDS: tuple[str, ...] = ("pod-imports", "pod-exports")


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def mint_download_token(*, kind: StagingKind, job_id: UUID, ttl_seconds: int) -> str:
    """Sign a token binding a staged object ``(kind, job_id)`` to an expiry.

    Token layout mirrors the datastore file token: ``<payload>.<kid>.<sig>``.
    """
    expires_at = int(time.time()) + int(ttl_seconds)
    payload = json.dumps(
        {"k": kind, "i": str(job_id), "e": expires_at},
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{_b64e(payload)}.{get_secret_signer().sign(_PURPOSE, payload)}"


def verify_download_token(
    token: str, *, now_epoch: int | None = None
) -> tuple[StagingKind, UUID]:
    """Return ``(kind, job_id)`` for a valid token; raise
    :class:`BundleJobExpiredError` (410) on a tampered, malformed, or expired
    token (all indistinguishable to the caller, and all mean "get a fresh URL")."""
    now = now_epoch if now_epoch is not None else int(time.time())
    try:
        payload_b64, signature = token.split(".", 1)
        payload = _b64d(payload_b64)
        if not get_secret_signer().verify(_PURPOSE, payload, signature):
            raise BundleJobExpiredError("This download link is invalid.")
        data = json.loads(payload)
        if int(data["e"]) < now:
            raise BundleJobExpiredError("This download link has expired.")
        kind = str(data["k"])
        if kind not in _VALID_KINDS:
            raise BundleJobExpiredError("This download link is invalid.")
        return kind, UUID(str(data["i"]))  # type: ignore[return-value]
    except BundleJobExpiredError:
        raise
    except Exception as exc:  # malformed b64/json/uuid
        raise BundleJobExpiredError("This download link is invalid.") from exc


def build_download_url(*, kind: StagingKind, job_id: UUID, ttl_seconds: int) -> str:
    """Absolute, signed download URL for a staged archive."""
    token = mint_download_token(kind=kind, job_id=job_id, ttl_seconds=ttl_seconds)
    return f"{settings.api_url.rstrip('/')}{DOWNLOAD_PATH}?token={quote(token)}"
