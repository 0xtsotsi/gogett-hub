"""Make sure the local ``users`` row exists for a SuperTokens user.

The ``users`` table is the source of truth for identity in our domain
(profile, organization membership, etc.), while SuperTokens holds the auth
session and credentials. On signup we eagerly create the local row, but if
that step fails (DB blip, migration mishap, manual data surgery, race with a
parallel signup of the same email) the SuperTokens user can outlive the
local row — and every authed endpoint then 404s with ``USER_NOT_FOUND`` the
moment it tries to load the user.

This helper provides the single recovery path used by the signin /
signinup overrides: lookup by id, then by email, then create. A concurrent
insert is treated as success (the row exists, which is what we wanted).
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.log.log import get_logger
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.errors import UserConflictError
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.user_service import UserService

logger = get_logger(__name__)


async def ensure_user_entity(
    *,
    user_id: UUID,
    email: str,
    is_verified: bool = True,
) -> UserEntity | None:
    """Return the local ``UserEntity`` for ``user_id``, creating it if missing.

    Returns ``None`` when no email is available (SuperTokens never told us one)
    — without an email we can't build a unique row and silently duplicating
    identity data would be worse than skipping the recovery.

    A missing local row is the bug we are here to fix. A concurrent insert
    racing this one surfaces as :class:`UserConflictError` from
    ``create_user``; that's the same end-state we wanted, so we swallow it
    and re-read the row.
    """
    from app.modules.identity.infrastructure.user_cache import get_user_cache

    normalized_email = normalize_identity_email(email) if email else None
    if not normalized_email:
        return None

    async with async_session_maker() as db_session:
        uow = SqlAlchemyUnitOfWork(db_session)
        message_bus = get_message_bus()
        user_repository = UserRepository(uow, message_bus=message_bus)
        user_service = UserService(
            user_repository=user_repository,
            organization_repository=OrganizationRepository(
                uow, message_bus=message_bus
            ),
        )

        existing = await user_repository.get(user_id)
        if existing is not None:
            return existing

        # The SuperTokens user id may have been re-mapped to a different local
        # row (e.g. after a backup restore that preserved emails but minted
        # new ids). Reusing the existing row by-email would leave the authed
        # session broken — every subsequent endpoint loads by the session's
        # ``user_id`` and would 404 with ``USER_NOT_FOUND``. We can't safely
        # ``UPDATE users SET id`` because of foreign keys elsewhere, and we
        # can't ``DELETE`` the old row without losing memberships. Surface
        # this loudly so ops can run the documented backfill: a later
        # ``INSERT`` here would also collide with the unique-email index.
        by_email = await user_repository.get_by_email(normalized_email)
        if by_email is not None:
            if by_email.id != user_id:
                logger.warning(
                    "identity.supertokens_auth.ensure_user_entity.id_mismatch",
                    user_id=str(user_id),
                    existing_user_id=str(by_email.id),
                )
            return by_email

        # Drop any stale cache entry before rebuilding: the read path goes
        # cache → DB, and a leftover snapshot would hide the recovery for as
        # long as the TTL lasts. ``get_user_cache`` may legitimately return
        # ``None`` (caching disabled, certain test environments); guard so
        # we never raise during a recovery that should always succeed.
        user_cache = get_user_cache()
        if user_cache is not None:
            await user_cache.invalidate(user_id)

        try:
            user = await user_service.create_user(
                UserEntity(
                    id=user_id,
                    email=normalized_email,
                    is_verified=is_verified,
                    is_active=True,
                    is_superuser=False,
                    is_deleted=False,
                ),
                emit_signed_up_event=False,
            )
        except UserConflictError:
            # Lost the race to another signin/request — the row exists now,
            # which is exactly the outcome we wanted. Read it back so the
            # caller has the freshest state. ``flush()`` left the session in
            # a failed-transaction state; roll back before issuing the next
            # query, otherwise SQLAlchemy raises InvalidRequestError.
            await db_session.rollback()
            user = await user_repository.get_by_email(normalized_email)
            if user is None:
                raise
            if user_cache is not None:
                await user_cache.invalidate(user.id)
            logger.info(
                "identity.supertokens_auth.ensure_user_entity.recovered_via_concurrent_insert",
                user_id=str(user_id),
            )
            return user

        await uow.commit()
        # ``create_user`` re-populates the cache via the service layer; force
        # an invalidate just in case that path was skipped, so the next read
        # goes through the freshly-written row.
        if user_cache is not None:
            await user_cache.invalidate(user.id)
        logger.info(
            "identity.supertokens_auth.ensure_user_entity.recovered",
            user_id=str(user_id),
        )
        return user
