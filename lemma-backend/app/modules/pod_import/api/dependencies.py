"""Pod-import FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends

from app.core.api.dependencies import UoWDep
from app.core.authorization.dependencies import pod_from_path, require_action
from app.core.authorization.permissions import Permissions
from app.modules.pod_import.infrastructure.existing_resources import PodExistingResources
from app.modules.pod_import.infrastructure.staging import BundleStaging
from app.modules.pod_import.services.import_app_service import ImportAppService


def get_import_app_service(uow: UoWDep) -> ImportAppService:
    # pod scoping is enforced by the route guard. The existing-resources adapter
    # is conservative (everything plans as a create) until its per-type queries
    # are bound, so a pod-id placeholder is harmless here.
    return ImportAppService(
        uow=uow,
        existing=PodExistingResources(uow, pod_id=""),
        staging=BundleStaging(),
    )


ImportAppServiceDep = Annotated[ImportAppService, Depends(get_import_app_service)]

# An import creates/updates many pod resources — guard with pod-update.
ImportEditorDep = require_action(Permissions.POD_UPDATE, pod_from_path)
