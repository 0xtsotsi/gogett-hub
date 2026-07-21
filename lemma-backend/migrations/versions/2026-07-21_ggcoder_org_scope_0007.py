"""Promote the shared ggcoder runtime profile from PERSONAL to ORGANIZATION.

Revision ID: 0007_ggcoder_org_scope
Revises: 0006_conversation_history_index

Goal
----
The ``ggcoder`` daemon-backed harness profile is currently a PERSONAL profile,
which means only its owner can see it in the runtime picker (every other
member of the organization sees ``availability_status = UNAVAILABLE_FOR_YOU``
or no row at all). This migration promotes it to ``ORGANIZATION`` so every
member of the hosting organization can see it and dispatch runs to it.

Safety
------
- Targets a specific profile name (``ggcoder``). Operators with a different
  local name should rename before running this migration, or skip it.
- The partial unique index ``uq_agent_runtime_profile_org_name`` already
  prevents two ORGANIZATION profiles in the same org sharing a name, so this
  migration cannot collide with an existing org-scoped profile.
- ``user_id`` is preserved on the row (the profile still records who the
  daemon owner is) but the ``scope`` flip makes it visible to all org members.
- Reversible: the downgrade restores PERSONAL with the original ``user_id``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0007_ggcoder_org_scope"
down_revision = "0006_conversation_history_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


GGCODER_PROFILE_NAME = "ggcoder"


def upgrade() -> None:
    # Promote daemon-backed harness profiles matching the configured name to
    # ORGANIZATION scope. ``user_id`` is intentionally retained so the daemon
    # ownership record remains intact and ``AgentRuntimeDaemonRepository`` lookups
    # by ``(daemon_id, user_id)`` still resolve for the original owner.
    op.execute(
        sa.text(
            """
            UPDATE agent_runtime_profiles
               SET scope = 'ORGANIZATION'
             WHERE scope = 'PERSONAL'
               AND daemon_id IS NOT NULL
               AND name = :profile_name;
            """
        ).bindparams(profile_name=GGCODER_PROFILE_NAME)
    )

    # Defense-in-depth: at most one ORGANIZATION-scoped daemon-backed harness
    # profile may share the same (org, daemon) pair. Stops a second org admin
    # from accidentally shadowing the shared ggcoder profile with another
    # PERSONAL→ORGANIZATION flip targeting the same daemon.
    op.create_index(
        "uq_org_shared_daemon_profile",
        "agent_runtime_profiles",
        ["organization_id", "daemon_id"],
        unique=True,
        postgresql_where=sa.text(
            "scope = 'ORGANIZATION' AND daemon_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_org_shared_daemon_profile",
        table_name="agent_runtime_profiles",
    )
    op.execute(
        sa.text(
            """
            UPDATE agent_runtime_profiles
               SET scope = 'PERSONAL'
             WHERE scope = 'ORGANIZATION'
               AND daemon_id IS NOT NULL
               AND name = :profile_name;
            """
        ).bindparams(profile_name=GGCODER_PROFILE_NAME)
    )