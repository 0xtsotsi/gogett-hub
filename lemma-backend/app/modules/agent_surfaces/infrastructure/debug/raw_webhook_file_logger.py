from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiofiles

from app.modules.agent_surfaces.config import surface_settings
from app.core.redaction import redact_value


def _enabled_sources() -> set[str]:
    raw_value = surface_settings.surface_raw_webhook_log_sources.strip()
    if not raw_value:
        return set()
    return {
        item.strip().lower()
        for item in raw_value.split(",")
        if item.strip()
    }


def should_log_raw_webhook(source: str) -> bool:
    if not surface_settings.surface_raw_webhook_log_dir:
        return False
    enabled_sources = _enabled_sources()
    if not enabled_sources:
        return True
    return source.lower() in enabled_sources


async def log_raw_webhook_event(
    *,
    source: str,
    payload: dict[str, Any],
    headers: dict[str, Any],
) -> None:
    if not should_log_raw_webhook(source):
        return

    target_dir = Path(surface_settings.surface_raw_webhook_log_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / f"{source.lower()}.jsonl"
    record = {
        "logged_at": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "headers": redact_value(headers),
        "payload": redact_value(payload),
    }
    async with aiofiles.open(target_file, "a", encoding="utf-8") as handle:
        await handle.write(json.dumps(record, ensure_ascii=True) + "\n")
