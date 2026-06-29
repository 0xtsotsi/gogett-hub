"""Application service tying the import engine to persistence + planning.

Thin orchestration the controller calls: create an import from a staged bundle
(build the plan, persist it PLANNED), fetch one, and apply/resume one. The
step-by-step apply itself lives in :class:`ImportService`.
"""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.pod_import.domain.entities import PodImportEntity
from app.modules.pod_import.infrastructure.applier import (
    BackendResourceApplier,
    ImportApplyContext,
)
from app.modules.pod_import.infrastructure.repositories import PodImportRepository
from app.modules.pod_import.infrastructure.staging import BundleStaging
from app.modules.pod_import.services.import_service import ImportService
from app.modules.pod_import.services.plan_builder import ExistingResources, build_plan


class ImportAppService:
    def __init__(
        self,
        *,
        uow: SqlAlchemyUnitOfWork,
        existing: ExistingResources,
        staging: BundleStaging,
    ) -> None:
        self.uow = uow
        self._repo = PodImportRepository(uow)
        self._existing = existing
        self._staging = staging
        self._engine = ImportService(
            repository=self._repo,
            applier=BackendResourceApplier(uow),
        )

    async def create(
        self,
        *,
        pod_id: UUID,
        user_id: UUID,
        archive: bytes,
        filename: str | None,
        source_name: str | None,
    ) -> PodImportEntity:
        # Create the aggregate first so its id keys the staged bundle, then plan.
        entity = PodImportEntity.create(
            pod_id=pod_id,
            user_id=user_id,
            plan=[],
            source_name=source_name,
        )
        bundle_root = self._staging.stage(entity.id, archive, filename)
        steps, requirements, capabilities = build_plan(bundle_root, self._existing)
        entity.plan = steps
        entity.requirements = requirements
        entity.capabilities = capabilities
        await self._repo.save(entity)
        return entity

    async def get(self, import_id: UUID) -> PodImportEntity | None:
        return await self._repo.get(import_id)

    async def apply(
        self,
        *,
        import_id: UUID,
        ctx: Context,
        variables: dict[str, str] | None = None,
    ) -> PodImportEntity | None:
        entity = await self._repo.get(import_id)
        if entity is None:
            return None
        bundle_root = self._staging.path_for(import_id)
        if bundle_root is None:
            raise FileNotFoundError(
                f"Staged bundle for import {import_id} not found — re-upload to re-plan."
            )
        replacements = await self._build_replacements(entity, bundle_root, variables or {})
        apply_ctx = ImportApplyContext(
            pod_id=entity.pod_id,
            user_id=entity.user_id,
            bundle_path=bundle_root,
            ctx=ctx,
            variables=replacements,
        )
        return await self._engine.apply(entity, ctx=apply_ctx)

    async def _build_replacements(
        self, entity: PodImportEntity, bundle_root, supplied: dict[str, str]
    ) -> dict[str, str]:
        """Map the bundle's ${var} placeholders to concrete values: supplied
        connector accounts win; pod-member assignees default to the importing
        user's membership in the target pod."""
        from lemma_pod_bundle import build_replacements, declared_variables

        member_default = await self._importing_member_id(entity.pod_id, entity.user_id)
        return build_replacements(
            declared_variables(bundle_root),
            supplied=supplied,
            pod_member_default=member_default,
        )

    async def _importing_member_id(self, pod_id: UUID, user_id: UUID) -> str | None:
        from app.modules.pod.api.dependencies import get_pod_member_service

        try:
            member = await get_pod_member_service(self.uow).get_pod_member_by_user_id(
                pod_id, user_id, requester_user_id=user_id
            )
            return str(member.id)
        except Exception:
            return None
