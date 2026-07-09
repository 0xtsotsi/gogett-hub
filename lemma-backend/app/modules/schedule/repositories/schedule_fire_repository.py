"""Persistence operations for durable schedule-fire delivery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.schedule.domain.schedule import (
    ScheduleFireDeliveryStatus,
    ScheduleFireEntity,
)
from app.modules.schedule.infrastructure.models.fire import ScheduleFire


class ScheduleFireRepository:
    MAX_ATTEMPTS = 10
    ABANDON_AFTER = timedelta(seconds=60)

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow
        self.session = uow.session

    async def claim(
        self,
        *,
        schedule_id: UUID,
        source_event_id: str,
        target_kind: str,
        payload: dict,
        metadata: dict | None,
        llm_output: dict | None,
    ) -> ScheduleFireEntity | None:
        now = datetime.now(timezone.utc)
        created_id = await self.session.scalar(
            insert(ScheduleFire)
            .values(
                schedule_id=schedule_id,
                source_event_id=source_event_id,
                status=ScheduleFireDeliveryStatus.PROCESSING.value,
                attempts=1,
                target_kind=target_kind,
                payload=payload,
                fire_metadata=metadata or {},
                llm_output=llm_output or {},
                started_at=now,
            )
            .on_conflict_do_nothing(
                constraint="uq_schedule_fire_source_event"
            )
            .returning(ScheduleFire.id)
        )
        if created_id is not None:
            model = await self.session.get(ScheduleFire, created_id)
            assert model is not None
            return model.to_entity()

        model = await self.session.scalar(
            select(ScheduleFire)
            .where(
                ScheduleFire.schedule_id == schedule_id,
                ScheduleFire.source_event_id == source_event_id,
            )
            .with_for_update()
        )
        if model is None:
            return None
        if model.status in {
            ScheduleFireDeliveryStatus.DELIVERED.value,
            ScheduleFireDeliveryStatus.FILTERED.value,
        }:
            return None
        if (
            model.status == ScheduleFireDeliveryStatus.PROCESSING.value
            and model.started_at is not None
            and model.started_at > now - self.ABANDON_AFTER
        ):
            return None
        if model.attempts >= self.MAX_ATTEMPTS:
            model.status = ScheduleFireDeliveryStatus.DEAD_LETTERED.value
            model.completed_at = now
            await self.session.flush()
            return None

        model.status = ScheduleFireDeliveryStatus.PROCESSING.value
        model.attempts += 1
        model.started_at = now
        model.completed_at = None
        model.error_type = None
        model.error_code = None
        await self.session.flush()
        return model.to_entity()

    async def mark_delivered(self, fire_id: UUID, *, target_run_id: str | None) -> None:
        await self._mark(
            fire_id,
            status=ScheduleFireDeliveryStatus.DELIVERED,
            target_run_id=target_run_id,
        )

    async def mark_failed(self, fire_id: UUID, exc: Exception) -> None:
        await self._mark(
            fire_id,
            status=ScheduleFireDeliveryStatus.FAILED,
            error_type=type(exc).__name__,
            error_code=getattr(exc, "code", None),
        )

    async def _mark(
        self,
        fire_id: UUID,
        *,
        status: ScheduleFireDeliveryStatus,
        target_run_id: str | None = None,
        error_type: str | None = None,
        error_code: str | None = None,
    ) -> None:
        await self.session.execute(
            update(ScheduleFire)
            .where(ScheduleFire.id == fire_id)
            .values(
                status=status.value,
                target_run_id=target_run_id,
                error_type=error_type,
                error_code=error_code,
                completed_at=datetime.now(timezone.utc),
            )
        )

    async def list_for_schedule(
        self, schedule_id: UUID, *, limit: int = 100
    ) -> list[ScheduleFireEntity]:
        rows = await self.session.scalars(
            select(ScheduleFire)
            .where(ScheduleFire.schedule_id == schedule_id)
            .order_by(ScheduleFire.created_at.desc(), ScheduleFire.id.desc())
            .limit(limit)
        )
        return [row.to_entity() for row in rows.all()]

    async def reset_for_retry(
        self, *, schedule_id: UUID, fire_id: UUID
    ) -> ScheduleFireEntity | None:
        model = await self.session.scalar(
            select(ScheduleFire)
            .where(ScheduleFire.id == fire_id, ScheduleFire.schedule_id == schedule_id)
            .with_for_update()
        )
        if model is None or model.status not in {
            ScheduleFireDeliveryStatus.FAILED.value,
            ScheduleFireDeliveryStatus.DEAD_LETTERED.value,
        }:
            return None
        model.status = ScheduleFireDeliveryStatus.RECEIVED.value
        model.attempts = 0
        model.completed_at = None
        model.error_type = None
        model.error_code = None
        await self.session.flush()
        return model.to_entity()
