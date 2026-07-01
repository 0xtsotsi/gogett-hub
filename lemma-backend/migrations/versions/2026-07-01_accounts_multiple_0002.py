"""accounts: allow multiple per auth config + is_default

Drops the (user_id, auth_config_id) uniqueness so a user can connect several
accounts to the same app (e.g. multiple Telegram bot tokens). Adds an
``is_default`` flag (exactly one default per user/auth_config, enforced by a
partial unique index) used when an account is resolved without an explicit id.

Revision ID: 0002_accounts_multiple
Revises: 0001_baseline
Create Date: 2026-07-01

"""

import warnings
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["downgrade", "upgrade", "schema_upgrades", "schema_downgrades", "data_upgrades", "data_downgrades"]

# revision identifiers, used by Alembic.
revision = '0002_accounts_multiple'
down_revision = '0001_baseline'
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
    op.add_column(
        'accounts',
        sa.Column(
            'is_default',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )
    op.create_index('ix_accounts_is_default', 'accounts', ['is_default'])
    # Existing rows are unique per (user, auth_config) under the old constraint,
    # so promoting them all to default keeps at most one default per pair.
    op.execute("UPDATE accounts SET is_default = true")
    op.drop_index('ix_unique_user_auth_config_account', table_name='accounts')
    op.create_index(
        'uq_accounts_default_per_auth_config',
        'accounts',
        ['user_id', 'auth_config_id'],
        unique=True,
        postgresql_where=sa.text('is_default'),
    )


def schema_downgrades() -> None:
    op.drop_index('uq_accounts_default_per_auth_config', table_name='accounts')
    op.create_index(
        'ix_unique_user_auth_config_account',
        'accounts',
        ['user_id', 'auth_config_id'],
        unique=True,
    )
    op.drop_index('ix_accounts_is_default', table_name='accounts')
    op.drop_column('accounts', 'is_default')


def data_upgrades() -> None:
    pass


def data_downgrades() -> None:
    pass
