from typing import Any, Dict, Union
from uuid import UUID

from supertokens_python.recipe.emailpassword.interfaces import (
    RecipeInterface,
    SignInOkResult,
    SignUpOkResult,
)
from supertokens_python.recipe.session.interfaces import SessionContainer

from app.core.log.log import get_logger
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.supertokens_auth.user_entity_sync import (
    ensure_user_entity,
)

logger = get_logger(__name__)


def override_emailpassword_functions(
    original_implementation: RecipeInterface,
) -> RecipeInterface:
    original_sign_up = original_implementation.sign_up
    original_sign_in = original_implementation.sign_in

    async def sign_up(
        email: str,
        password: str,
        tenant_id: str,
        session: Union[SessionContainer, None],
        should_try_linking_with_session_user: Union[bool, None],
        user_context: Dict[str, Any],
    ):
        from app.modules.identity.domain.email import normalize_identity_email

        email = normalize_identity_email(email)
        result = await original_sign_up(
            email,
            password,
            tenant_id,
            session,
            should_try_linking_with_session_user,
            user_context,
        )

        if isinstance(result, SignUpOkResult) and len(result.user.login_methods) == 1:
            # The signup override is the original eager creation path.
            # `ensure_user_entity` is the single recovery helper used here and
            # by the signin override; both call sites end up with the same row.
            # On a first-time signup we DO want the welcome email event, so
            # bypass the helper's quiet path and call create_user directly.
            from app.core.infrastructure.db.session import async_session_maker
            from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
            from app.core.infrastructure.events.message_bus import get_message_bus
            from app.modules.identity.infrastructure.organization_repositories import (
                OrganizationRepository,
            )
            from app.modules.identity.infrastructure.user_repositories import (
                UserRepository,
            )
            from app.modules.identity.services.user_service import UserService

            async with async_session_maker() as db_session:
                uow = SqlAlchemyUnitOfWork(db_session)
                message_bus = get_message_bus()
                user_service = UserService(
                    user_repository=UserRepository(uow, message_bus=message_bus),
                    organization_repository=OrganizationRepository(
                        uow, message_bus=message_bus
                    ),
                )
                await user_service.create_user(
                    UserEntity(
                        id=UUID(str(result.user.id)),
                        email=normalize_identity_email(result.user.emails[0]),
                        is_verified=True,
                        is_active=True,
                        is_superuser=False,
                        is_deleted=False,
                    ),
                    emit_signed_up_event=True,
                )
                await uow.commit()

        return result

    async def sign_in(
        email: str,
        password: str,
        tenant_id: str,
        session: Union[SessionContainer, None],
        should_try_linking_with_session_user: Union[bool, None],
        user_context: Dict[str, Any],
    ):
        from app.modules.identity.domain.email import normalize_identity_email

        email = normalize_identity_email(email)
        result = await original_sign_in(
            email,
            password,
            tenant_id,
            session,
            should_try_linking_with_session_user,
            user_context,
        )

        # If signin succeeded but the local ``users`` row is missing, every
        # authed endpoint will 404 with USER_NOT_FOUND the moment it tries to
        # load the user. Recover transparently so signing in is always
        # followed by a usable session.
        if isinstance(result, SignInOkResult) and result.user.emails:
            login_methods = result.user.login_methods
            is_verified = bool(login_methods[0].verified) if login_methods else False
            try:
                await ensure_user_entity(
                    user_id=UUID(str(result.user.id)),
                    email=result.user.emails[0],
                    is_verified=is_verified,
                )
            except Exception as exc:  # noqa: BLE001
                # The session is already issued; refusing signin because of a
                # recovery failure would be worse than letting the user in and
                # surfacing the underlying error in logs. The structured
                # logger filters through `_SafeExceptionFilter` to capture
                # the actual exception text safely; we just record the type.
                logger.error(
                    "identity.supertokens_auth.signin.recovery_failed",
                    user_id=str(result.user.id),
                    error_type=type(exc).__name__,
                    exc_info=exc,
                )

        return result

    original_implementation.sign_up = sign_up
    original_implementation.sign_in = sign_in

    return original_implementation
