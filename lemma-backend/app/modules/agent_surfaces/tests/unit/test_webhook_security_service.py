from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError
from uuid import uuid4

from app.modules.agent_surfaces.config import surface_settings
from app.core.security import _is_surface_webhook_path
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    SurfacePlatform,
    SurfaceConfig,
)
from app.modules.agent_surfaces.services.webhook_security_service import (
    SurfaceWebhookSecurityService,
    SurfaceWebhookAuthenticationError,
    WhatsAppReplayGuard,
    _extract_whatsapp_replay_fields,
)

pytestmark = pytest.mark.asyncio


async def test_verify_platform_request_skips_checks_when_security_disabled(
    monkeypatch,
):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    service = SurfaceWebhookSecurityService()

    await service.verify_platform_request(
        platform="slack",
        headers={},
        raw_body=b'{"type":"event_callback"}',
    )


async def test_verify_surface_request_uses_surface_telegram_secret(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    service = SurfaceWebhookSecurityService()
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        surface_type=SurfacePlatform.TELEGRAM,
        config=SurfaceConfig(type="TELEGRAM"),
        webhook_secret="surface-secret",
    )

    await service.verify_surface_request(
        surface=surface,
        headers={"x-telegram-bot-api-secret-token": "surface-secret"},
        raw_body=b"{}",
    )


async def test_surface_webhook_auth_exclusion_matches_only_uuid_webhook_paths():
    surface_id = "019e7d94-44b9-75ba-8730-21821b4163f8"

    assert _is_surface_webhook_path(f"/surfaces/{surface_id}/webhook") is True
    assert _is_surface_webhook_path(f"/surfaces/{surface_id}/webhook/extra") is False
    assert _is_surface_webhook_path("/surfaces/not-a-uuid/webhook") is False
    assert _is_surface_webhook_path(f"/pods/{surface_id}/surfaces") is False


# ── Resend (Svix) inbound signature verification ──────────────────────────────

_RESEND_SECRET = "whsec_" + base64.b64encode(b"resend-inbound-secret-key").decode()


def _svix_headers(raw_body: bytes, secret: str, *, timestamp: int | None = None) -> dict[str, str]:
    """Build a valid Svix signature header set for ``raw_body``."""
    svix_id = "msg_2b3c4d"
    ts = str(timestamp if timestamp is not None else int(time.time()))
    key = base64.b64decode(secret[len("whsec_") :])
    signed = b"%b.%b.%b" % (svix_id.encode(), ts.encode(), raw_body)
    sig = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    return {
        "svix-id": svix_id,
        "svix-timestamp": ts,
        "svix-signature": f"v1,{sig}",
    }


async def test_verify_resend_request_accepts_valid_svix_signature(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_signing_secret", _RESEND_SECRET)
    service = SurfaceWebhookSecurityService()
    body = b'{"type":"email.inbound","data":{}}'

    await service.verify_resend_request(headers=_svix_headers(body, _RESEND_SECRET), raw_body=body)


async def test_verify_resend_request_rejects_tampered_body(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_signing_secret", _RESEND_SECRET)
    service = SurfaceWebhookSecurityService()
    headers = _svix_headers(b'{"to":"pod-a@x"}', _RESEND_SECRET)

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await service.verify_resend_request(headers=headers, raw_body=b'{"to":"pod-attacker@x"}')


async def test_verify_resend_request_rejects_missing_headers(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_signing_secret", _RESEND_SECRET)
    service = SurfaceWebhookSecurityService()

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await service.verify_resend_request(headers={}, raw_body=b"{}")


async def test_verify_resend_request_rejects_stale_timestamp(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_signing_secret", _RESEND_SECRET)
    service = SurfaceWebhookSecurityService()
    body = b"{}"
    stale = _svix_headers(body, _RESEND_SECRET, timestamp=int(time.time()) - 3600)

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await service.verify_resend_request(headers=stale, raw_body=body)


async def test_verify_resend_request_raises_when_secret_unconfigured(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "resend_inbound_signing_secret", None)
    service = SurfaceWebhookSecurityService()
    body = b"{}"

    with pytest.raises(SurfaceWebhookAuthenticationError) as exc:
        await service.verify_resend_request(
            headers=_svix_headers(body, _RESEND_SECRET), raw_body=body
        )
    assert exc.value.status_code == 503


async def test_verify_resend_request_skips_when_security_disabled(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    service = SurfaceWebhookSecurityService()

    # No signature headers at all, but disabled security short-circuits.
    await service.verify_resend_request(headers={}, raw_body=b"{}")


# ── Native WhatsApp signature + replay protection ─────────────────────────────

_WHATSAPP_APP_SECRET = "wa-app-secret-test"
_WHATSAPP_MESSAGE_ID = "wamid.HBgLTESTMESSAGEID12345=="
_WHATSAPP_PHONE = "15550001111"


def _whatsapp_message_body(
    *, message_id: str = _WHATSAPP_MESSAGE_ID, ts: int | None = None
) -> bytes:
    """Build a realistic native WhatsApp webhook envelope with ``messages[0]``."""
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": _WHATSAPP_PHONE,
                                    "phone_number_id": "PHONE_ID",
                                },
                                "contacts": [{"profile": {"name": "Alex"}, "wa_id": "15557770000"}],
                                "messages": [
                                    {
                                        "from": "15557770000",
                                        "id": message_id,
                                        "timestamp": str(
                                            ts if ts is not None else int(time.time())
                                        ),
                                        "type": "text",
                                        "text": {"body": "hello!"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode("utf-8")


def _whatsapp_status_body(*, status_id: str = "status-abc", ts: int | None = None) -> bytes:
    """Build a native WhatsApp status (delivery receipt) envelope."""
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": _WHATSAPP_PHONE,
                                    "phone_number_id": "PHONE_ID",
                                },
                                "statuses": [
                                    {
                                        "id": status_id,
                                        "timestamp": str(
                                            ts if ts is not None else int(time.time())
                                        ),
                                        "status": "delivered",
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode("utf-8")


def _whatsapp_headers(raw_body: bytes, app_secret: str) -> dict[str, str]:
    digest = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return {"x-hub-signature-256": f"sha256={digest}"}


class _FakeRedisSet:
    """In-memory Redis ``SET key val NX EX`` for the replay guard tests.

    Tracks each (key, value, ex) call so we can assert the TTL behaviour
    without spinning up a real Redis or adding ``fakeredis`` to the dev deps.
    Returns ``True`` on first claim, ``None`` on collision — matching the
    redis-py async client contract.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.calls: list[tuple[str, str, int | None, bool]] = []

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False, **_):
        self.calls.append((key, value, ex, nx))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


def _stub_replay_guard(monkeypatch, *, redis_error: Exception | None = None) -> _FakeRedisSet:
    """Swap the singleton ``WhatsAppReplayGuard`` with one backed by ``_FakeRedisSet``.

    Pass ``redis_error=...`` to make the underlying ``set`` raise so the
    fail-closed Redis-unavailable branch is exercised.
    """
    fake = _FakeRedisSet()
    fake_redis = AsyncMock()

    if redis_error is None:
        fake_redis.set = fake.set
    else:

        async def _raising_set(*args, **kwargs):
            raise redis_error

        fake_redis.set = _raising_set

    guard = WhatsAppReplayGuard(redis_url="redis://unused", ttl_seconds=600)
    guard._redis = fake_redis  # type: ignore[assignment]

    from app.modules.agent_surfaces.services import webhook_security_service as mod

    monkeypatch.setattr(mod, "_whatsapp_replay_guard", guard, raising=False)
    return fake


async def test_whatsapp_accepts_valid_signature_and_fresh_message(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    fake = _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_message_body()
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    await service.verify_platform_request(platform="whatsapp", headers=headers, raw_body=body)

    assert len(fake.calls) == 1
    key, _, ex, nx = fake.calls[0]
    assert key == f"agent_surfaces:whatsapp_replay:{_WHATSAPP_MESSAGE_ID}"
    assert nx is True
    assert ex == 600


async def test_whatsapp_rejects_tampered_signature(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_message_body()
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await service.verify_platform_request(
            platform="whatsapp",
            headers=headers,
            raw_body=body + b"tampered",
        )


async def test_whatsapp_rejects_missing_signature_header(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_message_body()

    with pytest.raises(SurfaceWebhookAuthenticationError):
        await service.verify_platform_request(platform="whatsapp", headers={}, raw_body=body)


async def test_whatsapp_rejects_stale_timestamp(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    fake = _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_message_body(ts=int(time.time()) - 3600)
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    with pytest.raises(SurfaceWebhookAuthenticationError, match="replay window"):
        await service.verify_platform_request(platform="whatsapp", headers=headers, raw_body=body)
    assert fake.calls == []  # dedup must NOT run for stale events


async def test_whatsapp_rejects_future_timestamp_outside_window(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    fake = _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_message_body(ts=int(time.time()) + 3600)
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    with pytest.raises(SurfaceWebhookAuthenticationError, match="replay window"):
        await service.verify_platform_request(platform="whatsapp", headers=headers, raw_body=body)
    assert fake.calls == []


async def test_whatsapp_rejects_body_without_message_id(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    # ``messages[0]`` has a timestamp but no id — the verifier must reject
    # before reaching the replay guard.
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [
                                    {
                                        "from": "15557770000",
                                        "timestamp": str(int(time.time())),
                                        "type": "text",
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        }
    ).encode("utf-8")
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    with pytest.raises(SurfaceWebhookAuthenticationError, match="missing"):
        await service.verify_platform_request(platform="whatsapp", headers=headers, raw_body=body)


async def test_whatsapp_accepts_status_callback_without_messages(monkeypatch):
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_status_body()
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    await service.verify_platform_request(platform="whatsapp", headers=headers, raw_body=body)


async def test_whatsapp_rejects_second_delivery_with_same_message_id(monkeypatch):
    """Regression: a captured valid body replayed inside the window is rejected.

    Mirrors the BP-005 scenario — an attacker who sniffs one signed envelope
    replays it; the second delivery must be rejected so we don't trigger
    amplified outbound replies / agent runs.
    """
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    fake = _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_message_body()
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    # First delivery: accepted, dedup key claimed.
    await service.verify_platform_request(platform="whatsapp", headers=headers, raw_body=body)
    assert len(fake.calls) == 1

    # Second delivery of the identical captured body: rejected as replay.
    with pytest.raises(SurfaceWebhookAuthenticationError, match="already been processed"):
        await service.verify_platform_request(platform="whatsapp", headers=headers, raw_body=body)

    # The guard should have been asked to claim the key twice; the second
    # claim returns False because the key already exists.
    assert len(fake.calls) == 2
    assert (
        fake.calls[0][0]
        == fake.calls[1][0]
        == (f"agent_surfaces:whatsapp_replay:{_WHATSAPP_MESSAGE_ID}")
    )


async def test_whatsapp_replay_check_fires_on_surface_route_too(monkeypatch):
    """Same replay rejection applies to the surface-level webhook route."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    fake = _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="wa",
        surface_type=SurfacePlatform.WHATSAPP,
        config=SurfaceConfig(type="WHATSAPP"),
        account_id=None,
    )
    body = _whatsapp_message_body(message_id="wamid.SURFACE_ROUTE_TEST==")
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    await service.verify_surface_request(surface=surface, headers=headers, raw_body=body)
    with pytest.raises(SurfaceWebhookAuthenticationError, match="already been processed"):
        await service.verify_surface_request(surface=surface, headers=headers, raw_body=body)
    assert len(fake.calls) == 2


async def test_whatsapp_fails_closed_when_redis_is_unavailable(monkeypatch):
    """Replay guard is a security dependency — Redis errors must reject, not bypass."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", True)
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", _WHATSAPP_APP_SECRET)
    _stub_replay_guard(monkeypatch, redis_error=RedisError("connection refused"))
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_message_body()
    headers = _whatsapp_headers(body, _WHATSAPP_APP_SECRET)

    with pytest.raises(SurfaceWebhookAuthenticationError, match="replay protection"):
        await service.verify_platform_request(platform="whatsapp", headers=headers, raw_body=body)


async def test_whatsapp_skips_replay_check_when_security_disabled(monkeypatch):
    """Local dev with security off bypasses both signature and replay checks."""
    monkeypatch.setattr(surface_settings, "surface_webhook_security_enabled", False)
    fake = _stub_replay_guard(monkeypatch)
    service = SurfaceWebhookSecurityService()
    body = _whatsapp_message_body()

    await service.verify_platform_request(platform="whatsapp", headers={}, raw_body=body)
    assert fake.calls == []


async def test_extract_whatsapp_replay_fields_reads_messages_envelope():
    ts = int(time.time())
    body = _whatsapp_message_body(message_id="wamid.ABC==", ts=ts)
    assert _extract_whatsapp_replay_fields(body) == (ts, "wamid.ABC==")


async def test_extract_whatsapp_replay_fields_falls_back_to_statuses():
    ts = int(time.time())
    body = _whatsapp_status_body(status_id="status-xyz", ts=ts)
    assert _extract_whatsapp_replay_fields(body) == (ts, "status-xyz")


async def test_extract_whatsapp_replay_fields_prefers_messages_over_statuses():
    ts = int(time.time())
    body = json.dumps(
        {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "messages": [{"id": "msg-1", "timestamp": str(ts), "type": "text"}],
                                "statuses": [
                                    {"id": "status-1", "timestamp": "1", "status": "delivered"}
                                ],
                            }
                        }
                    ]
                }
            ]
        }
    ).encode("utf-8")
    assert _extract_whatsapp_replay_fields(body) == (ts, "msg-1")


async def test_extract_whatsapp_replay_fields_returns_none_for_empty_or_invalid_body():
    assert _extract_whatsapp_replay_fields(b"") == (None, None)
    assert _extract_whatsapp_replay_fields(b"not-json") == (None, None)
    assert _extract_whatsapp_replay_fields(b'{"entry":[]}') == (None, None)
    assert _extract_whatsapp_replay_fields(b'{"entry":[{"changes":[]}]}') == (None, None)
