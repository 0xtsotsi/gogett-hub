"""Unit tests for the short-UoW + authorization-context scopes.

These pin the property the production connection-pool fix depends on: the pooled
connection is held only inside the ``async with`` block, the contextvar is bound
inside and reset on exit (even on error), and exceptions propagate so the UoW
factory rolls back.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.authorization import scope
from app.core.authorization.current import get_current_context
from app.core.authorization.scope import context_scope, pod_context_scope, uow_scope


class _TrackingUowFactory:
    """Records UoW open/close and the exc type its ``__aexit__`` observed.

    A real ``SessionUnitOfWorkFactory`` commits when exit sees no exception and
    rolls back when it does, so ``exit_exc`` tells us which path a real factory
    would take.
    """

    def __init__(self):
        self.state = {"open": False, "opens": 0, "exits": 0, "exit_exc": "unset"}

    def __call__(self):
        state = self.state

        class _Cm:
            async def __aenter__(self_):
                state["open"] = True
                state["opens"] += 1
                return SimpleNamespace(session=object())

            async def __aexit__(self_, exc_type, exc, tb):
                state["open"] = False
                state["exits"] += 1
                state["exit_exc"] = exc_type
                return False  # never suppress

        return _Cm()


@pytest.mark.asyncio
async def test_context_scope_sets_and_resets():
    ctx = object()
    async with context_scope(ctx) as bound:
        assert bound is ctx
        assert get_current_context() is ctx
    assert get_current_context() is None


@pytest.mark.asyncio
async def test_context_scope_resets_on_exception():
    ctx = object()
    with pytest.raises(ValueError):
        async with context_scope(ctx):
            assert get_current_context() is ctx
            raise ValueError("boom")
    assert get_current_context() is None


@pytest.mark.asyncio
async def test_pod_context_scope_yields_uow_and_ctx(monkeypatch):
    ctx = object()
    monkeypatch.setattr(scope, "resolve_pod_context", AsyncMock(return_value=ctx))
    factory = _TrackingUowFactory()

    async with pod_context_scope(
        factory, request=SimpleNamespace(), user_id=uuid4(), pod_id=uuid4()
    ) as uc:
        assert uc.ctx is ctx
        assert uc.uow is not None
        assert get_current_context() is ctx  # contextvar bound inside
        assert factory.state["open"] is True  # connection held only inside

    assert get_current_context() is None  # reset on exit
    assert factory.state["open"] is False  # connection released
    assert factory.state["opens"] == 1
    assert factory.state["exit_exc"] is None  # clean exit -> factory commits


@pytest.mark.asyncio
async def test_pod_context_scope_resets_and_propagates_on_error(monkeypatch):
    ctx = object()
    monkeypatch.setattr(scope, "resolve_pod_context", AsyncMock(return_value=ctx))
    factory = _TrackingUowFactory()

    with pytest.raises(ValueError):
        async with pod_context_scope(
            factory, request=SimpleNamespace(), user_id=uuid4(), pod_id=uuid4()
        ):
            raise ValueError("boom")

    assert get_current_context() is None  # reset even on error
    assert factory.state["open"] is False
    assert factory.state["exit_exc"] is ValueError  # factory sees exc -> rolls back


@pytest.mark.asyncio
async def test_uow_scope_yields_bare_uow_without_context():
    factory = _TrackingUowFactory()
    async with uow_scope(factory) as uow:
        assert uow is not None
        assert factory.state["open"] is True
        assert get_current_context() is None  # no auth context bound
    assert factory.state["open"] is False
    assert factory.state["exit_exc"] is None
