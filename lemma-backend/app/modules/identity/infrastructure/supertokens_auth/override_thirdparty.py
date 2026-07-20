from typing import Any, Dict, Optional, Union
from uuid import UUID

from supertokens_python.recipe.session.interfaces import SessionContainer
from supertokens_python.recipe.thirdparty.interfaces import (
    RecipeInterface,
    SignInUpNotAllowed,
    SignInUpOkResult,
)
from supertokens_python.recipe.thirdparty.types import RawUserInfoFromProvider

from app.core.log.log import get_logger
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.infrastructure.supertokens_auth.auth_method_conflicts import (
    get_emailpassword_conflict_reason,
    has_emailpassword_login_method,
    has_thirdparty_login_method,
    list_users_by_email,
)
from app.modules.identity.infrastructure.supertokens_auth.user_entity_sync import (
    ensure_user_entity,
)

logger = get_logger(__name__)


def override_thirdparty_functions(
    original_implementation: RecipeInterface,
) -> RecipeInterface:
    original_sign_in_up = original_implementation.sign_in_up

    async def sign_in_up(
        third_party_id: str,
        third_party_user_id: str,
        email: str,
        is_verified: bool,
        oauth_tokens: Dict[str, Any],
        raw_user_info_from_provider: RawUserInfoFromProvider,
        session: Optional[SessionContainer],
        should_try_linking_with_session_user: Union[bool, None],
        tenant_id: str,
        user_context: Dict[str, Any],
    ):
        email = normalize_identity_email(email)
        users = await list_users_by_email(
            tenant_id=tenant_id,
            email=email,
            user_context=user_context,
        )
        has_matching_thirdparty_user = has_thirdparty_login_method(
            users,
            email=email,
            third_party_id=third_party_id,
            third_party_user_id=third_party_user_id,
        )

        if not has_matching_thirdparty_user and has_emailpassword_login_method(
            users, email
        ):
            return SignInUpNotAllowed(get_emailpassword_conflict_reason())

        result = await original_sign_in_up(
            third_party_id,
            third_party_user_id,
            email,
            is_verified,
            oauth_tokens,
            raw_user_info_from_provider,
            session,
            should_try_linking_with_session_user,
            tenant_id,
            user_context,
        )

        if not isinstance(result, SignInUpOkResult) or not result.user.emails:
            return result

        # ``is_verified`` here means "did the upstream provider already
        # verify the email?" — for returning sign-ins we read it off the
        # user the override just resolved. The third-party override's
        # parameter ``is_verified`` is what SuperTokens passed in; the
        # resolved user's login methods reflect what was stored.
        login_methods = result.user.login_methods
        is_verified = bool(login_methods[0].verified) if login_methods else False

        # The original behaviour: first-time third-party signins (no existing
        # session, brand-new recipe user, single login method) eagerly create
        # the local row. Everything else — returning third-party users, or
        # link-on-existing-session cases — falls through to recovery, which
        # is a no-op when the row already exists.
        is_first_time_signup = (
            session is None
            and result.created_new_recipe_user
            and len(result.user.login_methods) == 1
        )

        # First-time signups need the welcome email event, so call the service
        # directly with emit_signed_up_event=True. Returning users get the
        # recovery path, which never re-emits the signup event.
        if is_first_time_signup:
            from app.core.infrastructure.db.session import async_session_maker
            from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
            from app.core.infrastructure.events.message_bus import get_message_bus
            from app.modules.identity.domain.user_entities import UserEntity
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
                        is_verified=is_verified,
                        is_active=True,
                        is_superuser=False,
                        is_deleted=False,
                    ),
                    emit_signed_up_event=True,
                )
                await uow.commit()
            return result

        try:
            await ensure_user_entity(
                user_id=UUID(str(result.user.id)),
                email=result.user.emails[0],
                is_verified=is_verified,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "identity.supertokens_auth.thirdparty.recovery_failed",
                user_id=str(result.user.id),
                error_type=type(exc).__name__,
                exc_info=exc,
            )

        return result

    original_implementation.sign_in_up = sign_in_up

    return original_implementation
