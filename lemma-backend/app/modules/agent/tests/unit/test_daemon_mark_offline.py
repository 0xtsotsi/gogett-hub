"""Tests for the daemon ``mark_offline`` race fix.

Two sockets can target the same ``daemon_id`` when the daemon reconnects
faster than the old socket's finally-block runs. Before the fix, the older
socket's disconnect would unconditionally flip ``status`` back to
``OFFLINE`` and silently clobber the ``ONLINE`` status the newer socket just
wrote. ``mark_offline`` now accepts a ``connected_at`` guard so the UPDATE
only fires when this socket's connection is still the live one on the row.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import update
from sqlalchemy.dialects import sqlite
from sqlalchemy.sql.dml import Update

from app.modules.agent.infrastructure.models import AgentRuntimeDaemonModel
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeDaemonRepository,
)


def _compile(stmt: Update) -> str:
    """Render an UPDATE for inspection without hitting a real database."""
    return str(
        stmt.compile(dialect=sqlite.dialect(), compile_kwargs={"literal_binds": True})
    )


def _build_mark_offline_stmt(*, connected_at: datetime | None) -> Update:
    """Mirror the production WHERE/values construction without a session."""
    now = datetime.now(timezone.utc)
    stmt = (
        update(AgentRuntimeDaemonModel)
        .where(
            AgentRuntimeDaemonModel.id == uuid4(),
            AgentRuntimeDaemonModel.user_id == uuid4(),
        )
        .values(
            status="OFFLINE",
            last_seen_at=now,
            disconnected_at=now,
        )
    )
    if connected_at is not None:
        stmt = stmt.where(AgentRuntimeDaemonModel.connected_at == connected_at)
    return stmt


def test_mark_offline_guarded_where_clause_includes_connected_at():
    """The UPDATE must filter on ``connected_at`` so a stale disconnect can't
    overwrite a newer connection's ONLINE status."""
    sentinel = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
    sql = _compile(_build_mark_offline_stmt(connected_at=sentinel))

    assert "agent_runtime_daemons" in sql
    assert "connected_at =" in sql
    # SQLite's literal rendering of the sentinel -- avoids regex on the date shape.
    assert "2026-07-20" in sql


def test_mark_offline_unconditional_where_omits_connected_at_filter():
    """The legacy caller (no ``connected_at``) keeps the old behaviour so the
    route's update_catalog / generic reset paths still work."""
    sql = _compile(_build_mark_offline_stmt(connected_at=None))

    # ``disconnected_at`` legitimately appears in the SET clause; the guard we
    # care about is the absence of a ``connected_at = ...`` predicate in WHERE.
    assert "agent_runtime_daemons" in sql
    assert "connected_at =" not in sql
    assert "WHERE agent_runtime_daemons.id" in sql
    assert "WHERE agent_runtime_daemons.connected_at" not in sql


@pytest.mark.asyncio
async def test_mark_offline_returns_none_when_connected_at_mismatch():
    """A newer connection's UPDATE will have already changed ``connected_at``,
    so our guarded UPDATE matches zero rows and returns ``None`` -- the live
    ONLINE row is left untouched."""

    class _FakeResult:
        rowcount = 0

    class _FakeSession:
        def __init__(self) -> None:
            self.statements: list[object] = []

        async def execute(self, statement):
            self.statements.append(statement)
            return _FakeResult()

        async def flush(self) -> None:
            return None

    session = _FakeSession()
    uow = SimpleNamespace(session=session)
    repo = AgentRuntimeDaemonRepository(uow)
    daemon_id = uuid4()
    user_id = uuid4()
    stale_connected_at = datetime(2026, 7, 20, 11, 0, 0, tzinfo=timezone.utc)

    result = await repo.mark_offline(
        daemon_id=daemon_id,
        user_id=user_id,
        connected_at=stale_connected_at,
    )

    assert result is None
    assert len(session.statements) == 1
    sql = _compile(session.statements[0])  # type: ignore[arg-type]
    assert "agent_runtime_daemons" in sql
    assert "connected_at =" in sql


@pytest.mark.asyncio
async def test_mark_offline_updates_when_connected_at_matches():
    """When the row's ``connected_at`` is still ours, the UPDATE flips status
    and returns the freshly-read model."""

    class _FakeRow:
        status = "OFFLINE"
        connected_at = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)

    class _FakeResult:
        rowcount = 1

    class _FakeSession:
        def __init__(self) -> None:
            self.statements: list[object] = []
            self.flushes = 0

        async def execute(self, statement):
            self.statements.append(statement)
            return _FakeResult()

        async def flush(self) -> None:
            self.flushes += 1

    session = _FakeSession()
    live_connected_at = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)

    async def _fake_get_for_user(*, daemon_id, user_id):
        return _FakeRow()

    uow = SimpleNamespace(session=session)
    repo = AgentRuntimeDaemonRepository(uow)
    repo.get_for_user = _fake_get_for_user  # type: ignore[assignment]

    result = await repo.mark_offline(
        daemon_id=uuid4(),
        user_id=uuid4(),
        connected_at=live_connected_at,
    )

    assert isinstance(result, _FakeRow)
    assert session.flushes == 1
    sql = _compile(session.statements[0])  # type: ignore[arg-type]
    assert "OFFLINE" in sql
    assert "connected_at =" in sql


@pytest.mark.asyncio
async def test_mark_offline_legacy_caller_still_flips_status():
    """The default (no ``connected_at``) caller keeps the unconditional
    UPDATE so cleanup paths outside the websocket route still work."""

    class _FakeRow:
        status = "OFFLINE"

    class _FakeResult:
        rowcount = 1

    class _FakeSession:
        def __init__(self) -> None:
            self.statements: list[object] = []
            self.flushes = 0

        async def execute(self, statement):
            self.statements.append(statement)
            return _FakeResult()

        async def flush(self) -> None:
            self.flushes += 1

    session = _FakeSession()
    uow = SimpleNamespace(session=session)

    async def _fake_get_for_user(*, daemon_id, user_id):
        return _FakeRow()

    repo = AgentRuntimeDaemonRepository(uow)
    repo.get_for_user = _fake_get_for_user  # type: ignore[assignment]

    result = await repo.mark_offline(daemon_id=uuid4(), user_id=uuid4())

    assert isinstance(result, _FakeRow)
    assert session.flushes == 1
    sql = _compile(session.statements[0])  # type: ignore[arg-type]
    # No connected_at guard -- legacy callers deliberately skip it.
    assert "connected_at =" not in sql
