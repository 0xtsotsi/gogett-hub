"""Fault tolerance for the transactional outbox staging path.

The outbox is a delivery mechanism, not the source of truth for domain state.
A staging failure (missing table, schema drift, partial deploy) must NOT
cascade into the domain commit — otherwise user-facing operations like
signup surface as opaque 500s ("Something went wrong") instead of writing
the local row.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork


class _FakeEvent(DomainEvent):
    event_type: str = "test.event"
    producer: str = "test"

    @classmethod
    def stream_name(cls) -> str:
        return "test_stream"


def _make_uow_with_pending() -> SqlAlchemyUnitOfWork:
    uow = SqlAlchemyUnitOfWork(session=MagicMock())
    uow._pending_events = [_FakeEvent(event_id=uuid4())]
    # ``begin_nested`` is a sync (non-async) context manager in SQLAlchemy's
    # AsyncSession, but we mock it as an async context manager so the test
    # can drive failure modes explicitly. Real AsyncSession.begin_nested()
    # returns a session-transaction-like object that supports ``async with``.
    @asynccontextmanager
    async def begin_nested(*_args, **_kwargs):
        yield MagicMock()

    uow.session.begin_nested = begin_nested
    return uow


async def test_commit_succeeds_when_outbox_insert_fails():
    """A staging failure must be swallowed so the domain commit survives.
    This is the symptom that produced user-facing "Something went wrong"
    errors on signup in dev environments where the outbox table was
    missing. The staging INSERT must run inside a SAVEPOINT so a failure
    there does not poison the outer domain transaction."""
    uow = _make_uow_with_pending()
    uow.session.execute = AsyncMock(
        side_effect=DBAPIError("INSERT", {}, Exception("nope"))
    )
    uow.session.commit = AsyncMock()

    # Must not raise — the domain commit must still succeed.
    await uow.commit()

    uow.session.execute.assert_awaited_once()  # staging was attempted
    uow.session.commit.assert_awaited_once()  # domain commit happened


async def test_commit_skips_staging_when_no_pending_events():
    uow = SqlAlchemyUnitOfWork(session=MagicMock())
    uow._pending_events = []
    uow.session.execute = AsyncMock()
    uow.session.commit = AsyncMock()

    await uow.commit()

    uow.session.execute.assert_not_awaited()
    uow.session.commit.assert_awaited_once()


async def test_commit_stages_when_outbox_healthy():
    """Happy path: staging runs inside a SAVEPOINT, then domain commit runs."""
    uow = _make_uow_with_pending()
    uow.session.execute = AsyncMock(return_value=MagicMock())
    uow.session.commit = AsyncMock()

    await uow.commit()

    uow.session.execute.assert_awaited_once()
    uow.session.commit.assert_awaited_once()
    assert uow._pending_events == []  # cleared after staging


async def test_staging_runs_inside_savepoint():
    """The outbox INSERT must run inside ``session.begin_nested()`` so a
    failure there can be rolled back without aborting the surrounding
    domain transaction. Without the SAVEPOINT, a missing
    ``domain_event_outbox`` table would roll back the entire signup."""
    entered = False
    exited = False

    @asynccontextmanager
    async def tracked_nested():
        nonlocal entered, exited
        entered = True
        yield MagicMock()
        exited = True

    uow = SqlAlchemyUnitOfWork(session=MagicMock())
    uow._pending_events = [_FakeEvent(event_id=uuid4())]
    uow.session.begin_nested = tracked_nested
    uow.session.execute = AsyncMock(return_value=MagicMock())
    uow.session.commit = AsyncMock()

    await uow.commit()

    assert entered is True
    assert exited is True


async def test_domain_write_errors_still_surface():
    """The staging catch must not shield the *commit* from real errors. A
    DBAPIError raised by ``session.commit()`` itself must propagate so
    legitimate domain failures (e.g. constraint violations) still 500."""
    uow = SqlAlchemyUnitOfWork(session=MagicMock())
    uow._pending_events = []
    uow.session.execute = AsyncMock()
    uow.session.commit = AsyncMock(
        side_effect=DBAPIError("COMMIT", {}, Exception("boom"))
    )

    with pytest.raises(DBAPIError):
        await uow.commit()


async def test_staging_clears_pending_even_when_staging_fails():
    """If staging is skipped due to a transient failure, we still need to
    clear the pending list so subsequent commits don't try to re-stage the
    same events indefinitely."""
    uow = _make_uow_with_pending()
    uow.session.execute = AsyncMock(
        side_effect=DBAPIError("INSERT", {}, Exception("nope"))
    )
    uow.session.commit = AsyncMock()

    await uow.commit()

    assert uow._pending_events == []