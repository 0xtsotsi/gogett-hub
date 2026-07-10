"""Add the backend reliability and durable-delivery foundations.

Revision ID: 0003_backend_reliability
Revises: 0002_surfaces_rework

This intentionally centralizes the release's additive database changes in one
revision: event outbox/inbox, usage-counter repair, schedule-fire delivery,
pod provisioning state, and durable pod-bundle jobs.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0003_backend_reliability"
down_revision = "0002_surfaces_rework"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_event_delivery_tables() -> None:
    op.create_table(
        "domain_event_outbox",
        sa.Column("stream", sa.String(length=160), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("producer", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=True),
        sa.Column("causation_id", sa.Uuid(), nullable=True),
        sa.Column("request_id", sa.String(length=160), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_domain_event_outbox_id", "domain_event_outbox", ["id"])
    for column in (
        "stream",
        "event_type",
        "correlation_id",
        "causation_id",
        "request_id",
    ):
        op.create_index(
            f"ix_domain_event_outbox_{column}", "domain_event_outbox", [column]
        )
    op.create_index(
        "ix_domain_event_outbox_dispatch",
        "domain_event_outbox",
        ["available_at", "lease_until"],
        postgresql_where=sa.text(
            "published_at IS NULL AND dead_lettered_at IS NULL"
        ),
    )

    op.create_table(
        "domain_event_inbox",
        sa.Column("consumer", sa.String(length=200), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=200), nullable=False),
        sa.Column(
            "status", sa.String(length=32), server_default="PROCESSING", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_type", sa.String(length=200), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "consumer", "event_id", name="uq_domain_event_inbox_consumer_event"
        ),
    )
    op.create_index("ix_domain_event_inbox_id", "domain_event_inbox", ["id"])
    op.create_index(
        "ix_domain_event_inbox_event_id", "domain_event_inbox", ["event_id"]
    )
    op.create_index(
        "ix_domain_event_inbox_event_type", "domain_event_inbox", ["event_type"]
    )
    op.create_index(
        "ix_domain_event_inbox_status_received",
        "domain_event_inbox",
        ["status", "last_received_at"],
    )


def _repair_usage_counter_uniqueness() -> None:
    # PostgreSQL treats NULL values as distinct in a conventional unique index.
    # Collapse legacy duplicates before installing NULLS NOT DISTINCT.
    op.execute(
        """
        WITH aggregates AS (
            SELECT
                min(id::text)::uuid AS keep_id,
                organization_id,
                user_id,
                window_kind,
                window_start,
                max(window_end) AS window_end,
                sum(used_usd) AS used_usd,
                sum(reserved_usd) AS reserved_usd
            FROM usage_limit_counters
            GROUP BY organization_id, user_id, window_kind, window_start
        )
        UPDATE usage_limit_counters AS target
        SET
            window_end = aggregates.window_end,
            used_usd = aggregates.used_usd,
            reserved_usd = aggregates.reserved_usd
        FROM aggregates
        WHERE target.id = aggregates.keep_id
        """
    )
    op.execute(
        """
        WITH keepers AS (
            SELECT
                min(id::text)::uuid AS keep_id,
                organization_id,
                user_id,
                window_kind,
                window_start
            FROM usage_limit_counters
            GROUP BY organization_id, user_id, window_kind, window_start
        )
        DELETE FROM usage_limit_counters AS target
        USING keepers
        WHERE target.organization_id IS NOT DISTINCT FROM keepers.organization_id
          AND target.user_id IS NOT DISTINCT FROM keepers.user_id
          AND target.window_kind = keepers.window_kind
          AND target.window_start = keepers.window_start
          AND target.id <> keepers.keep_id
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM usage_limit_counters
                GROUP BY organization_id, user_id, window_kind, window_start
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'usage counter deduplication integrity check failed';
            END IF;
        END $$
        """
    )
    op.drop_index("uq_usage_limit_counter_window", table_name="usage_limit_counters")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_usage_limit_counter_window
        ON usage_limit_counters
        (organization_id, user_id, window_kind, window_start)
        NULLS NOT DISTINCT
        """
    )


def _add_schedule_run_ledger() -> None:
    op.add_column(
        "schedules",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.create_table(
        "schedule_runs",
        sa.Column("schedule_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="RECEIVED", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("target_run_id", sa.String(length=255), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("llm_output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_type", sa.String(length=200), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedules.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "schedule_id", "source_event_id", name="uq_schedule_run_source_event"
        ),
    )
    op.create_index("ix_schedule_runs_id", "schedule_runs", ["id"])
    op.create_index(
        "ix_schedule_runs_schedule_id", "schedule_runs", ["schedule_id"]
    )
    op.create_index(
        "ix_schedule_runs_status_updated", "schedule_runs", ["status", "updated_at"]
    )


def _add_pod_provisioning_state() -> None:
    op.add_column(
        "pods",
        sa.Column(
            "provisioning_status",
            sa.String(length=32),
            server_default="UNKNOWN",
            nullable=False,
        ),
    )
    op.add_column(
        "pods",
        sa.Column(
            "provisioning_attempts", sa.Integer(), server_default="0", nullable=False
        ),
    )
    op.add_column(
        "pods",
        sa.Column("provisioning_error_type", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "pods",
        sa.Column("provisioning_error_code", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "pods",
        sa.Column("provisioning_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "pods",
        sa.Column("provisioning_completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pods_provisioning_status", "pods", ["provisioning_status"])


def _add_pod_bundle_jobs() -> None:
    op.create_table(
        "pod_bundle_import_jobs",
        sa.Column("pod_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_step", sa.Integer(), nullable=True),
        sa.Column("committed_steps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pod_bundle_import_jobs_id", "pod_bundle_import_jobs", ["id"])
    op.create_index(
        "ix_pod_bundle_import_jobs_pod_id", "pod_bundle_import_jobs", ["pod_id"]
    )
    op.create_index(
        "ix_pod_bundle_import_jobs_user_id", "pod_bundle_import_jobs", ["user_id"]
    )
    op.create_index(
        "ix_pod_bundle_import_jobs_status", "pod_bundle_import_jobs", ["status"]
    )
    op.create_index(
        "ix_pod_bundle_import_status_updated",
        "pod_bundle_import_jobs",
        ["status", "updated_at"],
    )

    op.create_table(
        "pod_bundle_import_steps",
        sa.Column("import_id", sa.Uuid(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["import_id"], ["pod_bundle_import_jobs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "import_id", "step_index", name="uq_pod_bundle_import_step"
        ),
    )
    op.create_index("ix_pod_bundle_import_steps_id", "pod_bundle_import_steps", ["id"])
    op.create_index(
        "ix_pod_bundle_import_steps_import_id", "pod_bundle_import_steps", ["import_id"]
    )


def upgrade() -> None:
    _add_event_delivery_tables()
    _repair_usage_counter_uniqueness()
    _add_schedule_run_ledger()
    _add_pod_provisioning_state()
    _add_pod_bundle_jobs()


def downgrade() -> None:
    op.drop_table("pod_bundle_import_steps")
    op.drop_table("pod_bundle_import_jobs")

    op.drop_index("ix_pods_provisioning_status", table_name="pods")
    op.drop_column("pods", "provisioning_completed_at")
    op.drop_column("pods", "provisioning_started_at")
    op.drop_column("pods", "provisioning_error_code")
    op.drop_column("pods", "provisioning_error_type")
    op.drop_column("pods", "provisioning_attempts")
    op.drop_column("pods", "provisioning_status")

    op.drop_table("schedule_runs")
    op.drop_column("schedules", "consecutive_failures")

    op.drop_index("uq_usage_limit_counter_window", table_name="usage_limit_counters")
    op.create_index(
        "uq_usage_limit_counter_window",
        "usage_limit_counters",
        ["organization_id", "user_id", "window_kind", "window_start"],
        unique=True,
    )

    op.drop_table("domain_event_inbox")
    op.drop_table("domain_event_outbox")
