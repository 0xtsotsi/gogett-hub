from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.identity.domain.errors import UserConflictError
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.supertokens_auth import user_entity_sync


@pytest.fixture
def fake_uow():
    """Stand-in UoW exposing ``session`` so the repo / service work without
    binding to a real DB session."""
    uow = AsyncMock()
    uow.session = AsyncMock()
    return uow


@pytest.fixture
def patched_dependencies(monkeypatch, fake_uow):
    """Replace ``async_session_maker`` + ``get_message_bus`` so ``ensure_user_entity``
    runs against an in-memory ``fake_uow`` instead of opening a real DB session.
    Returns the fake uow so tests can pre-populate the user table."""
    monkeypatch.setattr(
        user_entity_sync, "async_session_maker", lambda: _FakeSessionCtx(fake_uow)
    )
    monkeypatch.setattr(user_entity_sync, "get_message_bus", lambda: AsyncMock())
    return fake_uow


class _FakeSessionCtx:
    def __init__(self, uow):
        self._uow = uow

    async def __aenter__(self):
        return self._uow.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_ensure_user_entity_returns_existing_row_without_writing(
    patched_dependencies, monkeypatch
):
    user_id = uuid4()
    email = "already-there@example.com"
    existing = UserEntity(id=user_id, email=email)

    user_repository = AsyncMock()
    user_repository.get.return_value = existing

    captured: dict[str, object] = {}

    def fake_user_repository_factory(*_args, **_kwargs):
        captured["repo"] = user_repository
        return user_repository

    monkeypatch.setattr(
        user_entity_sync, "UserRepository", fake_user_repository_factory
    )
    monkeypatch.setattr(
        user_entity_sync,
        "OrganizationRepository",
        lambda *_args, **_kwargs: AsyncMock(),
    )

    create_user_mock = AsyncMock()
    monkeypatch.setattr(user_entity_sync.UserService, "create_user", create_user_mock)

    result = await user_entity_sync.ensure_user_entity(
        user_id=user_id, email=email, is_verified=True
    )

    assert result is existing
    create_user_mock.assert_not_awaited()


async def test_ensure_user_entity_creates_row_when_missing(
    patched_dependencies, monkeypatch
):
    user_id = uuid4()
    email = "missing@example.com"
    created = UserEntity(id=user_id, email=email)

    user_repository = AsyncMock()
    user_repository.get.return_value = None
    user_repository.get_by_email.return_value = None

    monkeypatch.setattr(
        user_entity_sync, "UserRepository", lambda *_a, **_kw: user_repository
    )
    monkeypatch.setattr(
        user_entity_sync,
        "OrganizationRepository",
        lambda *_a, **_kw: AsyncMock(),
    )

    user_service = AsyncMock()
    user_service.create_user.return_value = created
    monkeypatch.setattr(
        user_entity_sync, "UserService", lambda *args, **kwargs: user_service
    )

    result = await user_entity_sync.ensure_user_entity(
        user_id=user_id, email=email, is_verified=True
    )

    assert result is created
    user_service.create_user.assert_awaited_once()
    entity_arg = user_service.create_user.await_args.args[0]
    assert entity_arg.id == user_id
    assert entity_arg.email == email


async def test_ensure_user_entity_treats_concurrent_conflict_as_success(
    patched_dependencies, monkeypatch
):
    """If another request recovered first, the second call must not raise.

    The scenario this test exercises is the narrowest one where the recovery
    branch's rollback matters: ``get_by_id`` and the *first* ``get_by_email``
    both return None (the row hasn't been written yet), ``create_user`` then
    raises ``UserConflictError`` because another request raced the INSERT, and
    the read-back ``get_by_email`` finally sees the row that the other request
    just committed. Without a session rollback before the read-back, SQLAlchemy
    would raise ``InvalidRequestError`` because the previous ``flush()``
    failed inside an open transaction.
    """
    user_id = uuid4()
    email = "racing@example.com"
    recovered = UserEntity(id=user_id, email=email)

    user_repository = AsyncMock()
    user_repository.get.return_value = None
    # First ``get_by_email`` (pre-create lookup) returns None; the second one
    # (post-conflict read-back) returns the row that the racing request wrote.
    user_repository.get_by_email.side_effect = [None, recovered]

    monkeypatch.setattr(
        user_entity_sync, "UserRepository", lambda *_a, **_kw: user_repository
    )
    monkeypatch.setattr(
        user_entity_sync,
        "OrganizationRepository",
        lambda *_a, **_kw: AsyncMock(),
    )

    user_service = AsyncMock()

    async def racing_create(entity, *, emit_signed_up_event: bool = True):
        # Recovery path always passes emit_signed_up_event=False. Verify that.
        assert emit_signed_up_event is False, (
            "Recovery must not re-emit the signup event"
        )
        raise UserConflictError("raced")

    user_service.create_user.side_effect = racing_create
    monkeypatch.setattr(
        user_entity_sync, "UserService", lambda *args, **kwargs: user_service
    )

    result = await user_entity_sync.ensure_user_entity(
        user_id=user_id, email=email, is_verified=True
    )

    assert result is recovered
    assert user_repository.get_by_email.await_count == 2
    # ``create_user`` raising UserConflictError means ``flush()`` failed on the
    # underlying session, which SQLAlchemy leaves in a failed-transaction state.
    # The recovery branch MUST roll back before issuing the read-back, otherwise
    # the real DB session raises InvalidRequestError and the recovery path
    # explodes with the very symptom it was meant to prevent.
    patched_dependencies.session.rollback.assert_awaited_once()


async def test_ensure_user_entity_returns_none_without_email(
    patched_dependencies, monkeypatch
):
    """Without an email we can't safely build a unique row — return None
    instead of fabricating one with an empty email."""
    user_id = uuid4()

    create_user_mock = AsyncMock()
    monkeypatch.setattr(user_entity_sync.UserService, "create_user", create_user_mock)

    result = await user_entity_sync.ensure_user_entity(
        user_id=user_id, email="", is_verified=True
    )

    assert result is None
    create_user_mock.assert_not_awaited()


async def test_ensure_user_entity_reuses_row_with_matching_email(
    patched_dependencies, monkeypatch
):
    """If a local row exists with the same email but a different id, we
    reuse it instead of failing with a unique-index collision. This
    protects against the rare backup-restore scenario where SuperTokens
    minted new ids but emails were preserved."""
    new_id = uuid4()
    existing_id = uuid4()
    email = "shared@example.com"
    existing = UserEntity(id=existing_id, email=email)

    user_repository = AsyncMock()
    user_repository.get.return_value = None
    user_repository.get_by_email.return_value = existing

    monkeypatch.setattr(
        user_entity_sync, "UserRepository", lambda *_a, **_kw: user_repository
    )
    monkeypatch.setattr(
        user_entity_sync,
        "OrganizationRepository",
        lambda *_a, **_kw: AsyncMock(),
    )

    user_service = AsyncMock()
    monkeypatch.setattr(
        user_entity_sync, "UserService", lambda *args, **kwargs: user_service
    )

    result = await user_entity_sync.ensure_user_entity(
        user_id=new_id, email=email, is_verified=True
    )

    assert result is existing
    user_service.create_user.assert_not_awaited()
