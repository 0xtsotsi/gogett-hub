"""Unit tests for :mod:`app.core.crypto.keys` resolution + fail-closed gates.

Pins the post-BP-001 contract: the deterministic local seed is no longer
reachable from the runtime path without an explicit opt-in, and the
opt-in is gated to local/testing environments.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.crypto.keys import (
    ALLOW_LOCAL_FALLBACK_ENV,
    LOCAL_KEY_SEED,
    _local_fallback_allowed,
    _single_primary_secret,
    derive_kid,
    legacy_candidate_secrets,
    load_static_keyring,
    local_fallback_secret,
)

pytestmark = pytest.mark.unit


# ----- back-compat: the constant still exists for tests + legacy v1 fixtures.


def test_local_key_seed_is_unchanged():
    # Pinning the seed value so a refactor can't accidentally widen the blast
    # radius (e.g. by making it a different constant per release). The whole
    # point of option (b) is that this stays constant AND unreachable without
    # the opt-in.
    assert LOCAL_KEY_SEED == b"lemma-local-connector-secret-key"


def test_local_fallback_secret_is_deterministic():
    # Existing v1 blobs depend on this determinism — the rotation walker
    # reads them with the same bytes the writer used.
    assert local_fallback_secret() == local_fallback_secret()
    assert local_fallback_secret() != Fernet.generate_key()


def test_opt_in_env_var_name_is_exported():
    # Public symbol used by docs, install scripts, and tests.
    assert ALLOW_LOCAL_FALLBACK_ENV == "LEMMA_ALLOW_LOCAL_FALLBACK_KEY"


# ----- _local_fallback_allowed: pure env/settings gate.


def test_local_fallback_disallowed_by_default(monkeypatch):
    monkeypatch.delenv(ALLOW_LOCAL_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    assert _local_fallback_allowed() is False


@pytest.mark.parametrize("truthy", ["1", "true", "True", "yes", "on"])
def test_local_fallback_allowed_by_env_truthy(monkeypatch, truthy):
    monkeypatch.setenv(ALLOW_LOCAL_FALLBACK_ENV, truthy)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    assert _local_fallback_allowed() is True


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off"])
def test_local_fallback_disallowed_by_env_falsy(monkeypatch, falsy):
    monkeypatch.setenv(ALLOW_LOCAL_FALLBACK_ENV, falsy)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    assert _local_fallback_allowed() is False


def test_local_fallback_allowed_by_settings_field(monkeypatch):
    monkeypatch.delenv(ALLOW_LOCAL_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", True)
    assert _local_fallback_allowed() is True


# ----- _single_primary_secret: fail-closed contract.


def test_single_primary_secret_uses_configured_key(monkeypatch):
    explicit = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "secret_encryption_key", explicit)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    assert _single_primary_secret() == explicit.encode()


def test_single_primary_secret_returns_none_in_local_without_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    monkeypatch.delenv(ALLOW_LOCAL_FALLBACK_ENV, raising=False)
    assert _single_primary_secret() is None


def test_single_primary_secret_returns_none_in_testing_without_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "environment", "testing")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    monkeypatch.delenv(ALLOW_LOCAL_FALLBACK_ENV, raising=False)
    assert _single_primary_secret() is None


def test_single_primary_secret_uses_local_seed_when_opted_in(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", True)
    assert _single_primary_secret() == local_fallback_secret()


def test_single_primary_secret_never_uses_local_seed_in_production(monkeypatch):
    # Critical: the opt-in must NOT extend the deterministic seed to hosted
    # environments. Even if an operator sets LEMMA_ALLOW_LOCAL_FALLBACK_KEY=1
    # in production (mistake), local_fallback_secret() must not be returned
    # because settings.is_local_mode() is False there.
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", True)
    assert _single_primary_secret() is None


def test_single_primary_secret_falls_back_to_legacy_env_var(monkeypatch):
    legacy = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", legacy)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    assert _single_primary_secret() == legacy.encode()


# ----- load_static_keyring: raises clearly when no key is configured.


def test_load_static_keyring_raises_in_local_without_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.setattr(settings, "secret_encryption_keyset", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    monkeypatch.delenv(ALLOW_LOCAL_FALLBACK_ENV, raising=False)
    with pytest.raises(RuntimeError, match="LEMMA_ALLOW_LOCAL_FALLBACK_KEY"):
        load_static_keyring()


def test_load_static_keyring_succeeds_with_opt_in(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.setattr(settings, "secret_encryption_keyset", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", True)
    ring = load_static_keyring()
    assert ring.primary_kid == derive_kid(local_fallback_secret())


def test_load_static_keyring_raises_in_production_without_key(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.setattr(settings, "secret_encryption_keyset", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No secret encryption key"):
        load_static_keyring()


def test_load_static_keyring_raises_in_development_without_key(monkeypatch):
    # Belt-and-suspenders: "development" is also non-local, so no fallback.
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.setattr(settings, "secret_encryption_keyset", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="No secret encryption key"):
        load_static_keyring()


# ----- legacy_candidate_secrets: opt-in gates the v1-decryption fallback too.


def test_legacy_candidates_include_local_seed_only_when_opted_in(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.setattr(settings, "secret_encryption_keyset", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)

    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    monkeypatch.delenv(ALLOW_LOCAL_FALLBACK_ENV, raising=False)
    assert local_fallback_secret() not in legacy_candidate_secrets()

    monkeypatch.setattr(settings, "allow_local_fallback_key", True)
    assert local_fallback_secret() in legacy_candidate_secrets()


def test_legacy_candidates_include_configured_keys(monkeypatch):
    explicit = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "secret_encryption_key", explicit)
    monkeypatch.setattr(settings, "secret_encryption_keyset", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    cands = legacy_candidate_secrets()
    assert explicit.encode() in cands


def test_legacy_candidates_dedupe(monkeypatch):
    explicit = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "secret_encryption_key", explicit)
    monkeypatch.setattr(settings, "secret_encryption_keyset", None)
    monkeypatch.setenv("CONNECTOR_ENCRYPTION_KEY", explicit)
    monkeypatch.setattr(settings, "allow_local_fallback_key", False)
    cands = legacy_candidate_secrets()
    assert cands.count(explicit.encode()) == 1


def test_legacy_candidates_never_contain_local_seed_in_production(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.setattr(settings, "secret_encryption_keyset", None)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    monkeypatch.setattr(settings, "allow_local_fallback_key", True)
    assert local_fallback_secret() not in legacy_candidate_secrets()


# ----- keyset path stays unaffected by the opt-in gate.


def test_keyset_bypasses_opt_in_gate(monkeypatch):
    # The keyset path is the secure rotation path — operators who use it should
    # never need the local-seed opt-in, even in production.
    key = Fernet.generate_key().decode()
    import json as _json

    keyset = _json.dumps([{"kid": "k1", "key": key, "primary": True}])
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_encryption_key", None)
    monkeypatch.setattr(settings, "secret_encryption_keyset", keyset)
    monkeypatch.delenv("CONNECTOR_ENCRYPTION_KEY", raising=False)
    ring = load_static_keyring()
    assert ring.primary_kid == "k1"
    assert ring.primary.secret == key.encode()
