"""rename connector_operations.http_request to execution

Revision ID: 0003_op_execution
Revises: 0002_op_http_request
Create Date: 2026-07-05 00:00:00.000000

"""

import warnings

from alembic import op

__all__ = ["downgrade", "upgrade", "schema_upgrades", "schema_downgrades", "data_upgrades", "data_downgrades"]

# revision identifiers, used by Alembic.
revision = "0003_op_execution"
down_revision = "0002_op_http_request"
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
    op.alter_column("connector_operations", "http_request", new_column_name="execution")


def schema_downgrades() -> None:
    """schema downgrade migrations go here."""
    op.alter_column("connector_operations", "execution", new_column_name="http_request")


def data_upgrades() -> None:
    """Add any optional data upgrade migrations here."""


def data_downgrades() -> None:
    """Add any optional data downgrade migrations here."""
