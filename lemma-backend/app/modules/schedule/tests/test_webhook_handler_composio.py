import pytest
from unittest.mock import AsyncMock
from uuid import uuid4

from app.modules.connectors.domain.connector_trigger import ConnectorTriggerEntity
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.webhook_handler import WebhookHandler
from app.modules.schedule.services.webhook_schedule_matcher import WebhookScheduleMatcher
from app.modules.schedule.domain.errors import ScheduleSourceEventIdRequiredError


@pytest.mark.asyncio
async def test_handle_webhook_composio_success(composio_gmail_event):
    schedule_repo = AsyncMock()
    app_schedule_repo = AsyncMock()
    event_publisher = AsyncMock()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
        connector_trigger_repository=app_schedule_repo,
    )
    handler = WebhookHandler(
        schedule_repository=schedule_repo,
        schedule_matcher=matcher,
        event_publisher=event_publisher,
    )

    schedule_id = uuid4()
    provider_id = composio_gmail_event["data"]["trigger_nano_id"]

    schedule_entity = ScheduleEntity(
        id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id=composio_gmail_event["type"],
        account_id=uuid4(),
        config={"provider_trigger_id": provider_id},
        is_active=True,
    )
    schedule_repo.find_by_config.return_value = [schedule_entity]

    composio_gmail_event["id"] = "evt_fixture_123"
    result_ids = await handler.handle_webhook(
        source="composio", payload=composio_gmail_event
    )

    assert result_ids == [schedule_id]
    schedule_repo.find_by_config.assert_called_once_with(
        schedule_type=ScheduleType.WEBHOOK,
        criteria={"provider_trigger_id": provider_id},
    )
    event_publisher.publish_schedule_fired.assert_called_once()


@pytest.mark.asyncio
async def test_handle_webhook_composio_missing_provider_id():
    schedule_repo = AsyncMock()
    app_schedule_repo = AsyncMock()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
        connector_trigger_repository=app_schedule_repo,
    )
    handler = WebhookHandler(
        schedule_repository=schedule_repo,
        schedule_matcher=matcher,
        event_publisher=AsyncMock(),
    )

    payload = {"type": "some_event", "data": {}}
    with pytest.raises(ScheduleSourceEventIdRequiredError):
        await handler.handle_webhook(source="composio", payload=payload)

    schedule_repo.find_by_config.assert_not_called()


@pytest.mark.asyncio
async def test_handle_webhook_composio_v3_success():
    schedule_repo = AsyncMock()
    app_schedule_repo = AsyncMock()
    event_publisher = AsyncMock()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
        connector_trigger_repository=app_schedule_repo,
    )
    handler = WebhookHandler(
        schedule_repository=schedule_repo,
        schedule_matcher=matcher,
        event_publisher=event_publisher,
    )

    schedule_id = uuid4()
    provider_id = "ti_v3_123"
    payload = {
        "type": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
        "webhook_type": "composio.trigger.message",
        "metadata": {
            "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
            "trigger_id": provider_id,
            "connected_account_id": "ca_123",
        },
        "data": {
            "event_id": "evt_123",
            "summary": "Workflow Discussion",
        },
    }

    schedule_entity = ScheduleEntity(
        id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id=payload["type"],
        account_id=uuid4(),
        config={"provider_trigger_id": provider_id},
        is_active=True,
    )
    schedule_repo.find_by_config.return_value = [schedule_entity]

    result_ids = await handler.handle_webhook(source="composio", payload=payload)

    assert result_ids == [schedule_id]
    schedule_repo.find_by_config.assert_called_once_with(
        schedule_type=ScheduleType.WEBHOOK,
        criteria={"provider_trigger_id": provider_id},
    )
    publish_call = event_publisher.publish_schedule_fired.call_args.kwargs
    assert publish_call["payload"] == payload["data"]
    assert publish_call["metadata"]["event_type"] == payload["metadata"]["trigger_slug"]
    assert (
        publish_call["metadata"]["webhook_event_type"]
        == "composio.trigger.message"
    )
    assert publish_call["source_event_id"] == "evt_123"


@pytest.mark.asyncio
async def test_handle_webhook_slack_envelope_payload(slack_public_channel_event):
    schedule_repo = AsyncMock()
    app_schedule_repo = AsyncMock()
    event_publisher = AsyncMock()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
        connector_trigger_repository=app_schedule_repo,
    )
    handler = WebhookHandler(
        schedule_repository=schedule_repo,
        schedule_matcher=matcher,
        event_publisher=event_publisher,
    )

    app_trigger = ConnectorTriggerEntity(
        id="slack_slack_thread_reply",
        connector_id="slack",
        event_type="message",
        config_schema={
            "properties": {
                "team_id": {"type": "string"},
                "channel_id": {"type": "string"},
                "thread_ts": {"type": "string"},
            }
        },
    )
    app_schedule_repo.get_by_app_name_and_event_type.return_value = [app_trigger]

    schedule_id = uuid4()
    schedule_entity = ScheduleEntity(
        id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id=app_trigger.id,
        account_id=uuid4(),
        config={
            "source": "slack",
            "team_id": "T0784N82P17",
            "channel_id": "C0784NE17PB",
            "thread_ts": "1766236488.619109",
        },
        is_active=True,
    )
    schedule_repo.find_schedules_by_config.return_value = [schedule_entity]

    result_ids = await handler.handle_webhook(
        source="slack", payload=slack_public_channel_event
    )

    assert result_ids == [schedule_id]
    schedule_repo.find_schedules_by_config.assert_called_once_with(
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id=app_trigger.id,
        team_id="T0784N82P17",
        channel_id="C123",
        thread_ts="1766236791.639079",
    )
    event_publisher.publish_schedule_fired.assert_called_once()
    publish_call = event_publisher.publish_schedule_fired.call_args.kwargs
    assert publish_call["payload"] == slack_public_channel_event["payload"]
    assert publish_call["source_event_id"] == "1766236791.639079"
