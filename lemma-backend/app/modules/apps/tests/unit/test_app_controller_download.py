"""Controller-level regression for DB pool exhaustion.

The app archive download endpoints must resolve + authorize the archive location
*inside* a short Unit of Work and read the archive bytes from storage *after*
that UoW (and its pooled DB connection) has been released. These tests drive the
endpoint functions directly with a tracking ``uow_factory`` to pin that ordering.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.apps.api.controllers import app_controller


class _TrackingUowFactory:
    def __init__(self):
        self.state = {"open": False, "opens": 0}

    def __call__(self):
        state = self.state

        class _Cm:
            async def __aenter__(self_):
                state["open"] = True
                state["opens"] += 1
                return object()

            async def __aexit__(self_, *exc):
                state["open"] = False
                return False

        return _Cm()


class _FakeAppService:
    def __init__(self, state, content):
        self._state = state
        self._content = content
        self.resolved_while_open = None
        self.read_while_open = None

    async def resolve_source_archive(self, pod_id, name, user_id, ctx=None):
        self.resolved_while_open = self._state["open"]
        return uuid4(), "source/archive.zip"

    async def resolve_dist_archive(self, pod_id, name, user_id, ctx=None):
        self.resolved_while_open = self._state["open"]
        return uuid4(), "releases/v1/dist/archive.zip"

    async def read_archive(self, app_id, archive_path):
        self.read_while_open = self._state["open"]
        return self._content


@pytest.mark.asyncio
async def test_source_archive_resolves_in_uow_then_reads_after_release(monkeypatch):
    factory = _TrackingUowFactory()
    service = _FakeAppService(factory.state, b"SOURCE-ZIP")
    monkeypatch.setattr(app_controller, "build_app_service", lambda uow: service)

    response = await app_controller.download_app_source_archive(
        uuid4(),
        "dashboard",
        SimpleNamespace(id=uuid4()),
        object(),  # ctx
        uow_factory=factory,
    )

    assert service.resolved_while_open is True
    assert service.read_while_open is False
    assert factory.state["open"] is False
    assert factory.state["opens"] == 1

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"SOURCE-ZIP"


@pytest.mark.asyncio
async def test_dist_archive_resolves_in_uow_then_reads_after_release(monkeypatch):
    factory = _TrackingUowFactory()
    service = _FakeAppService(factory.state, b"DIST-ZIP")
    monkeypatch.setattr(app_controller, "build_app_service", lambda uow: service)

    response = await app_controller.download_app_dist_archive(
        uuid4(),
        "dashboard",
        SimpleNamespace(id=uuid4()),
        object(),  # ctx
        uow_factory=factory,
    )

    assert service.resolved_while_open is True
    assert service.read_while_open is False
    assert factory.state["open"] is False

    body = b"".join([chunk async for chunk in response.body_iterator])
    assert body == b"DIST-ZIP"
