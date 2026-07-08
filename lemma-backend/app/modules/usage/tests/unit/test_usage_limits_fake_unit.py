"""Fast, DB-free unit tests for the usage limit-resolution seam.

This is the Phase 6 payoff: because UsageService depends on a UsageLimitPort
(implemented by billing) and a repository — not SQLAlchemy — its limit logic is
unit-testable with fakes. The billing-backed equivalents run as e2e in
lemma-cloud; here we pin the math + the no-billing fallback in milliseconds.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.test_support.fakes import FakeUnitOfWork
from app.modules.usage.domain.ports import UsageLimitValues
from app.modules.usage.services.usage_service import UsageService

pytestmark = pytest.mark.unit


class _StubUsageRepository:
    """Only the read methods get_usage_limits calls; spend is fixed per test."""

    def __init__(self, *, used_usd: float) -> None:
        self.uow = FakeUnitOfWork()
        self._used = used_usd

    async def get_system_cost(self, **_kwargs) -> float:
        return self._used

    async def get_reserved_cost(self, **_kwargs) -> float:
        return 0.0


class _FakeUsageLimitPort:
    """Stand-in for billing's adapter: returns fixed (org, user) limits."""

    def __init__(self, *, org_limit, user_limit) -> None:
        self._limits = (org_limit, user_limit)

    async def resolve_limits(self, *, organization_id, user_id):
        del organization_id, user_id
        return self._limits


async def test_user_weekly_limit_math_with_billing_port():
    service = UsageService(
        usage_repository=_StubUsageRepository(used_usd=2.5),
        usage_limit_port=_FakeUsageLimitPort(org_limit=None, user_limit=10.0),
    )

    limits = await service.get_usage_limits(organization_id=None, user_id=uuid4())

    assert limits["allowed"] is True
    assert limits["user_weekly"]["limit_usd"] == 10.0
    assert limits["user_weekly"]["used_usd"] == 2.5
    assert limits["user_weekly"]["remaining_usd"] == 7.5


async def test_user_weekly_limit_blocks_when_exceeded():
    service = UsageService(
        usage_repository=_StubUsageRepository(used_usd=12.0),
        usage_limit_port=_FakeUsageLimitPort(org_limit=None, user_limit=10.0),
    )

    limits = await service.get_usage_limits(organization_id=None, user_id=uuid4())

    assert limits["allowed"] is False
    assert limits["user_weekly"]["allowed"] is False
    assert limits["user_weekly"]["remaining_usd"] == 0.0


async def test_user_monthly_limit_blocks_when_exceeded():
    class _MonthlyLimitPort:
        async def resolve_limits(self, *, organization_id, user_id):
            del organization_id, user_id
            return UsageLimitValues(
                org_monthly_limit_usd=None,
                user_weekly_limit_usd=None,
                user_monthly_limit_usd=10.0,
            )

    service = UsageService(
        usage_repository=_StubUsageRepository(used_usd=12.0),
        usage_limit_port=_MonthlyLimitPort(),
    )

    limits = await service.get_usage_limits(organization_id=None, user_id=uuid4())

    assert limits["allowed"] is False
    assert limits["user_weekly"]["allowed"] is True
    assert limits["user_monthly"]["allowed"] is False
    assert limits["user_monthly"]["limit_usd"] == 10.0
    assert limits["user_monthly"]["remaining_usd"] == 0.0


async def test_falls_back_to_builtin_defaults_without_billing():
    # No UsageLimitPort (the OSS build) -> usage's built-in default limits.
    service = UsageService(
        usage_repository=_StubUsageRepository(used_usd=0.0),
        usage_limit_port=None,
    )

    limits = await service.get_usage_limits(organization_id=None, user_id=uuid4())

    assert (
        limits["user_weekly"]["limit_usd"]
        == UsageService.DEFAULT_USER_WEEKLY_COST_LIMIT_USD
    )
    assert limits["user_monthly"]["limit_usd"] is None
