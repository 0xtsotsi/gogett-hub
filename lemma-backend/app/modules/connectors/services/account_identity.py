"""Derive a stable provider identity + human-friendly label for a connected
account, so multiple accounts of the same app can be deduped and told apart.

Every connector yields a ``provider_account_id`` (the underlying account/bot the
credentials belong to) and a ``display_name`` (what a user sees). Most apps
derive both from the stored credentials / OAuth profile with no network call;
Telegram is the exception — a bot token only reveals its identity via ``getMe``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.log.log import get_logger

logger = get_logger(__name__)

# Telegram Bot API base; the bot token attaches directly after ``bot`` (so the
# full URL is ``https://api.telegram.org/bot<token>/getMe``). Credentials may
# carry an ``api_base_url`` override (ending at ``.../bot``) for tests.
_TELEGRAM_API_BASE = "https://api.telegram.org/bot"


@dataclass(frozen=True)
class AccountIdentity:
    provider_account_id: str | None = None
    email: str | None = None
    display_name: str | None = None


def _as_dict(credentials: Any) -> dict:
    if isinstance(credentials, dict):
        return credentials
    dump = getattr(credentials, "model_dump", None)
    if callable(dump):
        try:
            return dump(mode="json")
        except Exception:
            return {}
    return {}


def _nested(data: dict, *path: str) -> str | None:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if cur is None:
        return None
    text = str(cur).strip()
    return text or None


def _str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


async def resolve_account_identity(
    *,
    connector_id: str,
    credentials: Any,
    profile: dict | None = None,
) -> AccountIdentity:
    """Best-effort ``(provider_account_id, email, display_name)`` for an account.

    Never raises — an app we can't identify returns empty fields (the account is
    still created, just unlabeled and not deduped)."""
    creds = _as_dict(credentials)
    profile = profile if isinstance(profile, dict) else {}
    raw = creds.get("raw_response") if isinstance(creds.get("raw_response"), dict) else {}
    user_data = creds.get("user_data") if isinstance(creds.get("user_data"), dict) else {}
    app = (connector_id or "").lower()

    try:
        if app == "telegram":
            return await _telegram_identity(creds)
        if app == "whatsapp":
            return _whatsapp_identity(creds)
        if app == "resend":
            return _resend_identity(creds)
        if app in ("gmail", "outlook", "google_drive"):
            return _email_identity(creds, profile, raw, user_data)
        if app == "slack":
            return _slack_identity(creds, profile, raw, user_data)
        return _generic_identity(creds, profile, raw, user_data)
    except Exception as exc:  # pragma: no cover - identity is best-effort
        logger.warning(
            "Account identity resolution failed for connector=%s: %s", app, exc
        )
        return AccountIdentity()


async def _telegram_identity(creds: dict) -> AccountIdentity:
    token = _str(creds.get("bot_token"))
    if not token:
        return AccountIdentity()
    base = _str(creds.get("api_base_url")) or _TELEGRAM_API_BASE
    url = f"{base.rstrip('/')}{token}/getMe"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(url)
            response.raise_for_status()
            result = (response.json() or {}).get("result") or {}
    except Exception as exc:
        logger.warning("Telegram getMe failed while resolving account identity: %s", exc)
        return AccountIdentity()
    bot_id = result.get("id")
    username = _str(result.get("username"))
    first_name = _str(result.get("first_name"))
    display = f"@{username}" if username else first_name
    return AccountIdentity(
        provider_account_id=str(bot_id) if bot_id is not None else None,
        display_name=display,
    )


def _whatsapp_identity(creds: dict) -> AccountIdentity:
    phone_number_id = _str(creds.get("phone_number_id"))
    waba_id = _str(creds.get("waba_id"))
    display_phone = _str(creds.get("display_phone_number")) or _str(creds.get("phone_number"))
    return AccountIdentity(
        provider_account_id=phone_number_id or waba_id,
        display_name=display_phone or phone_number_id or waba_id,
    )


def _resend_identity(creds: dict) -> AccountIdentity:
    from_address = _str(creds.get("from_address"))
    domain = _str(creds.get("domain"))
    return AccountIdentity(
        provider_account_id=from_address or domain,
        email=from_address,
        display_name=from_address or domain,
    )


def _email_identity(creds: dict, profile: dict, raw: dict, user_data: dict) -> AccountIdentity:
    email = (
        _nested(profile, "email_address")
        or _nested(profile, "emailAddress")
        or _nested(profile, "email")
        or _nested(user_data, "profile", "email_address")
        or _nested(user_data, "email")
        or _str(creds.get("email"))
    )
    provider_account_id = email or _nested(raw, "sub") or _nested(user_data, "sub")
    return AccountIdentity(
        provider_account_id=provider_account_id,
        email=email,
        display_name=email,
    )


def _slack_identity(creds: dict, profile: dict, raw: dict, user_data: dict) -> AccountIdentity:
    team_name = (
        _nested(raw, "team", "name")
        or _nested(raw, "team_name")
        or _nested(profile, "team", "name")
    )
    team_id = _nested(raw, "team", "id") or _nested(raw, "team_id")
    user_id = (
        _nested(raw, "authed_user", "id")
        or _nested(raw, "user_id")
        or _nested(profile, "user_id")
    )
    bot_user_id = _nested(raw, "bot_user_id") or _nested(profile, "bot_id")
    return AccountIdentity(
        provider_account_id=user_id or bot_user_id or team_id,
        display_name=team_name or team_id,
    )


def _generic_identity(creds: dict, profile: dict, raw: dict, user_data: dict) -> AccountIdentity:
    email = _nested(profile, "email") or _str(creds.get("email"))
    provider_account_id = (
        _nested(raw, "provider_account_id")
        or _nested(raw, "user", "id")
        or _nested(raw, "user_id")
        or _nested(raw, "id")
        or _nested(user_data, "id")
        or _nested(user_data, "sub")
        or email
    )
    return AccountIdentity(
        provider_account_id=provider_account_id,
        email=email,
        display_name=email or provider_account_id,
    )
