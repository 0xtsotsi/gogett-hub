"""Schedule adapter for the workflow module."""

from datetime import datetime
from uuid import UUID, uuid4

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.workflow.domain.ports import SchedulePort


class ScheduleControlAdapter(SchedulePort):
    def __init__(self, uow: SqlAlchemyUnitOfWork):
        _ = uow
        from app.modules.schedule.scheduler.api_client import SchedulerAPIClient

        self.scheduler = SchedulerAPIClient()

    async def schedule_workflow_wake(
        self, run_id: UUID, scheduled_at: str, pod_id: UUID, user_id: UUID
    ) -> UUID:
        """Ask the scheduler to wake this workflow run at a specific time.

        The wake is keyed to a fresh per-wait token, NOT the run id. A run with
        several sequential WAIT_UNTIL nodes would otherwise register every timer
        under the same run-id key, so a duplicate or late wake could resume the
        wrong timer (LP-059). The returned token becomes the wait's external_ref,
        and it rides in the fired payload as ``wait_ref`` so the wake resolves to
        exactly this wait. ``workflow_run_id`` is still included so the wake
        handler can build the run's authorization context.
        """
        _ = (pod_id, user_id)
        timer_id = uuid4()
        await self.scheduler.schedule_once_job(
            schedule_id=timer_id,
            run_date=datetime.fromisoformat(scheduled_at),
            payload={
                "workflow_run_id": str(run_id),
                "wait_ref": str(timer_id),
                "scheduled_at": scheduled_at,
                "source": "workflow_wait_until",
            },
            replace_existing=True,
        )
        return timer_id
