"""Pod event handlers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from faststream import Depends, Logger
from faststream.redis import RedisRouter
from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.jobs.streaq_runtime import streaq_cron
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.identity.domain.ports import IdentityEmailPort
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)
from app.modules.identity.infrastructure.organization_repositories import (
    OrganizationRepository,
)
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.pod.domain.events import (
    PodCreatedEvent,
    PodEvents,
    PodJoinRequestedEvent,
)
from app.modules.pod.domain.pod_entities import PodRole
from app.modules.pod.domain.pod_entities import PodProvisioningStatus
from app.modules.pod.domain.visibility import roles_allow_required
from app.modules.pod.infrastructure.pod_repositories import (
    PodMemberRepository,
    PodRepository,
)
from app.modules.pod.infrastructure.models import Pod

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def provide_identity_email_port() -> IdentityEmailPort:
    return SmtpIdentityEmailAdapter()


@reliable_redis_stream_subscriber(
    router,
    PodEvents.STREAM,
    group="pod-provisioning-events",
    consumer="pod-provisioning-events-consumer",
)
async def on_pod_created(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Handle pod creation event by provisioning pod-scoped data storage.

    This is a system-level operation, so we use repositories directly
    instead of going through the service layer (which enforces user-level
    ACL checks that are not applicable here).
    """
    event_type = event.get("event_type")
    if event_type != PodCreatedEvent.get_event_type():
        return

    async def process() -> None:
        parsed = PodCreatedEvent.model_validate(event)
        await _process_pod_created(parsed, fs_logger)

    await inbox.process("pod.provisioning", event, process)


async def _process_pod_created(parsed: PodCreatedEvent, fs_logger: Logger) -> None:
    fs_logger.info(f"Processing PodCreatedEvent for pod {parsed.pod_id}")

    attempt = await _begin_provisioning(parsed.pod_id)
    if attempt is None:
        return

    schema_manager = SchemaManager()
    try:
        await schema_manager.create_datastore_schema(parsed.pod_id)
    except Exception as exc:
        await _finish_provisioning(
            parsed.pod_id,
            status=PodProvisioningStatus.FAILED,
            error_type=type(exc).__name__,
            error_code=getattr(exc, "code", None),
        )
        fs_logger.error(
            "Pod datastore provisioning failed",
            pod_id=str(parsed.pod_id),
            attempt=attempt,
            error_type=type(exc).__name__,
        )
        raise

    await _finish_provisioning(
        parsed.pod_id,
        status=PodProvisioningStatus.READY,
    )
    fs_logger.info(f"Created pod data schema for pod {parsed.pod_id}")


async def _begin_provisioning(pod_id) -> int | None:
    async with async_session_maker() as session, session.begin():
        pod = await session.scalar(select(Pod).where(Pod.id == pod_id).with_for_update())
        if pod is None or pod.is_deleted:
            return None
        if pod.provisioning_status == PodProvisioningStatus.READY.value:
            return None
        if (
            pod.provisioning_status == PodProvisioningStatus.PROVISIONING.value
            and pod.provisioning_started_at is not None
            and pod.provisioning_started_at
            > datetime.now(timezone.utc) - timedelta(seconds=60)
        ):
            return None
        if pod.provisioning_attempts >= 10:
            return None
        pod.provisioning_status = PodProvisioningStatus.PROVISIONING.value
        pod.provisioning_attempts += 1
        pod.provisioning_started_at = datetime.now(timezone.utc)
        pod.provisioning_completed_at = None
        pod.provisioning_error_type = None
        pod.provisioning_error_code = None
        return pod.provisioning_attempts


async def _finish_provisioning(
    pod_id,
    *,
    status: PodProvisioningStatus,
    error_type: str | None = None,
    error_code: str | None = None,
) -> None:
    async with async_session_maker() as session, session.begin():
        pod = await session.get(Pod, pod_id, with_for_update=True)
        if pod is None:
            return
        pod.provisioning_status = status.value
        pod.provisioning_error_type = error_type
        pod.provisioning_error_code = error_code
        pod.provisioning_completed_at = datetime.now(timezone.utc)


@streaq_cron("*/10 * * * *", name="reconcile_pod_provisioning")
async def reconcile_pod_provisioning() -> None:
    """Classify legacy pods and re-drive schemas missing after old event loss."""
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(Pod.id, Pod.organization_id, Pod.user_id, Pod.name)
                .where(
                    Pod.is_deleted.is_(False),
                    Pod.provisioning_status == PodProvisioningStatus.UNKNOWN.value,
                )
                .limit(100)
            )
        ).all()

    manager = SchemaManager()
    for pod_id, organization_id, user_id, name in rows:
        if await manager.datastore_schema_exists(pod_id):
            await _finish_provisioning(
                pod_id, status=PodProvisioningStatus.READY
            )
            continue
        async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
            model = await uow.session.get(Pod, pod_id, with_for_update=True)
            if model is None or model.provisioning_status != PodProvisioningStatus.UNKNOWN.value:
                continue
            model.provisioning_status = PodProvisioningStatus.PROVISIONING.value
            uow.collect_events(
                [
                    PodCreatedEvent(
                        pod_id=pod_id,
                        organization_id=organization_id,
                        creator_id=user_id,
                        name=name,
                    )
                ]
            )
            await uow.commit()


@reliable_redis_stream_subscriber(
    router,
    PodEvents.STREAM,
    group="pod-join-request-events",
    consumer="pod-join-request-events-consumer",
)
async def on_pod_join_requested(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    email_port: IdentityEmailPort = Depends(provide_identity_email_port),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Notify pod admins by email when a user requests to join a pod."""
    if event.get("event_type") != PodJoinRequestedEvent.get_event_type():
        return

    async def process() -> None:
        parsed = PodJoinRequestedEvent.model_validate(event)
        await _process_pod_join_requested(
            parsed,
            fs_logger,
            uow_factory=uow_factory,
            email_port=email_port,
        )

    await inbox.process("pod.join-request-email", event, process)


async def _process_pod_join_requested(
    parsed: PodJoinRequestedEvent,
    fs_logger: Logger,
    *,
    uow_factory: UnitOfWorkFactory,
    email_port: IdentityEmailPort,
) -> None:
    fs_logger.info(
        f"Processing PodJoinRequestedEvent for pod {parsed.pod_id} "
        f"(request {parsed.join_request_id})"
    )

    async with uow_factory() as uow:
        pod_repository = PodRepository(uow)
        pod_member_repository = PodMemberRepository(uow)
        user_repository = UserRepository(uow)
        organization_repository = OrganizationRepository(uow)

        pod = await pod_repository.get(parsed.pod_id)
        if not pod:
            fs_logger.warning(f"Pod {parsed.pod_id} not found; skipping notification")
            return

        requester = await user_repository.get(parsed.requester_user_id)
        if not requester:
            fs_logger.warning(
                f"Requester {parsed.requester_user_id} not found; skipping notification"
            )
            return
        requester_name = (
            " ".join(part for part in [requester.first_name, requester.last_name] if part)
            or ""
        )

        organization = await organization_repository.get(parsed.organization_id)
        organization_name = organization.name if organization else ""

        members, _ = await pod_member_repository.list_pod_members(
            parsed.pod_id, limit=1000
        )
        admin_emails = [
            member.user_email
            for member in members
            if member.user_email
            and roles_allow_required(member.roles, PodRole.ADMIN)
        ]

    if not admin_emails:
        fs_logger.info(f"No pod admins to notify for pod {parsed.pod_id}")
        return

    for admin_email in admin_emails:
        await email_port.send_pod_join_request_email(
            to_email=admin_email,
            pod_name=pod.name,
            organization_name=organization_name,
            requester_name=requester_name,
            requester_email=str(requester.email),
        )
    fs_logger.info(
        f"Sent pod join request emails to {len(admin_emails)} admin(s) "
        f"for pod {parsed.pod_id}"
    )
