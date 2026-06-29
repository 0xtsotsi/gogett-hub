"""Pod-export endpoint — stream the pod as a downloadable bundle archive."""

from __future__ import annotations

import re
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.modules.pod_import.infrastructure.exporter import BundleExporter

router = APIRouter(prefix="/pods/{pod_id}/export", tags=["imports"])


def _safe_filename(pod_name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", pod_name).strip("-") or "pod"
    return f"{slug}.zip"


@router.get("")
async def export_pod(
    pod_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    ctx: PodContextDep,
    with_data: bool = Query(True, description="Include table rows in the bundle."),
) -> Response:
    """Export the pod's resources as a bundle archive (zip download)."""
    pod_name, archive = await BundleExporter(uow).export(
        pod_id=pod_id, user_id=user.id, ctx=ctx, with_data=with_data
    )
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{_safe_filename(pod_name)}"'},
    )
