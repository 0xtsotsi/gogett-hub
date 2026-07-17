"""Webhook authenticity verification for native agent surfaces.

Each supported platform has its own scheme (HMAC-SHA256 over the raw body, a
header-bound secret, an Svix envelope, a Bot Framework JWT, etc.), but they all
share three properties:

* the raw request body is the source of truth for signature checks (so the
  controller must hand us ``bytes`` it received, not a re-decoded JSON copy),
* successful verification rejects the request before any downstream handler
  runs, and
* a failure surfaces as ``SurfaceWebhookAuthenticationError`` (a
  ``DomainError``) so the global handler maps it to the right HTTP status.

Replay protection
-----------------

Slack (``v0:ts:body`` HMAC) and Resend (Svix ``id.ts.body`` HMAC) both bind the
timestamp into the signature base string and reject requests outside their skew
window — so an attacker who captures one signed request can only replay it
inside the 5-minute window.

Teams verifies a short-lived JWT signed by the Bot Framework signing keys; the
JWT's ``exp`` plays the same role.

The native WhatsApp webhook (``HMAC-SHA256(body, app_secret)``) historically
had neither, so any captured body could be replayed indefinitely. We now add
the same protections on top of the existing signature check:

1. Parse ``entry[0].changes[0].value.messages[*].timestamp`` (or
   ``statuses[*].timestamp`` for status callbacks) and reject events older than
   ``_WHATSAPP_MAX_TIMESTAMP_SKEW_SECONDS`` (300s, matching Slack's window).
2. After signature + freshness, atomically claim ``messages[*].id`` (or
   ``statuses[*].id``) in Redis with ``SET … NX EX 2*skew`` — a second delivery
   of the same envelope inside the window is rejected.
3. If Redis is unavailable, fail closed (reject the request). The dedup cache
   is a security control, not a convenience, so unavailability cannot be
   silently ignored.

Both the platform-level route (``/webhooks/whatsapp``) and the surface-level
route (``/{surface_id}/webhook`` for ``platform=whatsapp``) call into
``_verify_whatsapp_signature`` via ``verify_platform_request`` /
``verify_surface_request``, so the replay check fires on both routes
automatically.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
from typing import TYPE_CHECKING, Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import settings
from app.core.domain.errors import DomainError
from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.core.log.log import get_logger
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.entities import AgentSurfaceEntity, SurfacePlatform

if TYPE_CHECKING:
    from app.modules.agent_surfaces.services.credential_resolver import (
        SurfaceCredentialResolver,
    )

logger = get_logger(__name__)
_SLACK_SIGNATURE_VERSION = "v0"
_SLACK_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5
# Resend delivers inbound webhooks via Svix; the signature is a base64 HMAC-SHA256
# over ``{svix-id}.{svix-timestamp}.{body}`` keyed by the base64 secret (minus its
# ``whsec_`` prefix). The signature header carries space-separated ``v1,<sig>``
# entries so the secret can be rotated.
_SVIX_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5
# Native WhatsApp webhook signature is HMAC-SHA256(body, app_secret); Meta
# does not bind the timestamp into the base string. We add the same replay
# window as Slack on top of the signature check so a captured body cannot be
# replayed indefinitely. See module docstring.
_WHATSAPP_MAX_TIMESTAMP_SKEW_SECONDS = 60 * 5
# Redis dedup TTL is set to 2x the skew window so an entry cannot expire while
# a freshly-cloned envelope is still inside the freshness window.
_WHATSAPP_REPLAY_DEDUP_PREFIX = "agent_surfaces:whatsapp_replay"
_BOT_FRAMEWORK_OPENID_CONFIG_URL = (
    "https://login.botframework.com/v1/.well-known/openidconfiguration"
)
_BOT_FRAMEWORK_ALLOWED_ISSUERS = frozenset(
    {
        "https://api.botframework.com",
        "https://api.botframework.com/",
    }
)
_OIDC_CACHE_TTL_SECONDS = 60 * 10

# Shared Redis cache of OIDC/JWKS documents used to verify Teams webhook JWTs, so
# the metadata is fetched once across replicas. Redis unavailable -> refetch.
_oidc_cache: RedisJsonCache | None = None


def _get_oidc_cache() -> RedisJsonCache:
    global _oidc_cache
    if _oidc_cache is None or _oidc_cache._redis_url != settings.redis_url:
        _oidc_cache = RedisJsonCache(
            redis_url=settings.redis_url,
            key_prefix="surface:oidc",
            ttl_seconds=_OIDC_CACHE_TTL_SECONDS,
        )
    return _oidc_cache


class WhatsAppReplayGuard:
    """Redis-backed dedup for captured WhatsApp webhook envelopes.

    The native WhatsApp signature scheme does not bind a timestamp into the
    HMAC base string, so a single captured body could otherwise be replayed
    indefinitely — amplified outbound replies and amplified agent runs.

    We atomically claim ``messages[*].id`` (or ``statuses[*].id``) with
    ``SET … NX EX`` so that the second delivery of the same envelope inside
    the freshness window is rejected. The TTL is intentionally longer than
    the skew window so the dedup state outlives the freshness check.

    Redis is treated as a security dependency: any connection / timeout /
    protocol error from ``SET`` is surfaced as an authentication failure
    (fail-closed). Operators cannot opt to fall open.
    """

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        ttl_seconds: int = _WHATSAPP_MAX_TIMESTAMP_SKEW_SECONDS * 2,
    ) -> None:
        self._redis_url = redis_url or settings.redis_url
        self._ttl_seconds = ttl_seconds
        self._redis: Redis | None = None
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> Redis:
        if self._redis is not None:
            return self._redis
        async with self._lock:
            if self._redis is None:
                self._redis = Redis.from_url(
                    self._redis_url,
                    decode_responses=True,
                )
        return self._redis

    async def claim(self, message_id: str) -> bool:
        """Return True iff this ``message_id`` has not been seen in the TTL window.

        Raises ``SurfaceWebhookAuthenticationError`` if Redis is unavailable,
        so the request fails closed instead of silently accepting a replay.
        """
        if not message_id:
            # Defensive: callers gate on a non-empty id, but ``SET "" NX`` would
            # still succeed against a single empty-string key and produce
            # surprise cross-request collisions. Treat as auth failure.
            raise SurfaceWebhookAuthenticationError(
                "WhatsApp message id is required for replay protection"
            )
        redis = await self._get_redis()
        try:
            stored = await redis.set(
                f"{_WHATSAPP_REPLAY_DEDUP_PREFIX}:{message_id}",
                "1",
                ex=self._ttl_seconds,
                nx=True,
            )
        except RedisError as exc:
            logger.warning(
                "WhatsApp replay guard unavailable; failing closed message_id=%s",
                message_id,
                exc_info=True,
            )
            raise SurfaceWebhookAuthenticationError(
                "WhatsApp replay protection cache is unavailable"
            ) from exc
        return bool(stored)

    async def close(self) -> None:
        if self._redis is None:
            return
        redis = self._redis
        self._redis = None
        if hasattr(redis, "aclose"):
            await redis.aclose()
        else:
            await redis.close()


_whatsapp_replay_guard: WhatsAppReplayGuard | None = None


def _get_whatsapp_replay_guard() -> WhatsAppReplayGuard:
    global _whatsapp_replay_guard
    if _whatsapp_replay_guard is None:
        _whatsapp_replay_guard = WhatsAppReplayGuard()
    return _whatsapp_replay_guard


async def close_whatsapp_replay_guard() -> None:
    global _whatsapp_replay_guard
    if _whatsapp_replay_guard is None:
        return
    guard = _whatsapp_replay_guard
    _whatsapp_replay_guard = None
    await guard.close()


def _extract_whatsapp_replay_fields(
    raw_body: bytes,
) -> tuple[int | None, str | None]:
    """Pull ``(timestamp, message_id)`` out of a native WhatsApp webhook body.

    Walks the documented Meta envelope (``object`` / ``entry[]`` /
    ``changes[]`` / ``value``) and prefers ``value.messages[0]`` because that
    is the high-value target (replays there trigger amplified outbound
    replies + amplified agent runs). Status callbacks (``value.statuses[0]``)
    are also accepted so we still reject replays for delivery receipts.

    Returns ``(None, None)`` for malformed bodies; the verifier raises a
    descriptive auth error in that case rather than silently accepting.
    """
    if not raw_body:
        return None, None
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except UnicodeDecodeError, json.JSONDecodeError:
        return None, None
    if not isinstance(payload, dict):
        return None, None

    entry = payload.get("entry")
    if not isinstance(entry, list) or not entry:
        return None, None
    first_entry = entry[0]
    if not isinstance(first_entry, dict):
        return None, None

    changes = first_entry.get("changes")
    if not isinstance(changes, list) or not changes:
        return None, None
    first_change = changes[0]
    if not isinstance(first_change, dict):
        return None, None

    value = first_change.get("value")
    if not isinstance(value, dict):
        return None, None

    # Prefer messages[] (inbound user messages) over statuses[] (delivery
    # receipts). For a single envelope that contains both, the user message
    # id is the more sensitive dedup key.
    for key in ("messages", "statuses"):
        items = value.get(key)
        if not isinstance(items, list) or not items:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ts_raw = item.get("timestamp")
            mid = item.get("id")
            if ts_raw is None or mid is None:
                continue
            try:
                ts_int = int(ts_raw)
            except TypeError, ValueError:
                continue
            return ts_int, str(mid)
    return None, None


class SurfaceWebhookAuthenticationError(DomainError):
    """Webhook signature / authenticity check failed.

    A ``DomainError`` so the global handler translates it automatically (no
    manual catch-and-remap in the webhook controller). ``status_code`` is
    caller-supplied (401 for bad signatures, 404 for platforms without direct
    ingress).
    """

    def __init__(self, detail: str, *, status_code: int = 401):
        super().__init__(detail, code="SURFACE_WEBHOOK_AUTH_FAILED", status_code=status_code)
        # Preserve the legacy attribute name for any existing readers.
        self.detail = detail


class SurfaceWebhookSecurityService:
    def __init__(self, *, credential_resolver: "SurfaceCredentialResolver | None" = None):
        self._credential_resolver = credential_resolver

    def verification_enabled(self) -> bool:
        return bool(surface_settings.surface_webhook_security_enabled)

    def assert_platform_request_allowed(self, platform: str) -> None:
        if str(platform).upper() not in {"SLACK", "TEAMS", "WHATSAPP", "TELEGRAM"}:
            raise SurfaceWebhookAuthenticationError(
                f"Platform '{platform}' does not support direct webhook ingress",
                status_code=404,
            )

    async def verify_platform_request(
        self,
        *,
        platform: str,
        headers: dict[str, str],
        raw_body: bytes,
    ) -> None:
        if not self.verification_enabled():
            return
        normalized = str(platform).upper()
        if normalized == "SLACK":
            self._verify_slack_signature(
                headers=headers,
                raw_body=raw_body,
                signing_secret=surface_settings.slack_signing_secret,
            )
            return
        if normalized == "WHATSAPP":
            await self._verify_whatsapp_signature(
                headers=headers,
                raw_body=raw_body,
                app_secret=surface_settings.whatsapp_app_secret,
            )
            return
        if normalized == "TELEGRAM":
            self._verify_telegram_secret(
                headers=headers,
                webhook_secret=surface_settings.telegram_webhook_secret,
            )
            return
        if normalized == "TEAMS":
            await self._verify_teams_jwt(
                headers=headers,
                expected_app_id=surface_settings.microsoft_bot_app_id,
            )
            return
        raise SurfaceWebhookAuthenticationError(
            f"Platform '{platform}' does not support webhook verification",
            status_code=404,
        )

    async def verify_surface_request(
        self,
        *,
        surface: AgentSurfaceEntity,
        headers: dict[str, str],
        raw_body: bytes,
    ) -> None:
        if not self.verification_enabled():
            return
        if surface.surface_type is SurfacePlatform.TELEGRAM:
            self._verify_telegram_secret(
                headers=headers,
                webhook_secret=surface.webhook_secret,
            )
            return
        if surface.surface_type is SurfacePlatform.WHATSAPP:
            app_secret, _ = await self._resolve_whatsapp_secrets(surface)
            await self._verify_whatsapp_signature(
                headers=headers,
                raw_body=raw_body,
                app_secret=app_secret,
            )
            return
        await self.verify_platform_request(
            platform=surface.surface_type.value,
            headers=headers,
            raw_body=raw_body,
        )

    async def verify_resend_request(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
    ) -> None:
        """Verify a Resend (Svix) inbound webhook signature.

        Resend does not go through ``assert_platform_request_allowed`` (that path
        only covers the four chat platforms with a shared webhook), so the
        controller calls this directly before enqueuing the inbound email.
        """
        if not self.verification_enabled():
            return
        self._verify_svix_signature(
            headers=headers,
            raw_body=raw_body,
            signing_secret=surface_settings.resend_inbound_signing_secret,
        )

    def _verify_svix_signature(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        signing_secret: str | None,
    ) -> None:
        if not signing_secret:
            raise SurfaceWebhookAuthenticationError(
                "Resend inbound signing secret is not configured",
                status_code=503,
            )
        svix_id = headers.get("svix-id") or headers.get("Svix-Id")
        svix_timestamp = headers.get("svix-timestamp") or headers.get("Svix-Timestamp")
        svix_signature = headers.get("svix-signature") or headers.get("Svix-Signature")
        if not svix_id or not svix_timestamp or not svix_signature:
            raise SurfaceWebhookAuthenticationError("Missing Svix signature headers")
        try:
            timestamp_int = int(svix_timestamp)
        except (TypeError, ValueError) as exc:
            raise SurfaceWebhookAuthenticationError("Invalid Svix request timestamp") from exc
        if abs(int(time.time()) - timestamp_int) > _SVIX_MAX_TIMESTAMP_SKEW_SECONDS:
            raise SurfaceWebhookAuthenticationError("Svix request timestamp is too old")

        secret = signing_secret
        if secret.startswith("whsec_"):
            secret = secret[len("whsec_") :]
        try:
            secret_bytes = base64.b64decode(secret)
        except Exception as exc:
            raise SurfaceWebhookAuthenticationError(
                "Resend inbound signing secret is malformed",
                status_code=503,
            ) from exc

        signed_content = b"%b.%b.%b" % (
            svix_id.encode("utf-8"),
            str(timestamp_int).encode("utf-8"),
            raw_body,
        )
        expected = base64.b64encode(
            hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
        ).decode("utf-8")

        # The header is a space-separated list of ``version,signature`` pairs
        # (e.g. ``v1,<sig> v1,<sig2>``) so a rotated secret still verifies.
        for part in svix_signature.split(" "):
            _, _, candidate = part.partition(",")
            if candidate and hmac.compare_digest(expected, candidate):
                return
        raise SurfaceWebhookAuthenticationError("Invalid Svix request signature")

    async def _resolve_whatsapp_secrets(
        self, surface: AgentSurfaceEntity | None
    ) -> tuple[str | None, str | None]:
        """Returns ``(app_secret, verify_token)`` to check a WhatsApp request against.

        A surface bound to a connector account (the org's own WhatsApp Business
        app) is verified against *that account's* stored ``app_secret`` /
        ``verify_token`` — never the system fallback, so a misconfigured or
        missing org credential fails closed instead of silently matching
        Lemma's own managed number. Only account-less (Lemma-managed) surfaces
        use the env-configured system credentials.
        """
        if surface is not None and surface.account_id is not None:
            if self._credential_resolver is None:
                return None, None
            try:
                credentials = await self._credential_resolver.for_account(surface.account_id)
            except Exception:
                logger.warning(
                    "Could not resolve WhatsApp credentials for account %s",
                    surface.account_id,
                    exc_info=True,
                )
                return None, None
            return credentials.get("app_secret"), credentials.get("verify_token")
        return surface_settings.whatsapp_app_secret, surface_settings.whatsapp_verify_token

    async def resolve_whatsapp_verify_token(self, surface: AgentSurfaceEntity | None) -> str | None:
        """The verify token to check ``hub.verify_token`` against for this surface."""
        _, verify_token = await self._resolve_whatsapp_secrets(surface)
        return verify_token

    def _verify_slack_signature(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        signing_secret: str | None,
    ) -> None:
        if not signing_secret:
            raise SurfaceWebhookAuthenticationError(
                "Slack signing secret is not configured",
                status_code=503,
            )
        signature = headers.get("x-slack-signature") or headers.get("X-Slack-Signature")
        timestamp = headers.get("x-slack-request-timestamp") or headers.get(
            "X-Slack-Request-Timestamp"
        )
        if not signature or not timestamp:
            raise SurfaceWebhookAuthenticationError("Missing Slack signature headers")
        try:
            timestamp_int = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise SurfaceWebhookAuthenticationError("Invalid Slack request timestamp") from exc
        if abs(int(time.time()) - timestamp_int) > _SLACK_MAX_TIMESTAMP_SKEW_SECONDS:
            raise SurfaceWebhookAuthenticationError("Slack request timestamp is too old")

        basestring = f"{_SLACK_SIGNATURE_VERSION}:{timestamp_int}:".encode("utf-8") + raw_body
        expected = (
            f"{_SLACK_SIGNATURE_VERSION}="
            f"{hmac.new(signing_secret.encode('utf-8'), basestring, hashlib.sha256).hexdigest()}"
        )
        if not hmac.compare_digest(expected, signature):
            raise SurfaceWebhookAuthenticationError("Invalid Slack request signature")

    async def _verify_whatsapp_signature(
        self,
        *,
        headers: dict[str, str],
        raw_body: bytes,
        app_secret: str | None,
    ) -> None:
        """Verify a native WhatsApp webhook and reject replays.

        Three layered checks (each must pass):

        1. **HMAC signature** — ``sha256=hex(HMAC-SHA256(body, app_secret))``,
           matching Meta's documented scheme. Reject if the header is missing,
           malformed, or doesn't match.
        2. **Timestamp freshness** — parse
           ``entry[0].changes[0].value.messages[0].timestamp`` (falling back to
           ``statuses[0].timestamp`` for status callbacks). Reject if older
           than ``_WHATSAPP_MAX_TIMESTAMP_SKEW_SECONDS`` or further in the
           future than the same window. The body is parsed only after the
           signature verifies, so an attacker cannot use a forged envelope to
           probe the timestamp check.
        3. **Replay dedup** — atomically claim the message id in Redis with
           ``SET … NX EX 2*skew``. A second delivery of the same envelope
           inside the window is rejected. Redis unavailable fails closed.
        """
        if not app_secret:
            raise SurfaceWebhookAuthenticationError(
                "WhatsApp app secret is not configured",
                status_code=503,
            )
        signature = headers.get("x-hub-signature-256") or headers.get("X-Hub-Signature-256")
        if not signature or not signature.startswith("sha256="):
            raise SurfaceWebhookAuthenticationError("Missing WhatsApp signature header")
        expected = (
            "sha256="
            + hmac.new(
                app_secret.encode("utf-8"),
                raw_body,
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(expected, signature):
            raise SurfaceWebhookAuthenticationError("Invalid WhatsApp signature")

        message_timestamp, message_id = _extract_whatsapp_replay_fields(raw_body)
        if message_timestamp is None:
            raise SurfaceWebhookAuthenticationError(
                "WhatsApp webhook is missing a message timestamp"
            )
        if message_id is None:
            raise SurfaceWebhookAuthenticationError("WhatsApp webhook is missing a message id")
        now = int(time.time())
        if abs(now - message_timestamp) > _WHATSAPP_MAX_TIMESTAMP_SKEW_SECONDS:
            raise SurfaceWebhookAuthenticationError(
                "WhatsApp request timestamp is outside the replay window"
            )

        guard = _get_whatsapp_replay_guard()
        if not await guard.claim(message_id):
            raise SurfaceWebhookAuthenticationError(
                "WhatsApp message id has already been processed"
            )

    def _verify_telegram_secret(
        self,
        *,
        headers: dict[str, str],
        webhook_secret: str | None,
    ) -> None:
        if not webhook_secret:
            raise SurfaceWebhookAuthenticationError(
                "Telegram webhook secret is not configured",
                status_code=503,
            )
        header_secret = headers.get("x-telegram-bot-api-secret-token") or headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )
        if not header_secret:
            raise SurfaceWebhookAuthenticationError("Missing Telegram webhook secret header")
        if not hmac.compare_digest(webhook_secret, header_secret):
            raise SurfaceWebhookAuthenticationError("Invalid Telegram webhook secret")

    async def _verify_teams_jwt(
        self,
        *,
        headers: dict[str, str],
        expected_app_id: str | None,
    ) -> None:
        if not expected_app_id:
            raise SurfaceWebhookAuthenticationError(
                "Teams bot app ID is not configured",
                status_code=503,
            )
        auth_header = headers.get("authorization") or headers.get("Authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            raise SurfaceWebhookAuthenticationError("Missing Teams bearer token")
        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            raise SurfaceWebhookAuthenticationError("Missing Teams bearer token")

        openid_url = (
            surface_settings.microsoft_bot_openid_config_url or _BOT_FRAMEWORK_OPENID_CONFIG_URL
        )
        openid_config = await self._get_json_cached(openid_url)
        jwks_uri = str(openid_config.get("jwks_uri") or "").strip()
        if not jwks_uri:
            raise SurfaceWebhookAuthenticationError(
                "Teams OpenID metadata is missing jwks_uri",
                status_code=503,
            )
        jwks = await self._get_json_cached(jwks_uri)
        keys = jwks.get("keys") or []
        signing_key = self._resolve_jwt_signing_key(token, keys)
        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=expected_app_id,
                options={"verify_iss": False},
            )
        except jwt.PyJWTError as exc:
            raise SurfaceWebhookAuthenticationError("Invalid Teams bearer token") from exc

        issuer = str(claims.get("iss") or "").strip()
        if issuer not in _BOT_FRAMEWORK_ALLOWED_ISSUERS:
            raise SurfaceWebhookAuthenticationError("Invalid Teams token issuer")

    async def _get_json_cached(self, url: str) -> dict[str, Any]:
        cache = _get_oidc_cache()
        try:
            cached = await cache.get_json(url)
        except Exception:
            cached = None
        if cached is not None:
            return cached

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url)
            if response.status_code != 200:
                raise SurfaceWebhookAuthenticationError(
                    f"Failed to load Teams verification metadata from {url}",
                    status_code=503,
                )
            payload = response.json()
        if not isinstance(payload, dict):
            raise SurfaceWebhookAuthenticationError(
                f"Invalid Teams verification metadata from {url}",
                status_code=503,
            )
        try:
            await cache.set_json(url, payload, ttl_seconds=_OIDC_CACHE_TTL_SECONDS)
        except Exception:
            pass
        return payload

    def _resolve_jwt_signing_key(self, token: str, keys: list[dict[str, Any]]) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise SurfaceWebhookAuthenticationError("Malformed Teams bearer token") from exc

        key_id = header.get("kid") or header.get("x5t")
        if not key_id:
            raise SurfaceWebhookAuthenticationError("Teams bearer token is missing key id")

        for key in keys:
            if key.get("kid") == key_id or key.get("x5t") == key_id:
                return RSAAlgorithm.from_jwk(json.dumps(key))

        raise SurfaceWebhookAuthenticationError("Unable to resolve Teams signing key")
