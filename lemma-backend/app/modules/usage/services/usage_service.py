"""Usage tracking and limit service."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import NamedTuple
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.domain.runtime_profiles import RuntimeProfileScope
from app.modules.agent.domain.value_objects import AgentRunUsage
from app.modules.usage.domain.entities import (
    UsageLimitCounterScope,
    UsageRecord,
    UsageReservation,
)
from app.modules.usage.domain.ports import UsageLimitPort, UsageLimitValues
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.domain.events import ModelUsageEvent, UsageLimitDeniedEvent
from app.modules.usage.infrastructure.repositories import UsageRepository
from app.modules.usage.services.usage_context import UsageExecutionContext

logger = get_logger(__name__)


class ModelPricing(NamedTuple):
    input_per_million_usd: float
    output_per_million_usd: float
    unit_usd: float = 0.0
    # Cache-read rate (Fireworks "cached" input price). ``None`` means "no cache
    # discount" — cached tokens are billed at the full input rate, which
    # over-counts rather than under-counts.
    cached_input_per_million_usd: float | None = None


class UsageService:
    """Service for profile-aware usage recording and system-profile limits."""

    DEFAULT_ORG_MONTHLY_COST_LIMIT_USD: float | None = 50.0
    DEFAULT_USER_WEEKLY_COST_LIMIT_USD: float | None = 10.0
    DEFAULT_USER_MONTHLY_COST_LIMIT_USD: float | None = None
    DEFAULT_RESERVATION_USD = 0.01

    # Per-model rates (USD per 1M tokens). Keyed by both the public model name
    # and the provider model id so resolution succeeds on either. Starts empty;
    # provider-specific cloud modules register their pricing at startup via
    # ``register_model_pricing()``. The fallback below prevents unpriced models
    # from escaping metering entirely.
    _SYSTEM_MODEL_PRICING: dict[str, ModelPricing] = {}

    @classmethod
    def register_model_pricing(cls, pricing: dict[str, ModelPricing]) -> None:
        """Register additional per-model pricing (e.g. from a cloud module).

        Merges into the class-level pricing table; call at application startup
        before any agent runs. Keying by both the public name and the provider
        model id ensures ``_resolve_pricing`` resolves on either form.
        """
        cls._SYSTEM_MODEL_PRICING.update(pricing)

    # Used when a system model has no explicit pricing entry, so that usage is
    # still recorded (and counts toward limits) instead of being silently
    # dropped. Deliberately the most expensive rates in the table with no cache
    # discount, so an unpriced model over-counts rather than runs free. The
    # startup + unit invariant keeps the shipped catalog fully priced, so this
    # should never be hit in practice.
    _FALLBACK_PRICING = ModelPricing(1.74, 4.40, cached_input_per_million_usd=1.74)

    def __init__(
        self,
        *,
        usage_repository: UsageRepository,
        usage_limit_port: UsageLimitPort | None = None,
    ):
        self.usage_repository = usage_repository
        self.usage_limit_port = usage_limit_port

    async def reserve_for_profile(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        profile_id: str,
        profile_scope: str,
        model_name: str,
        amount_usd: float | None = None,
        now: datetime | None = None,
    ) -> UsageReservation | None:
        if not self._is_system_scope(profile_scope):
            return None
        now = now or datetime.now(timezone.utc)
        amount = amount_usd or self.DEFAULT_RESERVATION_USD
        limits = await self.get_usage_limits(
            organization_id=organization_id,
            user_id=user_id,
            now=now,
        )
        scopes: list[UsageLimitCounterScope] = []
        org_monthly = limits["org_monthly"]
        if organization_id is not None and org_monthly["limit_usd"] is not None:
            scopes.append(
                UsageLimitCounterScope(
                    organization_id=organization_id,
                    user_id=None,
                    window_kind="org_month",
                    window_start=org_monthly["window_start"],
                    window_end=org_monthly["reset_at"],
                    limit_usd=org_monthly["limit_usd"],
                    initial_used_usd=org_monthly["used_usd"],
                )
            )
        user_weekly = limits["user_weekly"]
        if user_weekly["limit_usd"] is not None:
            scopes.append(
                UsageLimitCounterScope(
                    organization_id=user_weekly["counter_organization_id"],
                    user_id=user_id,
                    window_kind="user_week",
                    window_start=user_weekly["window_start"],
                    window_end=user_weekly["reset_at"],
                    limit_usd=user_weekly["limit_usd"],
                    initial_used_usd=user_weekly["used_usd"],
                )
            )
        user_monthly = limits["user_monthly"]
        if user_monthly["limit_usd"] is not None:
            scopes.append(
                UsageLimitCounterScope(
                    organization_id=user_monthly["counter_organization_id"],
                    user_id=user_id,
                    window_kind="user_month",
                    window_start=user_monthly["window_start"],
                    window_end=user_monthly["reset_at"],
                    limit_usd=user_monthly["limit_usd"],
                    initial_used_usd=user_monthly["used_usd"],
                )
            )

        counter_ids = await self.usage_repository.reserve_limit_scopes(
            scopes=scopes,
            amount_usd=amount,
        )
        if counter_ids is None:
            self._collect_denied_event(
                organization_id=organization_id,
                user_id=user_id,
                profile_id=profile_id,
                model_name=model_name,
                reason="limit_exceeded",
            )
            raise UsageLimitExceededError()
        return UsageReservation(
            organization_id=organization_id,
            user_id=user_id,
            amount_usd=amount,
            counter_ids=counter_ids,
        )

    async def release_reservation(self, reservation: UsageReservation | None) -> None:
        if reservation is None:
            return
        await self.usage_repository.release_reservation(
            counter_ids=reservation.counter_ids,
            amount_usd=reservation.amount_usd,
        )

    async def record_agent_run_usage(
        self,
        *,
        ctx: UsageExecutionContext,
        runtime_profile: dict[str, object] | None,
        usage_data: AgentRunUsage,
        status: str | None,
        reservation: UsageReservation | None = None,
    ) -> UsageRecord | None:
        if (
            usage_data.input_tokens <= 0
            and usage_data.output_tokens <= 0
            and usage_data.units <= 0
        ):
            await self.release_reservation(reservation)
            return None

        profile_id = self._profile_value(runtime_profile, "profile_id") or "unknown"
        profile_scope = self._profile_value(runtime_profile, "scope") or "ORGANIZATION"
        model_name = self._profile_value(runtime_profile, "model_name") or usage_data.model_name
        provider_model_name = self._profile_value(runtime_profile, "provider_model_name")
        cache_read_tokens = self._coerce_token_count(
            (usage_data.metadata or {}).get("cache_read_tokens")
        )
        cost_usd, pricing_fallback = self._calculate_system_cost(
            profile_scope=profile_scope,
            model_name=model_name,
            provider_model_name=provider_model_name,
            input_tokens=usage_data.input_tokens,
            output_tokens=usage_data.output_tokens,
            units=usage_data.units,
            cache_read_tokens=cache_read_tokens,
        )
        metadata = dict(usage_data.metadata or {})
        if provider_model_name:
            metadata["provider_model_name"] = provider_model_name
        if pricing_fallback:
            metadata["pricing_fallback"] = True
        record = UsageRecord(
            organization_id=ctx.organization_id,
            pod_id=ctx.pod_id,
            user_id=ctx.user_id,
            agent_id=ctx.agent_id,
            conversation_id=ctx.conversation_id,
            agent_run_id=ctx.agent_run_id,
            parent_agent_run_id=ctx.parent_agent_run_id,
            source_type=ctx.source_type,
            source_id=ctx.source_id,
            profile_id=profile_id,
            profile_scope=profile_scope,
            model_name=model_name,
            usage_kind=usage_data.usage_kind,
            input_tokens=usage_data.input_tokens,
            output_tokens=usage_data.output_tokens,
            units=usage_data.units,
            cost_usd=cost_usd,
            status=status,
            metadata=metadata,
        )
        saved = await self.usage_repository.create(record)
        if reservation is not None:
            await self.usage_repository.consume_reservation(
                counter_ids=reservation.counter_ids,
                reserved_usd=reservation.amount_usd,
                actual_usd=cost_usd or 0.0,
            )
        self._collect_recorded_event(saved)
        return saved

    async def record_pydantic_ai_result_usage(
        self,
        *,
        ctx: UsageExecutionContext,
        runtime_profile: dict[str, object] | None,
        result: object,
        status: str | None,
        usage_kind: str = "llm",
        reservation: UsageReservation | None = None,
        metadata: dict[str, object] | None = None,
    ) -> UsageRecord | None:
        usage_data = self.usage_from_pydantic_ai_result(
            result=result,
            runtime_profile=runtime_profile,
            usage_kind=usage_kind,
            metadata=metadata,
        )
        if usage_data is None:
            await self.release_reservation(reservation)
            return None
        return await self.record_agent_run_usage(
            ctx=ctx,
            runtime_profile=runtime_profile,
            usage_data=usage_data,
            status=status,
            reservation=reservation,
        )

    def usage_from_pydantic_ai_result(
        self,
        *,
        result: object,
        runtime_profile: dict[str, object] | None,
        usage_kind: str = "llm",
        metadata: dict[str, object] | None = None,
    ) -> AgentRunUsage | None:
        usage_method = getattr(result, "usage", None)
        if not callable(usage_method):
            return None
        run_usage = usage_method()
        input_tokens = self._usage_value(
            run_usage,
            "input_tokens",
            "request_tokens",
            "prompt_tokens",
        )
        output_tokens = self._usage_value(
            run_usage,
            "output_tokens",
            "response_tokens",
            "completion_tokens",
        )
        units = float(self._usage_value(run_usage, "units"))
        if input_tokens <= 0 and output_tokens <= 0 and units <= 0:
            return None
        model_name = (
            self._profile_value(runtime_profile, "model_name")
            or self._profile_value(runtime_profile, "provider_model_name")
            or "unknown"
        )
        return AgentRunUsage(
            model_name=model_name,
            usage_kind=usage_kind,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            units=units,
            request_count=self._usage_value(run_usage, "requests"),
            tool_call_count=self._usage_value(run_usage, "tool_calls"),
            metadata=metadata,
        )

    async def get_organization_usage_summary(
        self,
        organization_id: UUID,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
    ):
        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=30))
        return await self.usage_repository.get_usage_summary(
            organization_id=organization_id,
            start=start,
            end=end,
            pod_id=pod_id,
            user_id=user_id,
            agent_id=agent_id,
            profile_id=profile_id,
            profile_scope=profile_scope,
            model_name=model_name,
            usage_kind=usage_kind,
            source_type=source_type,
            status=status,
        )

    async def get_usage_events(
        self,
        organization_id: UUID,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        days: int = 30,
        pod_id: UUID | None = None,
        user_id: UUID | None = None,
        agent_id: UUID | None = None,
        profile_id: str | None = None,
        profile_scope: str | None = None,
        model_name: str | None = None,
        usage_kind: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ):
        end = end or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=days))
        return list(
            await self.usage_repository.list_usage(
                organization_id=organization_id,
                start=start,
                end=end,
                pod_id=pod_id,
                user_id=user_id,
                agent_id=agent_id,
                profile_id=profile_id,
                profile_scope=profile_scope,
                model_name=model_name,
                usage_kind=usage_kind,
                source_type=source_type,
                status=status,
                limit=limit,
            )
        )

    async def get_usage_stats(self, organization_id: UUID, **kwargs):
        return list(
            await self.usage_repository.get_usage_stats(
                organization_id=organization_id,
                **kwargs,
            )
        )

    async def get_usage_limits(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        now: datetime | None = None,
    ) -> dict[str, object]:
        now = now or datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        limit_values = await self._resolve_usage_limit_values(
            organization_id=organization_id,
            user_id=user_id,
        )
        user_limit_organization_id = (
            organization_id
            if limit_values.user_limit_scope == "organization"
            else None
        )
        excluded_organization_ids = (
            limit_values.excluded_organization_ids
            if limit_values.user_limit_scope == "global"
            else ()
        )
        org_used = 0.0
        org_reserved = 0.0
        if organization_id is not None:
            org_used = await self.usage_repository.get_system_cost(
                organization_id=organization_id,
                user_id=None,
                start=month_start,
                end=now,
            )
            org_reserved = await self.usage_repository.get_reserved_cost(
                organization_id=organization_id,
                user_id=None,
                window_kind="org_month",
                window_start=month_start,
            )
        user_weekly_used = await self.usage_repository.get_system_cost(
            organization_id=user_limit_organization_id,
            user_id=user_id,
            start=week_start,
            end=now,
            exclude_organization_ids=excluded_organization_ids,
        )
        user_weekly_reserved = await self.usage_repository.get_reserved_cost(
            organization_id=user_limit_organization_id,
            user_id=user_id,
            window_kind="user_week",
            window_start=week_start,
        )
        user_monthly_used = await self.usage_repository.get_system_cost(
            organization_id=user_limit_organization_id,
            user_id=user_id,
            start=month_start,
            end=now,
            exclude_organization_ids=excluded_organization_ids,
        )
        user_monthly_reserved = await self.usage_repository.get_reserved_cost(
            organization_id=user_limit_organization_id,
            user_id=user_id,
            window_kind="user_month",
            window_start=month_start,
        )
        org_scope = self._limit_scope(
            limit_usd=limit_values.org_monthly_limit_usd,
            used_usd=org_used,
            reserved_usd=org_reserved,
            reset_at=self._next_month_start(now),
            window_start=month_start,
            scope="organization",
            counter_organization_id=organization_id,
        )
        user_weekly_scope = self._limit_scope(
            limit_usd=limit_values.user_weekly_limit_usd,
            used_usd=user_weekly_used,
            reserved_usd=user_weekly_reserved,
            reset_at=week_start + timedelta(days=7),
            window_start=week_start,
            scope=limit_values.user_limit_scope,
            counter_organization_id=user_limit_organization_id,
        )
        user_monthly_scope = self._limit_scope(
            limit_usd=limit_values.user_monthly_limit_usd,
            used_usd=user_monthly_used,
            reserved_usd=user_monthly_reserved,
            reset_at=self._next_month_start(now),
            window_start=month_start,
            scope=limit_values.user_limit_scope,
            counter_organization_id=user_limit_organization_id,
        )
        return {
            "organization_id": organization_id,
            "user_id": user_id,
            "org_monthly": org_scope,
            "user_weekly": user_weekly_scope,
            "user_monthly": user_monthly_scope,
            "allowed": bool(
                org_scope["allowed"]
                and user_weekly_scope["allowed"]
                and user_monthly_scope["allowed"]
            ),
        }

    def _calculate_system_cost(
        self,
        *,
        profile_scope: str,
        model_name: str,
        provider_model_name: str | None,
        input_tokens: int,
        output_tokens: int,
        units: float,
        cache_read_tokens: int = 0,
    ) -> tuple[float | None, bool]:
        """Return ``(cost_usd, pricing_fallback)`` for a system-scope run.

        ``input_tokens`` already includes ``cache_read_tokens`` (cache reads are a
        subset of the prompt), so the input is split: non-cached tokens at the
        full input rate, cache-read tokens at the (cheaper) cached rate. Returns
        ``(None, False)`` for non-system scopes (no system cost). The boolean is
        ``True`` when fallback pricing was used because the model was unpriced.
        """
        if not self._is_system_scope(profile_scope):
            return None, False
        pricing, pricing_fallback = self._resolve_pricing(
            model_name, provider_model_name
        )
        total_input = max(0, input_tokens)
        cache_read = min(max(0, cache_read_tokens), total_input)
        non_cached = total_input - cache_read
        cached_rate = (
            pricing.cached_input_per_million_usd
            if pricing.cached_input_per_million_usd is not None
            else pricing.input_per_million_usd
        )
        input_cost = (
            non_cached / 1_000_000 * pricing.input_per_million_usd
            + cache_read / 1_000_000 * cached_rate
        )
        output_cost = (max(0, output_tokens) / 1_000_000) * pricing.output_per_million_usd
        unit_cost = max(0.0, units) * pricing.unit_usd
        return round(input_cost + output_cost + unit_cost, 8), pricing_fallback

    def _resolve_pricing(
        self,
        model_name: str,
        provider_model_name: str | None,
    ) -> tuple[ModelPricing, bool]:
        """Resolve pricing for a system model, returning ``(pricing, fallback)``.

        Never raises: an unpriced model resolves to ``_FALLBACK_PRICING`` (and
        ``fallback=True``) and logs an error, so usage is still recorded and
        counts toward limits instead of being silently dropped — the bug that let
        unpriced models (e.g. glm-5.2) escape metering entirely.
        """
        for candidate in (model_name, provider_model_name):
            if not candidate:
                continue
            normalized = candidate.strip()
            if normalized in self._SYSTEM_MODEL_PRICING:
                return self._SYSTEM_MODEL_PRICING[normalized], False
        logger.error(
            "Missing usage pricing for system model %r (provider=%r); using "
            "fallback pricing so usage is still recorded",
            model_name,
            provider_model_name,
        )
        return self._FALLBACK_PRICING, True

    @staticmethod
    def _coerce_token_count(value: object) -> int:
        try:
            return max(0, int(value))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0

    async def _resolve_usage_limit_values(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> UsageLimitValues:
        # No external plan provider (e.g. no billing installed) -> built-in
        # defaults. A configured provider (billing) resolves plan-based limits.
        if self.usage_limit_port is None:
            return UsageLimitValues(
                org_monthly_limit_usd=(
                    self.DEFAULT_ORG_MONTHLY_COST_LIMIT_USD
                    if organization_id
                    else None
                ),
                user_weekly_limit_usd=self.DEFAULT_USER_WEEKLY_COST_LIMIT_USD,
                user_monthly_limit_usd=self.DEFAULT_USER_MONTHLY_COST_LIMIT_USD,
                user_limit_scope="organization",
            )
        resolved = await self.usage_limit_port.resolve_limits(
            organization_id=organization_id,
            user_id=user_id,
        )
        if isinstance(resolved, UsageLimitValues):
            return resolved
        # Backwards-compatible guard for older tests/adapters returning
        # ``(org_monthly, user_weekly)``.
        org_monthly, user_weekly = resolved
        return UsageLimitValues(
            org_monthly_limit_usd=org_monthly,
            user_weekly_limit_usd=user_weekly,
            user_monthly_limit_usd=None,
            user_limit_scope="organization",
        )

    def _limit_scope(
        self,
        *,
        limit_usd: float | None,
        used_usd: float,
        reserved_usd: float,
        reset_at: datetime,
        window_start: datetime,
        scope: str,
        counter_organization_id: UUID | None,
    ) -> dict[str, object]:
        consumed = used_usd + reserved_usd
        remaining = None if limit_usd is None else max(0.0, limit_usd - consumed)
        return {
            "limit_usd": limit_usd,
            "scope": scope,
            "used_usd": used_usd,
            "reserved_usd": reserved_usd,
            "remaining_usd": remaining,
            "allowed": limit_usd is None or consumed < limit_usd,
            "reset_at": reset_at,
            "window_start": window_start,
            "counter_organization_id": counter_organization_id,
        }

    def _next_month_start(self, now: datetime) -> datetime:
        if now.month == 12:
            return now.replace(
                year=now.year + 1,
                month=1,
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        return now.replace(
            month=now.month + 1,
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    def _collect_recorded_event(self, record: UsageRecord) -> None:
        usage_kind = (
            record.usage_kind.value
            if hasattr(record.usage_kind, "value")
            else str(record.usage_kind)
        )
        profile_scope = (
            record.profile_scope.value
            if hasattr(record.profile_scope, "value")
            else str(record.profile_scope)
        )
        self.usage_repository.uow.collect_events(
            [
                ModelUsageEvent(
                    usage_id=record.id,
                    organization_id=record.organization_id,
                    pod_id=record.pod_id,
                    user_id=record.user_id,
                    agent_id=record.agent_id,
                    conversation_id=record.conversation_id,
                    agent_run_id=record.agent_run_id,
                    parent_agent_run_id=record.parent_agent_run_id,
                    source_type=record.source_type,
                    source_id=record.source_id,
                    profile_id=record.profile_id,
                    profile_scope=profile_scope,
                    model_name=record.model_name,
                    usage_kind=usage_kind,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    units=record.units,
                    cost_usd=record.cost_usd,
                    status=record.status,
                    metadata=record.metadata,
                    occurred_at=record.occurred_at,
                )
            ]
        )

    def _collect_denied_event(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        profile_id: str,
        model_name: str,
        reason: str,
    ) -> None:
        self.usage_repository.uow.collect_events(
            [
                UsageLimitDeniedEvent(
                    organization_id=organization_id,
                    user_id=user_id,
                    profile_id=profile_id,
                    model_name=model_name,
                    reason=reason,
                )
            ]
        )

    def _profile_value(
        self,
        runtime_profile: dict[str, object] | None,
        key: str,
    ) -> str | None:
        if not isinstance(runtime_profile, dict):
            return None
        value = runtime_profile.get(key)
        return value if isinstance(value, str) and value else None

    def _is_system_scope(self, profile_scope: str) -> bool:
        return profile_scope == RuntimeProfileScope.SYSTEM.value or profile_scope == "SYSTEM"

    def _usage_value(self, usage: object, *names: str) -> int:
        for name in names:
            attr = getattr(usage, name, None)
            if callable(attr):
                try:
                    value = attr()
                except TypeError:
                    continue
            else:
                value = attr
            if value is None:
                continue
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
        return 0


def assert_system_pricing_covers_catalog(
    model_names: Iterable[tuple[str, str | None]],
    *,
    pricing: Mapping[str, ModelPricing] | None = None,
) -> list[str]:
    """Return the system models that have no pricing entry (empty == all priced).

    A model is "covered" when either its public name or its provider id is present
    in the pricing table, mirroring ``UsageService._resolve_pricing``'s
    OR-resolution. Used by the startup check and the unit invariant to guarantee
    no system model can slip through metering unpriced — the class of bug that let
    glm-5.2 escape usage tracking and run free past plan limits.
    """
    table = pricing if pricing is not None else UsageService._SYSTEM_MODEL_PRICING
    uncovered: list[str] = []
    for public_name, provider_name in model_names:
        candidates = [
            candidate.strip()
            for candidate in (public_name, provider_name)
            if candidate and candidate.strip()
        ]
        if not any(candidate in table for candidate in candidates):
            uncovered.append(public_name or provider_name or "<unknown>")
    return uncovered
