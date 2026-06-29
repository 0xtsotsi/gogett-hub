"""Pod-import HTTP endpoints.

The three verbs both renderers (CLI, web wizard) drive:
  POST   /pods/{pod_id}/imports            -> plan a bundle, return PLANNED
  GET    /pods/{pod_id}/imports/{id}       -> poll status + per-step progress
  POST   /pods/{pod_id}/imports/{id}/apply -> apply, or resume a FAILED import
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.authorization.dependencies import PodContextDep
from app.modules.pod_import.api.dependencies import ImportAppServiceDep
from app.modules.pod_import.api.schemas import ApplyImportRequest, PodImportResponse

router = APIRouter(prefix="/pods/{pod_id}/imports", tags=["imports"])


@router.post("", response_model=PodImportResponse, status_code=status.HTTP_201_CREATED)
async def create_import(
    pod_id: UUID,
    user: CurrentUser,
    service: ImportAppServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
    bundle: UploadFile = File(...),
    source_name: str | None = Form(None),
) -> PodImportResponse:
    """Upload a bundle archive (.zip/.tar.gz); returns the computed plan
    (PLANNED) with requirements + capabilities. Nothing is applied yet."""
    archive = await bundle.read()
    entity = await service.create(
        pod_id=pod_id,
        user_id=user.id,
        archive=archive,
        filename=bundle.filename,
        source_name=source_name,
    )
    async with uow:
        await uow.commit()
    return PodImportResponse.from_entity(entity)


@router.get("/{import_id}", response_model=PodImportResponse)
async def get_import(
    pod_id: UUID,
    import_id: UUID,
    service: ImportAppServiceDep,
    ctx: PodContextDep,
) -> PodImportResponse:
    entity = await service.get(import_id)
    if entity is None or entity.pod_id != pod_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    return PodImportResponse.from_entity(entity)


@router.post("/{import_id}/apply", response_model=PodImportResponse)
async def apply_import(
    pod_id: UUID,
    import_id: UUID,
    service: ImportAppServiceDep,
    uow: UoWDep,
    ctx: PodContextDep,
    body: ApplyImportRequest | None = None,
) -> PodImportResponse:
    """Apply the import, or resume a previously failed one. Re-callable: already
    completed steps are skipped. Reads the bundle staged at create time.
    ``variables`` resolves the bundle's ${var} placeholders (connector accounts;
    pod-member assignees default to the importing user)."""
    entity = await service.apply(
        import_id=import_id, ctx=ctx, variables=(body.variables if body else None)
    )
    if entity is None or entity.pod_id != pod_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import not found")
    async with uow:
        await uow.commit()
    return PodImportResponse.from_entity(entity)
