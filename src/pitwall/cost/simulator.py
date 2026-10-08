"""Pure what-if route and cost projection for FinOps workflows."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import Enum
from typing import Any, Literal, cast

from pitwall.config import RoutingWeights
from pitwall.core.models import Capability, Provider
from pitwall.cost.estimator import PerTokenPricing, parse_pricing_model
from pitwall.models.fit import Cloud, fit_options
from pitwall.models.schema import Variant
from pitwall.providers.registry import ProviderRegistry, get_default_registry
from pitwall.routing.context import AvailabilitySnapshot, PlanningContext, ProviderInput
from pitwall.routing.lockout import LockoutSnapshot
from pitwall.routing.production import (
    NoExecutableRouteError,
    ProductionRoutePlan,
    RouteElimination,
    RoutingOperation,
    build_production_plan,
)
from pitwall.routing.quota import QuotaSnapshot
from pitwall.routing.types import RoutingRequest
from pitwall.runpod_client.graphql import RunpodGpuType

_USD_QUANTUM = Decimal("0.000001")
DEFAULT_MAX_ATTEMPTS = 3
_CAPACITY_GPU_KEYS = ("gpu_names", "gpu_types", "gpuTypeIds", "gpu_type_priority")

type AvailabilityEntry = tuple[str, str, str, int, bool]
type PriceOverrides = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class WhatIfWorkload:
    """One hypothetical workload to replay through the production planner."""

    request: RoutingRequest
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderCostProjection:
    """Cost quote for one provider in the planned attempt chain."""

    provider_id: str
    attempt: int
    estimate_usd: Decimal
    upper_bound_usd: Decimal
    pricing_kind: str
    selected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "attempt": self.attempt,
            "estimate_usd": _decimal_to_str(self.estimate_usd),
            "upper_bound_usd": _decimal_to_str(self.upper_bound_usd),
            "pricing_kind": self.pricing_kind,
            "selected": self.selected,
        }


@dataclass(frozen=True, slots=True)
class ProngOption:
    """One cross-prong cost row: own-pod serve, free burn-down, or metered API."""

    prong: Literal["own_serve", "free", "metered"]
    usd_per_million_tokens: Decimal
    coverage_pct: float
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "prong": self.prong,
            "usd_per_million_tokens": _decimal_to_str(self.usd_per_million_tokens),
            "coverage_pct": self.coverage_pct,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class OwnPodFit:
    """Own-pod hardware inputs resolved through ``pitwall.models.fit_options``."""

    variant: Variant
    gpu_types: tuple[RunpodGpuType, ...]
    ttl_minutes: int
    cloud: Cloud = "secure"


@dataclass(frozen=True, slots=True)
class WhatIfProjection:
    """Projected route, selected-provider reservation, and budget headroom.

    ``plan`` is ``None`` when the production planner finds no executable provider; the
    reasons are then in ``eliminated``.
    """

    plan: ProductionRoutePlan | None
    eliminated: tuple[RouteElimination, ...]
    attempt_costs: tuple[ProviderCostProjection, ...]
    reserved_usd: Decimal
    current_spend_usd: Decimal
    projected_spend_usd: Decimal
    budget_usd: Decimal | None
    budget_headroom_usd: Decimal | None
    would_exceed_budget: bool | None
    prong_comparison: tuple[ProngOption, ...] = ()

    @property
    def selected_cost(self) -> ProviderCostProjection | None:
        for attempt_cost in self.attempt_costs:
            if attempt_cost.selected:
                return attempt_cost
        return None

    def to_dict(self) -> dict[str, Any]:
        selected = self.selected_cost
        return {
            "plan": self.plan.to_dict() if self.plan is not None else None,
            "eliminated": [item.to_dict() for item in self.eliminated],
            "cost": {
                "attempts": [attempt.to_dict() for attempt in self.attempt_costs],
                "selected": selected.to_dict() if selected is not None else None,
                "reserved_usd": _decimal_to_str(self.reserved_usd),
                "current_spend_usd": _decimal_to_str(self.current_spend_usd),
                "projected_spend_usd": _decimal_to_str(self.projected_spend_usd),
                "budget_usd": _optional_decimal_to_str(self.budget_usd),
                "budget_headroom_usd": _optional_decimal_to_str(self.budget_headroom_usd),
                "would_exceed_budget": self.would_exceed_budget,
            },
            "prong_comparison": [row.to_dict() for row in self.prong_comparison],
        }


@dataclass(frozen=True, slots=True)
class WhatIfBatchProjection:
    """Aggregate projection for a sequence of hypothetical workloads."""

    projections: tuple[WhatIfProjection, ...]
    total_reserved_usd: Decimal
    starting_spend_usd: Decimal
    projected_spend_usd: Decimal
    budget_usd: Decimal | None
    budget_headroom_usd: Decimal | None
    would_exceed_budget: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "projections": [projection.to_dict() for projection in self.projections],
            "cost": {
                "total_reserved_usd": _decimal_to_str(self.total_reserved_usd),
                "starting_spend_usd": _decimal_to_str(self.starting_spend_usd),
                "projected_spend_usd": _decimal_to_str(self.projected_spend_usd),
                "budget_usd": _optional_decimal_to_str(self.budget_usd),
                "budget_headroom_usd": _optional_decimal_to_str(self.budget_headroom_usd),
                "would_exceed_budget": self.would_exceed_budget,
            },
        }


class WhatIfSimulator:
    """Replay the production planner against hypothetical price and budget inputs.

    Observed state (provider health and cooldown, quota, model lockouts, prices, RunPod
    availability) is an explicit input; nothing is read from process-global tables.
    """

    def __init__(
        self,
        context: PlanningContext,
        *,
        price_overrides: PriceOverrides | None = None,
        budget_usd: object = None,
        current_spend_usd: object = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        mode: Literal["priority", "weighted"] = "priority",
        weights: RoutingWeights | None = None,
        lockouts: LockoutSnapshot | None = None,
        registry: ProviderRegistry | None = None,
    ) -> None:
        self._base_context = context
        self._price_overrides = dict(price_overrides or {})
        self._budget_usd = _optional_usd(budget_usd, "budget_usd")
        self._current_spend_usd = _usd_or_zero(current_spend_usd, "current_spend_usd")
        self._max_attempts = max_attempts
        self._mode: Literal["priority", "weighted"] = mode
        self._weights = weights if weights is not None else RoutingWeights()
        self._lockouts = lockouts if lockouts is not None else LockoutSnapshot()
        self._registry = registry if registry is not None else get_default_registry()

    @classmethod
    def from_inputs(
        cls,
        *,
        now: dt.datetime,
        providers: Iterable[ProviderInput],
        capability: Capability | None = None,
        availability_snapshot: AvailabilitySnapshot | None = None,
        availability_entries: Iterable[AvailabilityEntry] = (),
        quota_snapshot: QuotaSnapshot | None = None,
        lockouts: LockoutSnapshot | None = None,
        price_overrides: PriceOverrides | None = None,
        budget_usd: object = None,
        current_spend_usd: object = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        mode: Literal["priority", "weighted"] = "priority",
        weights: RoutingWeights | None = None,
    ) -> WhatIfSimulator:
        context = PlanningContext.replay(
            now=now,
            providers=providers,
            capability=capability,
            availability_snapshot=availability_snapshot,
            availability_entries=availability_entries,
            quota_snapshot=quota_snapshot,
        )
        return cls(
            context,
            price_overrides=price_overrides,
            budget_usd=budget_usd,
            current_spend_usd=current_spend_usd,
            max_attempts=max_attempts,
            mode=mode,
            weights=weights,
            lockouts=lockouts,
        )

    def simulate(
        self,
        request: RoutingRequest,
        *,
        payload: Mapping[str, Any] | None = None,
        capability: Capability | None = None,
        price_overrides: PriceOverrides | None = None,
        budget_usd: object = None,
        current_spend_usd: object = None,
        max_attempts: int | None = None,
        daily_tokens: int | None = None,
        quota_snapshot: QuotaSnapshot | None = None,
        own_pod_fit: OwnPodFit | None = None,
    ) -> WhatIfProjection:
        context = self._context_for(price_overrides)
        if quota_snapshot is not None:
            context = replace(context, quota_snapshot=quota_snapshot)
        resolved_capability = self._resolve_capability(capability, context)
        resolved_budget = self._resolve_budget(budget_usd)
        resolved_current_spend = self._resolve_current_spend(current_spend_usd)
        prong_comparison = _prong_comparison(
            context=context,
            daily_tokens=daily_tokens,
            quota_snapshot=quota_snapshot,
            own_pod_fit=own_pod_fit,
        )
        plan_payload = dict(payload or {})
        if request.stream:
            plan_payload.setdefault("stream", True)
        plan: ProductionRoutePlan | None
        eliminated: tuple[RouteElimination, ...]
        try:
            plan = build_production_plan(
                capability=resolved_capability,
                providers=_providers_for_plan(context),
                payload=plan_payload,
                operation=RoutingOperation.SYNC_INFERENCE,
                registry=self._registry,
                now=context.now,
                mode=self._mode,
                weights=self._weights,
                max_attempts=max_attempts if max_attempts is not None else self._max_attempts,
                context=context,
                lockouts=self._lockouts,
            )
        except NoExecutableRouteError as exc:
            plan = None
            eliminated = exc.eliminations
        else:
            eliminated = plan.eliminated
        attempt_costs = _attempt_costs(plan)
        reserved_usd = attempt_costs[0].upper_bound_usd if attempt_costs else Decimal("0.000000")
        projected_spend = _usd(resolved_current_spend + reserved_usd, "projected_spend_usd")
        budget_headroom = _budget_headroom(
            budget_usd=resolved_budget,
            projected_spend_usd=projected_spend,
        )
        return WhatIfProjection(
            plan=plan,
            eliminated=eliminated,
            attempt_costs=attempt_costs,
            reserved_usd=reserved_usd,
            current_spend_usd=resolved_current_spend,
            projected_spend_usd=projected_spend,
            budget_usd=resolved_budget,
            budget_headroom_usd=budget_headroom,
            would_exceed_budget=(
                None if budget_headroom is None else budget_headroom < Decimal("0")
            ),
            prong_comparison=prong_comparison,
        )

    def simulate_workloads(
        self,
        workloads: Iterable[WhatIfWorkload],
        *,
        price_overrides: PriceOverrides | None = None,
        budget_usd: object = None,
        current_spend_usd: object = None,
    ) -> WhatIfBatchProjection:
        resolved_budget = self._resolve_budget(budget_usd)
        running_spend = self._resolve_current_spend(current_spend_usd)
        starting_spend = running_spend
        projections: list[WhatIfProjection] = []

        for workload in workloads:
            projection = self.simulate(
                workload.request,
                payload=workload.payload,
                price_overrides=price_overrides,
                budget_usd=resolved_budget,
                current_spend_usd=running_spend,
            )
            projections.append(projection)
            running_spend = projection.projected_spend_usd

        total_reserved = _usd(
            sum(
                (projection.reserved_usd for projection in projections),
                Decimal("0"),
            ),
            "total_reserved_usd",
        )
        budget_headroom = _budget_headroom(
            budget_usd=resolved_budget,
            projected_spend_usd=running_spend,
        )
        return WhatIfBatchProjection(
            projections=tuple(projections),
            total_reserved_usd=total_reserved,
            starting_spend_usd=starting_spend,
            projected_spend_usd=running_spend,
            budget_usd=resolved_budget,
            budget_headroom_usd=budget_headroom,
            would_exceed_budget=(
                None if budget_headroom is None else budget_headroom < Decimal("0")
            ),
        )

    def _context_for(self, price_overrides: PriceOverrides | None) -> PlanningContext:
        merged_overrides: dict[str, object] = dict(self._price_overrides)
        if price_overrides is not None:
            merged_overrides.update(price_overrides)
        if not merged_overrides:
            return self._base_context
        return _context_with_price_overrides(self._base_context, merged_overrides)

    def _resolve_budget(self, budget_usd: object) -> Decimal | None:
        if budget_usd is None:
            return self._budget_usd
        return _optional_usd(budget_usd, "budget_usd")

    def _resolve_current_spend(self, current_spend_usd: object) -> Decimal:
        if current_spend_usd is None:
            return self._current_spend_usd
        return _usd(current_spend_usd, "current_spend_usd")

    @staticmethod
    def _resolve_capability(
        capability: Capability | None,
        context: PlanningContext,
    ) -> Capability:
        if capability is not None:
            return capability
        if context.capability is not None:
            return context.capability
        raise ValueError("capability must be supplied directly or through PlanningContext")


def _attempt_costs(plan: ProductionRoutePlan | None) -> tuple[ProviderCostProjection, ...]:
    if plan is None:
        return ()
    return tuple(
        ProviderCostProjection(
            provider_id=candidate.provider_id,
            attempt=attempt,
            estimate_usd=candidate.quote.estimate(),
            upper_bound_usd=candidate.quote.upper_bound(),
            pricing_kind=candidate.quote.pricing.kind,
            selected=attempt == 1,
        )
        for attempt, candidate in enumerate(plan.attempts, start=1)
    )


def _providers_for_plan(context: PlanningContext) -> tuple[Provider, ...]:
    """Typed provider snapshots with RunPod availability applied as explicit capacity state."""

    return tuple(_typed_provider(provider, context) for provider in context.providers)


def _typed_provider(provider: Mapping[str, Any], context: PlanningContext) -> Provider:
    document = _mutable_mapping(provider)
    document.setdefault("updated_at", context.now)
    typed = Provider.model_validate(document)
    if typed.provider_type.value != "pod_lease" or typed.config.get("capacity_available") is False:
        return typed
    if _capacity_unavailable(typed, context.availability_snapshot):
        return typed.model_copy(update={"config": {**typed.config, "capacity_available": False}})
    return typed


def _capacity_unavailable(provider: Provider, snapshot: AvailabilitySnapshot) -> bool:
    """True when the snapshot knows the pod's GPU slots and none is available."""

    config = provider.config
    datacenter = provider.region
    data_center_ids = config.get("data_center_ids") or config.get("dataCenterIds")
    if isinstance(data_center_ids, (list, tuple)) and data_center_ids:
        datacenter = str(data_center_ids[0])
    raw_count = config.get("gpu_count", 1)
    gpu_count = raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) else 1
    gpu_names: list[str] = []
    for key in _CAPACITY_GPU_KEYS:
        names = config.get(key)
        if isinstance(names, (list, tuple)):
            gpu_names.extend(str(name) for name in names)
    if datacenter is None or not gpu_names:
        return False
    answers = [
        snapshot.is_available(datacenter, name, provider.cloud_type or "SECURE", gpu_count)
        for name in gpu_names
    ]
    known = [answer for answer in answers if answer is not None]
    return bool(known) and not any(known)


def _prong_comparison(
    *,
    context: PlanningContext,
    daily_tokens: int | None,
    quota_snapshot: QuotaSnapshot | None,
    own_pod_fit: OwnPodFit | None,
) -> tuple[ProngOption, ...]:
    """Build the cross-prong cost comparison (plan Task 20).

    Rows are opt-in: the comparison is only computed when a projected daily
    token demand is supplied, and each row appears only when its inputs are
    supplied (``own_pod_fit``, ``quota_snapshot``, or a metered provider in the
    planning context).
    """

    if daily_tokens is None:
        return ()
    if isinstance(daily_tokens, bool) or daily_tokens <= 0:
        raise ValueError("daily_tokens must be a positive integer")
    rows: list[ProngOption] = []
    if own_pod_fit is not None:
        rows.append(_own_serve_row(own_pod_fit, daily_tokens))
    if quota_snapshot is not None:
        rows.append(_free_row(quota_snapshot, daily_tokens))
    metered = _metered_row(context)
    if metered is not None:
        rows.append(metered)
    return tuple(rows)


def _own_serve_row(fit: OwnPodFit, daily_tokens: int) -> ProngOption:
    options = [
        option
        for option in fit_options(
            fit.variant,
            gpu_types=fit.gpu_types,
            ttl_minutes=fit.ttl_minutes,
            cloud=fit.cloud,
        )
        if option.fit == "fits" and option.price_per_hour is not None
    ]
    if not options:
        raise ValueError("own_pod_fit has no fitting GPU option")
    best = min(options, key=lambda option: (option.price_per_hour, option.gpu_count))
    price = cast(Decimal, best.price_per_hour)
    daily_usd = _quantize_usd(price * 24, "own_pod_daily_usd")
    per_million = _quantize_usd(
        daily_usd * Decimal(1_000_000) / Decimal(daily_tokens),
        "own_pod_usd_per_million_tokens",
    )
    note = f"own pod {best.gpu_class} x{best.gpu_count} at ${price}/hr up 24h"
    return ProngOption(
        prong="own_serve",
        usd_per_million_tokens=per_million,
        coverage_pct=100.0,
        note=note,
    )


def _free_row(snapshot: QuotaSnapshot, daily_tokens: int) -> ProngOption:
    # Providers that share a pool_key draw on one quota, so the pool counts once (the
    # smallest remaining figure, the conservative one); a record without a pool_key is
    # its own pool.
    pools: dict[str, Decimal] = {}
    unkeyed = Decimal("0")
    for record in snapshot.records:
        if record.budget_units is None:
            continue
        left = record.budget_units - record.used_units
        if record.pool_key:
            pools[record.pool_key] = min(pools.get(record.pool_key, left), left)
        else:
            unkeyed += left
    remaining = max(sum(pools.values(), unkeyed), Decimal("0"))
    coverage_pct = min(100.0, float(remaining) / daily_tokens * 100.0)
    return ProngOption(
        prong="free",
        usd_per_million_tokens=Decimal("0.000000"),
        coverage_pct=coverage_pct,
        note=f"free pools cover {coverage_pct:.1f}% of {daily_tokens} tokens/day",
    )


def _metered_row(context: PlanningContext) -> ProngOption | None:
    best_rate: Decimal | None = None
    best_provider_id = ""
    for provider in context.providers:
        try:
            pricing = parse_pricing_model(provider)
        except TypeError, ValueError:
            continue
        if not isinstance(pricing, PerTokenPricing):
            continue
        blended = (pricing.per_million_input_tokens + pricing.per_million_output_tokens) / Decimal(
            2
        )
        if best_rate is None or blended < best_rate:
            best_rate = blended
            best_provider_id = _provider_id(provider)
    if best_rate is None:
        return None
    return ProngOption(
        prong="metered",
        usd_per_million_tokens=_quantize_usd(best_rate, "metered_usd_per_million_tokens"),
        coverage_pct=100.0,
        note=f"{best_provider_id} metered blended 50/50 prompt/completion",
    )


def _context_with_price_overrides(
    context: PlanningContext,
    price_overrides: PriceOverrides,
) -> PlanningContext:
    providers = tuple(
        _provider_with_price_override(provider, price_overrides) for provider in context.providers
    )
    return PlanningContext.replay(
        now=context.now,
        availability_snapshot=context.availability_snapshot,
        providers=providers,
        capability=context.capability,
        quota_snapshot=context.quota_snapshot,
    )


def _provider_with_price_override(
    provider: Mapping[str, Any],
    price_overrides: PriceOverrides,
) -> Mapping[str, Any]:
    provider_id = _provider_id(provider)
    override = price_overrides.get(provider_id)
    if override is None:
        return provider

    provider_copy = _mutable_mapping(provider)
    override_cost = _override_cost_mapping(provider_id, override)
    config = _provider_config(provider_copy)
    config["cost"] = _mutable_mapping(override_cost)
    return provider_copy


def _provider_config(provider: dict[str, Any]) -> dict[str, Any]:
    raw_config = provider.get("config")
    config = _mutable_mapping(raw_config) if isinstance(raw_config, Mapping) else {}
    provider["config"] = config
    return config


def _override_cost_mapping(provider_id: str, override: object) -> Mapping[str, Any]:
    if not isinstance(override, Mapping):
        raise ValueError(f"price override for provider {provider_id!r} must be a mapping")

    config = override.get("config")
    if isinstance(config, Mapping):
        config_cost = config.get("cost")
        if isinstance(config_cost, Mapping):
            return cast(Mapping[str, Any], config_cost)

    cost = override.get("cost")
    if isinstance(cost, Mapping):
        return cast(Mapping[str, Any], cost)

    return cast(Mapping[str, Any], override)


def _provider_id(provider: Mapping[str, Any]) -> str:
    value = provider.get("id")
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, str) and value:
        return value
    raise ValueError("provider must include a non-empty id")


def _mutable_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _thaw_value(value) for key, value in mapping.items()}


def _thaw_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _mutable_mapping(cast(Mapping[str, Any], value))
    if _is_sequence(value):
        return tuple(_thaw_value(item) for item in cast(Sequence[object], value))
    return value


def _is_sequence(value: object) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str))


def _budget_headroom(
    *,
    budget_usd: Decimal | None,
    projected_spend_usd: Decimal,
) -> Decimal | None:
    if budget_usd is None:
        return None
    return _signed_usd(budget_usd - projected_spend_usd, "budget_headroom_usd")


def _usd_or_zero(value: object, name: str) -> Decimal:
    if value is None:
        return Decimal("0.000000")
    return _usd(value, name)


def _optional_usd(value: object, name: str) -> Decimal | None:
    if value is None:
        return None
    return _usd(value, name)


def _usd(value: object, name: str) -> Decimal:
    decimal_value = _decimal(value, name)
    if decimal_value < 0:
        raise ValueError(f"{name} must be non-negative")
    return _quantize_usd(decimal_value, name)


def _signed_usd(value: object, name: str) -> Decimal:
    return _quantize_usd(_decimal(value, name), name)


def _decimal(value: object, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a decimal value")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal value") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{name} must be finite")
    return decimal_value


def _quantize_usd(value: Decimal, name: str) -> Decimal:
    try:
        return value.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"{name} is out of representable USD range: {value}") from exc


def _decimal_to_str(value: Decimal) -> str:
    return format(value, "f")


def _optional_decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return _decimal_to_str(value)


__all__ = [
    "AvailabilityEntry",
    "DEFAULT_MAX_ATTEMPTS",
    "OwnPodFit",
    "ProngOption",
    "ProviderCostProjection",
    "WhatIfBatchProjection",
    "WhatIfProjection",
    "WhatIfSimulator",
    "WhatIfWorkload",
]
