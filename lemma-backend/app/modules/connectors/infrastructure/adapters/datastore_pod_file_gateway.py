"""Pod datastore file gateway — reads/writes pod files by path for connectors.

Wraps the datastore ``DatastoreFileService`` so the connector-operation layer can
resolve pod file paths (e.g. ``/me/report.pdf``) to bytes for uploads and persist
downloaded bytes to a pod path — without the connectors module importing datastore
internals directly (it depends only on ``PodFileGatewayPort``; this adapter is
wired at the composition/DI layer).
"""

from __future__ import annotations

from typing import Any, Optional, Tuple
from uuid import UUID

from app.modules.connectors.domain.ports import PodFileGatewayPort


class DatastorePodFileGateway(PodFileGatewayPort):
    def __init__(self, uow: Any):
        # Import lazily to avoid a connectors -> datastore import cycle at module load.
        from app.modules.datastore.api.dependencies import build_file_service

        self._service = build_file_service(uow)

    async def read_bytes(
        self, *, pod_id: UUID, path: str, ctx: Any
    ) -> Tuple[bytes, Optional[str], Optional[str]]:
        entity, content = await self._service.download_file_content_by_path(pod_id, path, ctx)
        return content, getattr(entity, "mime_type", None), getattr(entity, "name", None)

    async def write_bytes(
        self,
        *,
        pod_id: UUID,
        directory: str,
        name: str,
        content: bytes,
        media_type: Optional[str],
        ctx: Any,
    ) -> dict[str, Any]:
        entity = await self._service.create_file(
            pod_id,
            name,
            content,
            ctx,
            directory_path=directory or "/",
        )
        fallback_path = f"{(directory or '/').rstrip('/')}/{name}"
        return {
            "type": "pod_file",
            "pod_path": getattr(entity, "path", fallback_path),
            "size_bytes": getattr(entity, "size_bytes", len(content)),
            "media_type": media_type or getattr(entity, "mime_type", None),
        }
