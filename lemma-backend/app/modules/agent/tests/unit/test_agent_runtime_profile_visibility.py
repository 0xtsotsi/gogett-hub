"""Unit tests for the PERSONAL-visibility defense-in-depth assertion.

These tests pin the contract that :meth:`AgentRuntimeProfileRepository
._assert_personal_visibility` is the last line of defense against a refactor
accidentally widening the SQL filter and leaking another user's PERSONAL
profile (and its encrypted credential ``availability``) into a non-owner's
runtime picker. The assertion is split out as a static method precisely so
it can be exercised without spinning up a database.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)


def _row(*, scope: str, user_id=None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), scope=scope, user_id=user_id)


def test_personal_owned_by_viewer_passes():
    viewer = uuid4()
    rows = [_row(scope="PERSONAL", user_id=viewer)]
    AgentRuntimeProfileRepository._assert_personal_visibility(rows, viewer_id=viewer)


def test_organization_visible_regardless_of_owner():
    viewer = uuid4()
    rows = [_row(scope="ORGANIZATION")]
    AgentRuntimeProfileRepository._assert_personal_visibility(rows, viewer_id=viewer)


def test_personal_owned_by_other_user_raises():
    viewer = uuid4()
    other = uuid4()
    rows = [_row(scope="PERSONAL", user_id=other)]
    with pytest.raises(RuntimeError, match="leaked PERSONAL rows"):
        AgentRuntimeProfileRepository._assert_personal_visibility(rows, viewer_id=viewer)


def test_mixed_set_only_flags_other_users_personal_rows():
    viewer = uuid4()
    other = uuid4()
    rows = [
        _row(scope="ORGANIZATION"),  # OK
        _row(scope="SYSTEM"),  # OK (no org_id; falls outside visibility check)
        _row(scope="PERSONAL", user_id=viewer),  # OK
        _row(scope="PERSONAL", user_id=other),  # leaks
    ]
    with pytest.raises(RuntimeError, match="leaked PERSONAL rows"):
        AgentRuntimeProfileRepository._assert_personal_visibility(rows, viewer_id=viewer)


def test_empty_set_passes():
    AgentRuntimeProfileRepository._assert_personal_visibility([], viewer_id=uuid4())