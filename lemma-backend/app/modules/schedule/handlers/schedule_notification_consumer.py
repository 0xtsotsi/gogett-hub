"""Schedule notification handlers.

Consumes ``schedule_events`` (own consumer group) and, on a
``ScheduleDeactivated`` event from the failure circuit breaker, emails the
schedule's creator. This subscriber is the extension point for reacting to
deactivation — future consumers (in-app notification, admin alerting) add their
own subscriber without touching the breaker.
"""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.events.stream_subscriber import redis_stream_sub
from app.modules.schedule.domain.events.schedule import (
    ScheduleDeactivated,
    ScheduleEvents,
)

router = RedisRouter()


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@router.subscriber(
    stream=redis_stream_sub(
        ScheduleEvents.STREAM,
        group="schedule-notifications",
        consumer="schedule-notifications-consumer",
    )
)
async def on_schedule_deactivated(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
) -> None:
    """Email the creator when their schedule is auto-deactivated."""
    if event.get("event_type") != ScheduleDeactivated.get_event_type():
        return

    parsed = ScheduleDeactivated.model_validate(event)

    # Resolve the creator's email.
    from app.modules.identity.infrastructure.user_repositories import UserRepository

    async with uow_factory() as uow:
        user = await UserRepository(uow).get(parsed.user_id)

    if user is None or not getattr(user, "email", None):
        fs_logger.warning(
            "ScheduleDeactivated for %s: no email for user %s; skipping notification",
            parsed.schedule_id,
            parsed.user_id,
        )
        return

    subject = "A scheduled automation was paused after repeated failures"
    html_content = (
        "<p>One of your scheduled automations was automatically paused because it "
        f"failed {parsed.consecutive_failures} times in a row.</p>"
        f"<p>Schedule ID: <code>{parsed.schedule_id}</code></p>"
        "<p>Re-enable it once you've addressed the cause; it will start fresh.</p>"
    )

    try:
        from app.core.email.email_sender import EmailSender

        sender = EmailSender.from_settings()
        await sender.send_email(
            to_email=str(user.email),
            subject=subject,
            html_content=html_content,
        )
        fs_logger.info(
            "Sent deactivation notice for schedule %s to %s",
            parsed.schedule_id,
            user.email,
        )
    except Exception:
        # Best-effort: the deactivation itself is already durable; a missed email
        # must not crash the consumer or wedge the stream.
        fs_logger.exception(
            "Failed to send deactivation email for schedule %s", parsed.schedule_id
        )
