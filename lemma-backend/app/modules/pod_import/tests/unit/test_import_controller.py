"""Unit tests for the import endpoints' authorization seams.

Two things are pinned here: apply's ownership check runs BEFORE any step
executes (a cross-pod import id must 404 without touching the target pod), and
each pod-scoped route carries the right permission guard. The guards themselves
(require_action) have no unit harness anywhere in the platform — they're only
exercised through the full-stack e2e suite — so these tests assert the wiring
(the right guard object on the right route), not the authorizer's verdicts.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.pod_import.api.controllers.export_controller import router as export_router
from app.modules.pod_import.api.controllers.github_controller import (
    github_import_into_pod_router,
    github_publish_router,
)
from app.modules.pod_import.api.controllers.import_controller import apply_import, router
from app.modules.pod_import.api.dependencies import ImportEditorDep, ImportViewerDep
from app.modules.pod_import.domain.entities import PodImportEntity


class FakeImportService:
    """Records whether apply ever ran — the ordering under test is that an
    ownership mismatch 404s before a single step executes."""

    def __init__(self, entity: PodImportEntity | None):
        self.entity = entity
        self.apply_ran = False

    async def get(self, import_id):
        return self.entity

    async def apply(self, *, import_id, ctx, variables=None):
        self.apply_ran = True
        return self.entity


class FakeUoW:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def commit(self):
        pass


@pytest.mark.asyncio
async def test_apply_404s_on_a_cross_pod_import_before_any_step_runs():
    entity = PodImportEntity.create(pod_id=uuid4(), user_id=uuid4(), plan=[])
    service = FakeImportService(entity)

    with pytest.raises(HTTPException) as exc:
        await apply_import(
            pod_id=uuid4(),  # not the import's pod
            import_id=entity.id,
            service=service,
            uow=FakeUoW(),
            ctx=None,
            body=None,
        )

    assert exc.value.status_code == 404
    assert service.apply_ran is False


@pytest.mark.asyncio
async def test_apply_404s_on_an_unknown_import_before_any_step_runs():
    service = FakeImportService(None)

    with pytest.raises(HTTPException) as exc:
        await apply_import(
            pod_id=uuid4(),
            import_id=uuid4(),
            service=service,
            uow=FakeUoW(),
            ctx=None,
            body=None,
        )

    assert exc.value.status_code == 404
    assert service.apply_ran is False


@pytest.mark.asyncio
async def test_apply_runs_for_the_owning_pod():
    entity = PodImportEntity.create(pod_id=uuid4(), user_id=uuid4(), plan=[])
    service = FakeImportService(entity)

    response = await apply_import(
        pod_id=entity.pod_id,
        import_id=entity.id,
        service=service,
        uow=FakeUoW(),
        ctx=None,
        body=None,
    )

    assert service.apply_ran is True
    assert response.pod_id == entity.pod_id


def _route(target_router, method: str, path: str):
    for route in target_router.routes:
        if route.path == path and method in route.methods:
            return route
    raise AssertionError(f"no route {method} {path}")


def test_every_pod_scoped_route_carries_the_matching_guard():
    # Mutating routes need pod-update; read routes (which still expose the
    # pod's full resource surface) need pod-read. The new-pod routes (/imports,
    # /imports/from-github) are deliberately absent: there is no pod to guard
    # yet, and org membership is enforced by pod_service.create_pod.
    guarded = [
        (router, "POST", "/pods/{pod_id}/imports", ImportEditorDep),
        (router, "POST", "/pods/{pod_id}/imports/{import_id}/apply", ImportEditorDep),
        (router, "GET", "/pods/{pod_id}/imports/{import_id}", ImportViewerDep),
        (
            github_import_into_pod_router,
            "POST",
            "/pods/{pod_id}/imports/from-github/{owner}/{repo}",
            ImportEditorDep,
        ),
        (export_router, "GET", "/pods/{pod_id}/export", ImportViewerDep),
        (github_publish_router, "POST", "/pods/{pod_id}/export/github", ImportEditorDep),
        (github_publish_router, "GET", "/pods/{pod_id}/export/github/preview", ImportViewerDep),
    ]
    for target_router, method, path, guard in guarded:
        assert guard in _route(target_router, method, path).dependencies, f"{method} {path}"
