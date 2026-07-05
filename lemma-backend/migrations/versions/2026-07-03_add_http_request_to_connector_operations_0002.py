"""add http_request to connector_operations

Revision ID: 0002_op_http_request
Revises: 0001_baseline
Create Date: 2026-07-03 00:00:00.000000

"""

import warnings
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy import Text  # noqa: F401
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["downgrade", "upgrade", "schema_upgrades", "schema_downgrades", "data_upgrades", "data_downgrades"]

# revision identifiers, used by Alembic.
revision = "0002_op_http_request"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            schema_upgrades()
            data_upgrades()


def downgrade() -> None:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=UserWarning)
        with op.get_context().autocommit_block():
            data_downgrades()
            schema_downgrades()


def schema_upgrades() -> None:
    """schema upgrade migrations go here."""
    op.add_column(
        "connector_operations",
        sa.Column("http_request", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def schema_downgrades() -> None:
    """schema downgrade migrations go here."""
    op.drop_column("connector_operations", "http_request")


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here."""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here."""
