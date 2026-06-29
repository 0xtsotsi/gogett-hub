"""add pod_imports

Stateful, resumable bundle imports. A single row per import holds the ordered
plan + per-step status (JSONB) plus the requirements/capabilities computed at
plan time, so a mid-apply failure leaves a durable, resumable checkpoint.

Revision ID: 0002_pod_imports
Revises: 0001_baseline
Create Date: 2026-06-29

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0002_pod_imports"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pod_imports",
        sa.Column("pod_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("plan", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requirements", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["pod_id"], ["pods.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pod_import_pod_status", "pod_imports", ["pod_id", "status"], unique=False)
    op.create_index(op.f("ix_pod_imports_id"), "pod_imports", ["id"], unique=False)
    op.create_index(op.f("ix_pod_imports_pod_id"), "pod_imports", ["pod_id"], unique=False)
    op.create_index(op.f("ix_pod_imports_user_id"), "pod_imports", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_pod_imports_user_id"), table_name="pod_imports")
    op.drop_index(op.f("ix_pod_imports_pod_id"), table_name="pod_imports")
    op.drop_index(op.f("ix_pod_imports_id"), table_name="pod_imports")
    op.drop_index("ix_pod_import_pod_status", table_name="pod_imports")
    op.drop_table("pod_imports")
