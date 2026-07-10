from __future__ import annotations

import base64
import hashlib

from app.modules.identity.services.desktop_auth_handoff import challenge_for_verifier


def test_challenge_for_verifier_is_unpadded_sha256_base64url():
    verifier = "desktop-verifier-with-enough-entropy-0123456789"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("utf-8")).digest())
        .decode("ascii")
        .rstrip("=")
    )

    assert challenge_for_verifier(verifier) == expected
    assert "=" not in expected
