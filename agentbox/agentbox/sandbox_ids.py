from __future__ import annotations

import re

from fastapi import HTTPException, status

SANDBOX_ID_RE = re.compile(r"^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$")


def validate_sandbox_id(sandbox_id: str) -> str:
    if not SANDBOX_ID_RE.fullmatch(sandbox_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "sandbox_id must be a DNS label: lowercase letters, numbers, "
                "hyphens, max 63 chars, no leading/trailing hyphen"
            ),
        )
    return sandbox_id


def sandbox_pod_name(sandbox_id: str) -> str:
    return f"agentbox-{validate_sandbox_id(sandbox_id)}"
