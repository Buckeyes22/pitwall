"""Tagged cost pricing models and compatibility estimators for Pitwall admission."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, DecimalException
from typing import Annotated, Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from pitwall.core.enums import CostMode
from pitwall.core.models import Capability

ProviderCost = Mapping[str, Any]

EstimatePayload = dict[str, Any]

_USD_QUANTUM = Decimal("0.000001")
_ONE_MILLION = Decimal(1_000_000)
_UNIT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

type CostConfidence = Literal["exact", "bounded", "estimated"]


@dataclass(frozen=True, slots=True)
class CostComponent:
    """One named, Decimal-authoritative component of a cost quote."""

    name: str
    unit: str
    rate: Decimal
    ceiling_rate: Decimal
    estimated_count: Decimal
    ceiling_count: Decimal
    estimate: Decimal
    ceiling: Decimal

    def __post_init__(self) -> None:
        _component_name(self.name, "name")
        _component_name(self.unit, "unit")
        for field_name in (
            "rate",
            "ceiling_rate",
            "estimated_count",
            "ceiling_count",
            "estimate",
            "ceiling",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, Decimal):
                raise TypeError(f"{field_name} must be Decimal")
            if not value.is_finite():
                raise ValueError(f"{field_name} must be finite")
            if value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.ceiling_count < self.estimated_count:
            raise ValueError("ceiling_count must be greater than or equal to estimated_count")
        if self.ceiling_rate < self.rate:
            raise ValueError("ceiling_rate must be greater than or equal to rate")
        if self.ceiling < self.estimate:
            raise ValueError("ceiling must be greater than or equal to estimate")
        if self.estimate != _usd(self.estimate) or self.ceiling != _usd(self.ceiling):
            raise ValueError("component amounts must use the six-decimal USD quantum")

    def model_dump(self, *, mode: Literal["python", "json"] = "python") -> dict[str, str | Decimal]:
        data: dict[str, str | Decimal] = {
            "name": self.name,
            "unit": self.unit,
            "rate": self.rate,
            "ceiling_rate": self.ceiling_rate,
            "count": self.estimated_count,
            "ceiling_count": self.ceiling_count,
            "estimate": self.estimate,
            "ceiling": self.ceiling,
        }
        if mode == "python":
            return data
        if mode == "json":
            return {
                key: str(value) if isinstance(value, Decimal) else value
                for key, value in data.items()
            }
        raise ValueError(f"unsupported dump mode: {mode!r}")

    def to_serializable_dict(self) -> dict[str, str]:
        return {
            key: str(value) if isinstance(value, Decimal) else value
            for key, value in self.model_dump().items()
        }


@runtime_checkable
class PricingModelProtocol(Protocol):
    """Uniform interface implemented by every tagged pricing variant."""

    def estimate(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> Decimal: ...

    def upper_bound(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> Decimal: ...

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]: ...


class _PricingBase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ZeroOrEnergyPricing(_PricingBase):
    """Zero-dollar pricing with an optional electricity estimate."""

    kind: Literal["zero"] = "zero"
    watts: Decimal | None = None
    usd_per_kwh: Decimal | None = None

    @field_validator("watts", "usd_per_kwh", mode="before")
    @classmethod
    def _validate_energy_decimal(cls, value: object, info: Any) -> Decimal | None:
        return _optional_non_negative_decimal(value, info.field_name)

    def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=False)

    def upper_bound(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=True)

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]:
        if self.watts is None or self.usd_per_kwh is None:
            return (_cost_component("zero", "request", Decimal("0"), Decimal("1")),)
        raw_seconds = payload.get("expected_seconds")
        seconds = (
            _non_negative_decimal(raw_seconds, "expected_seconds")
            if raw_seconds is not None
            else _worst_case_seconds(capability)
        )
        ceiling_seconds = _worst_case_seconds(capability)
        _validate_count_bound(seconds, ceiling_seconds, "capability execution timeout")
        kilowatt_hours = self.watts * seconds / Decimal(3600) / Decimal(1000)
        ceiling_kilowatt_hours = self.watts * ceiling_seconds / Decimal(3600) / Decimal(1000)
        return (
            _cost_component(
                "energy",
                "kilowatt_hour",
                self.usd_per_kwh,
                kilowatt_hours,
                ceiling_kilowatt_hours,
            ),
        )


class GpuHourPricing(_PricingBase):
    """Current RunPod GPU active-second pricing, explicitly tagged.

    Existing provider records store the GPU-hour-derived active rate as
    ``per_second_active``.  Keeping this field name preserves the current exact
    estimate path while making the shape explicit.
    """

    kind: Literal["gpu_hour"] = "gpu_hour"
    per_second_active: Decimal

    @field_validator("per_second_active", mode="before")
    @classmethod
    def _validate_per_second_active(cls, value: object) -> Decimal:
        return _non_negative_decimal(value, "per_second_active")

    def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=False)

    def upper_bound(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=True)

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]:
        del payload
        seconds = _worst_case_seconds(capability)
        return (_cost_component("active_execution", "second", self.per_second_active, seconds),)


class PerRequestPricing(_PricingBase):
    """Flat per-invocation pricing used by existing public endpoints."""

    kind: Literal["per_request"] = "per_request"
    per_request: Decimal

    @field_validator("per_request", mode="before")
    @classmethod
    def _validate_per_request(cls, value: object) -> Decimal:
        return _non_negative_decimal(value, "per_request")

    def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=False)

    def upper_bound(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=True)

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]:
        del capability
        if "request_count" in payload or "max_requests" in payload:
            raise ValueError(
                "per_request pricing covers exactly one admitted invocation; "
                "request_count and max_requests are not accepted as client-supplied caps"
            )
        return (
            _cost_component(
                "requests",
                "request",
                self.per_request,
                Decimal("1"),
            ),
        )


class PerSecondPricing(_PricingBase):
    """Per-second compute pricing with an optional spot/bid ceiling."""

    kind: Literal["per_second"] = "per_second"
    rate_per_second: Decimal
    bid_rate_per_second: Decimal | None = None

    @field_validator("rate_per_second", mode="before")
    @classmethod
    def _validate_rate_per_second(cls, value: object) -> Decimal:
        return _non_negative_decimal(value, "rate_per_second")

    @field_validator("bid_rate_per_second", mode="before")
    @classmethod
    def _validate_bid_rate_per_second(cls, value: object) -> Decimal | None:
        return _optional_non_negative_decimal(value, "bid_rate_per_second")

    def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=False)

    def upper_bound(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=True)

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]:
        del payload
        ceiling_rate = self.rate_per_second
        if self.bid_rate_per_second is not None:
            ceiling_rate = max(ceiling_rate, self.bid_rate_per_second)
        seconds = _worst_case_seconds(capability)
        return (
            _cost_component(
                "active_execution",
                "second",
                self.rate_per_second,
                seconds,
                seconds,
                ceiling_rate=ceiling_rate,
            ),
        )


class InputPriceTier(BaseModel):
    """Rates that apply when a request's input exceeds ``above_input_tokens``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    above_input_tokens: int = Field(gt=0)
    per_million_input_tokens: Decimal
    per_million_output_tokens: Decimal
    per_million_cached_input_tokens: Decimal | None = None

    @field_validator(
        "per_million_input_tokens",
        "per_million_output_tokens",
        "per_million_cached_input_tokens",
        mode="before",
    )
    @classmethod
    def _validate_rates(cls, value: object) -> Decimal | None:
        return None if value is None else _non_negative_decimal(value, "input tier rate")


class PerTokenPricing(_PricingBase):
    """Split prompt/completion token pricing with max-token upper bounds."""

    kind: Literal["per_token"] = "per_token"
    per_million_input_tokens: Decimal
    per_million_output_tokens: Decimal
    per_million_cached_input_tokens: Decimal | None = None
    input_tiers: tuple[InputPriceTier, ...] = ()
    default_max_output_tokens: int | None = Field(default=None, gt=0)

    @field_validator("per_million_input_tokens", mode="before")
    @classmethod
    def _validate_input_rate(cls, value: object) -> Decimal:
        return _non_negative_decimal(value, "per_million_input_tokens")

    @field_validator("per_million_output_tokens", mode="before")
    @classmethod
    def _validate_output_rate(cls, value: object) -> Decimal:
        return _non_negative_decimal(value, "per_million_output_tokens")

    @field_validator("per_million_cached_input_tokens", mode="before")
    @classmethod
    def _validate_cached_rate(cls, value: object) -> Decimal | None:
        return (
            None
            if value is None
            else _non_negative_decimal(value, "per_million_cached_input_tokens")
        )

    @field_validator("input_tiers")
    @classmethod
    def _validate_tiers(cls, value: tuple[InputPriceTier, ...]) -> tuple[InputPriceTier, ...]:
        thresholds = [tier.above_input_tokens for tier in value]
        if thresholds != sorted(set(thresholds)):
            raise ValueError("input_tiers must ascend by above_input_tokens without duplicates")
        return value

    def _rates(self, input_tokens: Decimal) -> tuple[Decimal, Decimal, Decimal | None]:
        rates = (
            self.per_million_input_tokens,
            self.per_million_output_tokens,
            self.per_million_cached_input_tokens,
        )
        for tier in self.input_tiers:
            if input_tokens > tier.above_input_tokens:
                rates = (
                    tier.per_million_input_tokens,
                    tier.per_million_output_tokens,
                    tier.per_million_cached_input_tokens,
                )
        return rates

    def _ceiling_rates(self) -> tuple[Decimal, Decimal]:
        inputs = [
            self.per_million_input_tokens,
            *(tier.per_million_input_tokens for tier in self.input_tiers),
        ]
        outputs = [
            self.per_million_output_tokens,
            *(tier.per_million_output_tokens for tier in self.input_tiers),
        ]
        return max(inputs), max(outputs)

    def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        input_tokens, output_tokens = _estimate_tokens(payload, capability)
        input_rate, output_rate, cached_rate = self._rates(input_tokens)
        cached = (
            _cached_token_count(payload, input_tokens) if cached_rate is not None else Decimal(0)
        )
        components = self._components(
            input_tokens - cached,
            input_tokens - cached,
            output_tokens,
            output_tokens,
            rates=(input_rate, output_rate, cached_rate),
            ceiling_rates=(input_rate, output_rate),
            cached_tokens=cached,
        )
        return _component_total(components, ceiling=False)

    def upper_bound(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=True)

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]:
        input_tokens, output_tokens = _estimate_tokens(payload, capability)
        input_ceiling = _input_token_upper_bound(payload, input_tokens)
        output_ceiling = output_token_ceiling(payload, self.default_max_output_tokens)
        _validate_count_bound(output_tokens, output_ceiling, "max_output_tokens")
        input_rate, output_rate, _cached = self._rates(input_tokens)
        return self._components(
            input_tokens,
            input_ceiling,
            output_tokens,
            output_ceiling,
            rates=(input_rate, output_rate, None),
            ceiling_rates=self._ceiling_rates(),
            cached_tokens=Decimal(0),
        )

    def _components(
        self,
        input_tokens: Decimal,
        input_ceiling: Decimal,
        output_tokens: Decimal,
        output_ceiling: Decimal,
        *,
        rates: tuple[Decimal, Decimal, Decimal | None] | None = None,
        ceiling_rates: tuple[Decimal, Decimal] | None = None,
        cached_tokens: Decimal = Decimal(0),
    ) -> tuple[CostComponent, ...]:
        base_in, base_out, base_cached = rates or (
            self.per_million_input_tokens,
            self.per_million_output_tokens,
            None,
        )
        ceil_in, ceil_out = ceiling_rates or (base_in, base_out)
        input_rate = base_in / _ONE_MILLION
        output_rate = base_out / _ONE_MILLION
        ceiling_input_rate = ceil_in / _ONE_MILLION
        ceiling_output_rate = ceil_out / _ONE_MILLION
        components: list[CostComponent] = [
            _cost_component(
                "input_tokens",
                "input_token",
                input_rate,
                input_tokens,
                input_ceiling,
                ceiling_rate=ceiling_input_rate,
            )
        ]
        products = [(input_rate, input_tokens), (output_rate, output_tokens)]
        if base_cached is not None and cached_tokens > 0:
            cached_rate = base_cached / _ONE_MILLION
            components.append(
                _cost_component(
                    "cached_input_tokens", "input_token", cached_rate, cached_tokens, cached_tokens
                )
            )
            products.append((cached_rate, cached_tokens))
        estimate_total = _sum_products_usd(*products)
        output_estimate = estimate_total - sum(
            (component.estimate for component in components), Decimal(0)
        )
        output_ceiling_amount = _multiply_usd(
            ceiling_output_rate, output_ceiling, rounding=ROUND_CEILING
        )
        components.append(
            CostComponent(
                name="output_tokens",
                unit="output_token",
                rate=output_rate,
                ceiling_rate=ceiling_output_rate,
                estimated_count=output_tokens,
                ceiling_count=output_ceiling,
                # Allocate aggregate micro-dollar rounding to the last component.
                # This keeps named components additive without changing legacy
                # token totals, which historically rounded only after summing.
                estimate=output_estimate,
                ceiling=max(output_estimate, output_ceiling_amount),
            )
        )
        return tuple(components)


class PerVmSecondPricing(_PricingBase):
    """Flat VM-second pricing for VM-style providers."""

    kind: Literal["per_vm_second"] = "per_vm_second"
    rate_per_second: Decimal

    @field_validator("rate_per_second", mode="before")
    @classmethod
    def _validate_rate_per_second(cls, value: object) -> Decimal:
        return _non_negative_decimal(value, "rate_per_second")

    def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=False)

    def upper_bound(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=True)

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]:
        del payload
        seconds = _worst_case_seconds(capability)
        return (_cost_component("vm_execution", "vm_second", self.rate_per_second, seconds),)


class ActiveIdlePricing(_PricingBase):
    """Active execution plus idle standing cost for a bounded endpoint window."""

    kind: Literal["active_idle"] = "active_idle"
    active_rate_per_second: Decimal
    idle_rate_per_second: Decimal
    minimum_billing_increment_seconds: Decimal = Decimal("1")
    scale_to_zero: bool = False
    max_idle_seconds: Decimal | None = None

    @field_validator(
        "active_rate_per_second",
        "idle_rate_per_second",
        mode="before",
    )
    @classmethod
    def _validate_strict_rate(cls, value: object, info: Any) -> Decimal:
        return _strict_non_negative_decimal(value, info.field_name)

    @field_validator("minimum_billing_increment_seconds", mode="before")
    @classmethod
    def _validate_billing_increment(cls, value: object) -> Decimal:
        increment = _strict_non_negative_decimal(value, "minimum_billing_increment_seconds")
        if increment <= 0:
            raise ValueError("minimum_billing_increment_seconds must be positive")
        return increment

    @field_validator("max_idle_seconds", mode="before")
    @classmethod
    def _validate_max_idle_seconds(cls, value: object) -> Decimal | None:
        if value is None:
            return None
        return _strict_non_negative_decimal(value, "max_idle_seconds")

    @field_validator("scale_to_zero", mode="before")
    @classmethod
    def _validate_scale_to_zero(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise ValueError("scale_to_zero must be boolean")
        return value

    def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(
            self._components(capability, payload, require_bound=False),
            ceiling=False,
        )

    def upper_bound(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=True)

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]:
        return self._components(capability, payload, require_bound=True)

    def _components(
        self,
        capability: Capability,
        payload: EstimatePayload,
        *,
        require_bound: bool,
    ) -> tuple[CostComponent, ...]:
        if "max_active_seconds" in payload:
            raise ValueError(
                "max_active_seconds must come from the capability execution timeout, "
                "not the client payload"
            )
        if "max_idle_seconds" in payload:
            raise ValueError(
                "max_idle_seconds must be provider/operator pricing configuration, "
                "not the client payload"
            )
        active_seconds = _optional_strict_count(
            payload,
            "active_seconds",
            default=_worst_case_seconds(capability),
        )
        max_active_seconds = _worst_case_seconds(capability)
        _validate_count_bound(active_seconds, max_active_seconds, "max_active_seconds")

        idle_seconds = _optional_strict_count(payload, "idle_seconds", default=Decimal("0"))
        max_idle_seconds = self.max_idle_seconds
        if max_idle_seconds is None:
            if self.scale_to_zero or self.idle_rate_per_second == 0:
                max_idle_seconds = Decimal("0")
            elif require_bound:
                raise ValueError(
                    "max_idle_seconds pricing configuration is required for active_idle "
                    "pricing without an immediate scale-to-zero guarantee"
                )
            else:
                max_idle_seconds = idle_seconds
        _validate_count_bound(idle_seconds, max_idle_seconds, "max_idle_seconds")

        active_billable = _billable_seconds(
            active_seconds,
            self.minimum_billing_increment_seconds,
        )
        max_active_billable = _billable_seconds(
            max_active_seconds,
            self.minimum_billing_increment_seconds,
        )
        idle_billable = _billable_seconds(
            idle_seconds,
            self.minimum_billing_increment_seconds,
        )
        max_idle_billable = _billable_seconds(
            max_idle_seconds,
            self.minimum_billing_increment_seconds,
        )
        return (
            _cost_component(
                "active_execution",
                "second",
                self.active_rate_per_second,
                active_billable,
                max_active_billable,
            ),
            _cost_component(
                "idle_standing",
                "second",
                self.idle_rate_per_second,
                idle_billable,
                max_idle_billable,
            ),
        )


class PerUnitPricing(_PricingBase):
    """Provider-defined per-run units with explicit rate and bounded count."""

    kind: Literal["per_unit"] = "per_unit"
    unit: str
    rate_per_unit: Decimal

    @field_validator("unit", mode="before")
    @classmethod
    def _validate_unit(cls, value: object) -> str:
        return _unit_name(value)

    @field_validator("rate_per_unit", mode="before")
    @classmethod
    def _validate_rate(cls, value: object) -> Decimal:
        return _strict_non_negative_decimal(value, "rate_per_unit")

    def estimate(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=False)

    def upper_bound(self, capability: Capability, payload: EstimatePayload) -> Decimal:
        return _component_total(self.quote_components(capability, payload), ceiling=True)

    def quote_components(
        self,
        capability: Capability,
        payload: EstimatePayload,
    ) -> tuple[CostComponent, ...]:
        del capability
        if "unit_count" not in payload:
            raise ValueError("unit_count is required for per_unit pricing")
        count = _strict_non_negative_decimal(payload["unit_count"], "unit_count")
        raw_ceiling = payload.get("max_unit_count", count)
        ceiling_count = _strict_positive_decimal(raw_ceiling, "max_unit_count")
        _validate_count_bound(count, ceiling_count, "max_unit_count")
        return (
            _cost_component(
                "run_units",
                self.unit,
                self.rate_per_unit,
                count,
                ceiling_count,
            ),
        )


type TaggedPricingModel = (
    ActiveIdlePricing
    | GpuHourPricing
    | PerRequestPricing
    | PerSecondPricing
    | PerTokenPricing
    | PerUnitPricing
    | PerVmSecondPricing
    | ZeroOrEnergyPricing
)
type PricingModel = Annotated[TaggedPricingModel, Field(discriminator="kind")]

_PRICING_MODEL_ADAPTER: TypeAdapter[TaggedPricingModel] = TypeAdapter(PricingModel)
_PRICING_MODEL_CLASSES = (
    ActiveIdlePricing,
    GpuHourPricing,
    PerRequestPricing,
    PerSecondPricing,
    PerTokenPricing,
    PerUnitPricing,
    PerVmSecondPricing,
    ZeroOrEnergyPricing,
)


@dataclass(frozen=True)
class CostQuote:
    """One structured, Decimal-authoritative quote bound to a request."""

    pricing: TaggedPricingModel
    capability: Capability
    payload: EstimatePayload

    @property
    def model(self) -> str:
        return self.pricing.kind

    @property
    def components(self) -> tuple[CostComponent, ...]:
        return self.pricing.quote_components(self.capability, self.payload)

    @property
    def currency(self) -> Literal["USD"]:
        return "USD"

    @property
    def confidence(self) -> CostConfidence:
        return _quote_confidence(self.pricing, self.components)

    @property
    def provenance(self) -> str:
        if isinstance(self.pricing, ZeroOrEnergyPricing) and self.pricing.watts is not None:
            return "operator_energy_config"
        return "provider_config"

    @property
    def assumptions(self) -> tuple[str, ...]:
        return _quote_assumptions(self.pricing)

    def estimate(self) -> Decimal:
        return self.pricing.estimate(self.capability, self.payload)

    def upper_bound(self) -> Decimal:
        return self.pricing.upper_bound(self.capability, self.payload)

    def model_dump(self, *, mode: Literal["python", "json"] = "python") -> dict[str, object]:
        if mode not in {"python", "json"}:
            raise ValueError(f"unsupported dump mode: {mode!r}")
        components = self.components
        estimate: Decimal | str = _component_total(components, ceiling=False)
        ceiling: Decimal | str = _component_total(components, ceiling=True)
        if mode == "json":
            estimate = str(estimate)
            ceiling = str(ceiling)
        return {
            "model": self.model,
            "components": [component.model_dump(mode=mode) for component in components],
            "estimate": estimate,
            "ceiling": ceiling,
            "confidence": _quote_confidence(self.pricing, components),
            "provenance": self.provenance,
            "currency": self.currency,
            "assumptions": list(self.assumptions),
        }

    def to_serializable_dict(self) -> dict[str, object]:
        """Return the one public quote shape with Decimal values as strings."""

        return self.model_dump(mode="json")


@runtime_checkable
class CostEstimator(Protocol):
    """Protocol that every compatibility estimator must satisfy."""

    def estimate(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal: ...

    def upper_bound(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal: ...


def parse_pricing_model(
    provider_cost: object,
    *,
    cost_mode: CostMode | str | None = None,
) -> TaggedPricingModel:
    """Return a tagged pricing model from tagged or legacy provider cost data."""

    if isinstance(provider_cost, _PRICING_MODEL_CLASSES):
        return provider_cost

    cost = _cost_mapping(provider_cost)
    if "kind" in cost or "model" in cost:
        return _tagged_pricing_model(cost)

    mode = _required_cost_mode(cost_mode)
    if mode == CostMode.ZERO:
        return ZeroOrEnergyPricing(
            watts=_optional_non_negative_decimal(cost.get("watts"), "watts"),
            usd_per_kwh=_optional_non_negative_decimal(
                cost.get("usd_per_kwh"),
                "usd_per_kwh",
            ),
        )
    if mode == CostMode.PER_SECOND:
        return GpuHourPricing(
            per_second_active=_required_non_negative_decimal(
                cost,
                "per_second_active",
            )
        )
    if mode == CostMode.PER_REQUEST:
        return PerRequestPricing(
            per_request=_required_non_negative_decimal(
                cost,
                "per_request",
            )
        )
    if mode == CostMode.PER_TOKEN:
        return PerTokenPricing(
            per_million_input_tokens=_required_non_negative_decimal(
                cost,
                "per_million_input_tokens",
            ),
            per_million_output_tokens=_required_non_negative_decimal(
                cost,
                "per_million_output_tokens",
            ),
        )
    raise ValueError(f"unsupported cost_mode: {cost_mode!r}")


def quote_cost(
    *,
    capability: Capability,
    provider_cost: object,
    payload: EstimatePayload,
) -> CostQuote:
    """Bind a tagged pricing model to one capability/payload for admission."""

    return CostQuote(
        pricing=parse_pricing_model(provider_cost, cost_mode=capability.cost_mode),
        capability=capability,
        payload=payload,
    )


class PerSecondEstimator:
    """Estimate cost for Pods and queue-based Serverless billed by container-second.

    Uses the capability's ``execution_timeout_ms`` as the worst-case runtime
    and multiplies by the provider's ``per_second_active`` rate.
    """

    def estimate(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal:
        return self._pricing(provider_cost).estimate(capability, payload)

    def upper_bound(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal:
        return self._pricing(provider_cost).upper_bound(capability, payload)

    @staticmethod
    def _pricing(provider_cost: object) -> TaggedPricingModel:
        return parse_pricing_model(provider_cost, cost_mode=CostMode.PER_SECOND)


class PerRequestEstimator:
    """Estimate cost for Public Endpoints with flat per-invocation pricing."""

    def estimate(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal:
        return self._pricing(provider_cost).estimate(capability, payload)

    def upper_bound(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal:
        return self._pricing(provider_cost).upper_bound(capability, payload)

    @staticmethod
    def _pricing(provider_cost: object) -> TaggedPricingModel:
        return parse_pricing_model(provider_cost, cost_mode=CostMode.PER_REQUEST)


class PerTokenEstimator:
    """Estimate cost for OpenAI-compatible endpoints.

    Reads ``per_million_input_tokens`` and ``per_million_output_tokens``
    from the provider cost dict and estimates token usage from the payload.
    """

    def estimate(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal:
        return self._pricing(provider_cost).estimate(capability, payload)

    def upper_bound(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal:
        return self._pricing(provider_cost).upper_bound(capability, payload)

    @staticmethod
    def _pricing(provider_cost: object) -> TaggedPricingModel:
        return parse_pricing_model(provider_cost, cost_mode=CostMode.PER_TOKEN)

    @staticmethod
    def _estimate_tokens(
        payload: EstimatePayload,
        capability: Capability,
    ) -> tuple[Decimal, Decimal]:
        """Return (input_tokens, output_tokens) estimate.

        If the payload includes explicit token counts, use them.
        Otherwise fall back to heuristic estimates based on payload size
        and capability defaults.
        """
        return _estimate_tokens(payload, capability)


class ZeroEstimator:
    def estimate(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal:
        return self._pricing(provider_cost).estimate(capability, payload)

    def upper_bound(
        self,
        capability: Capability,
        provider_cost: ProviderCost,
        payload: EstimatePayload,
    ) -> Decimal:
        return self._pricing(provider_cost).upper_bound(capability, payload)

    @staticmethod
    def _pricing(provider_cost: object) -> TaggedPricingModel:
        return parse_pricing_model(provider_cost, cost_mode=CostMode.ZERO)


_REGISTRY: dict[CostMode, CostEstimator] = {
    CostMode.PER_SECOND: PerSecondEstimator(),
    CostMode.PER_REQUEST: PerRequestEstimator(),
    CostMode.PER_TOKEN: PerTokenEstimator(),
    CostMode.ZERO: ZeroEstimator(),
}


def get_estimator(mode: CostMode | str) -> CostEstimator:
    """Return the :class:`CostEstimator` for *mode*.

    Raises :class:`ValueError` for unknown modes.
    """
    normalized_mode = _required_cost_mode(mode)
    estimator = _REGISTRY.get(normalized_mode)
    if estimator is None:
        raise ValueError(f"unsupported cost_mode: {mode!r}")
    return estimator


def _tagged_pricing_model(cost: Mapping[str, Any]) -> TaggedPricingModel:
    data = dict(cost)
    model = data.pop("model", None)
    if "kind" not in data and model is not None:
        data["kind"] = model
    pricing_model: TaggedPricingModel = _PRICING_MODEL_ADAPTER.validate_python(data)
    return pricing_model


def _required_cost_mode(cost_mode: CostMode | str | None) -> CostMode:
    if cost_mode is None:
        raise ValueError("legacy provider cost requires cost_mode or tagged pricing kind")
    try:
        return CostMode(cost_mode)
    except ValueError as exc:
        raise ValueError(f"unsupported cost_mode: {cost_mode!r}") from exc


def _worst_case_seconds(capability: Capability) -> Decimal:
    return Decimal(capability.defaults.execution_timeout_ms) / Decimal(1_000)


def _cost_component(
    name: str,
    unit: str,
    rate: Decimal,
    estimated_count: Decimal,
    ceiling_count: Decimal | None = None,
    *,
    ceiling_rate: Decimal | None = None,
) -> CostComponent:
    upper_count = estimated_count if ceiling_count is None else ceiling_count
    upper_rate = rate if ceiling_rate is None else ceiling_rate
    return CostComponent(
        name=name,
        unit=unit,
        rate=rate,
        ceiling_rate=upper_rate,
        estimated_count=estimated_count,
        ceiling_count=upper_count,
        estimate=_multiply_usd(rate, estimated_count),
        ceiling=_multiply_usd(upper_rate, upper_count, rounding=ROUND_CEILING),
    )


def _component_total(components: tuple[CostComponent, ...], *, ceiling: bool) -> Decimal:
    total = Decimal("0")
    try:
        for component in components:
            total += component.ceiling if ceiling else component.estimate
    except DecimalException as exc:
        raise ValueError("cost estimate is out of representable USD range") from exc
    return _usd(total)


def _multiply_usd(
    rate: Decimal,
    count: Decimal,
    *,
    rounding: str = ROUND_HALF_UP,
) -> Decimal:
    try:
        return _usd(rate * count, rounding=rounding)
    except DecimalException as exc:
        raise ValueError("cost estimate is out of representable USD range") from exc


def _sum_products_usd(
    *pairs: tuple[Decimal, Decimal],
    rounding: str = ROUND_HALF_UP,
) -> Decimal:
    try:
        total = sum((rate * count for rate, count in pairs), start=Decimal("0"))
    except DecimalException as exc:
        raise ValueError("cost estimate is out of representable USD range") from exc
    return _usd(total, rounding=rounding)


def _optional_strict_count(
    payload: Mapping[str, Any],
    key: str,
    *,
    default: Decimal,
) -> Decimal:
    raw = payload.get(key, _MISSING)
    if raw is _MISSING:
        return default
    return _strict_non_negative_decimal(raw, key)


def _validate_count_bound(count: Decimal, ceiling_count: Decimal, name: str) -> None:
    if ceiling_count < count:
        raise ValueError(f"{name} must be greater than or equal to the estimated count")


def _billable_seconds(seconds: Decimal, increment: Decimal) -> Decimal:
    if seconds == 0:
        return seconds
    try:
        increments = (seconds / increment).to_integral_value(rounding=ROUND_CEILING)
        return increments * increment
    except DecimalException as exc:
        raise ValueError("billable seconds are out of representable range") from exc


def _strict_non_negative_decimal(raw_value: object, name: str) -> Decimal:
    if isinstance(raw_value, float):
        raise ValueError(f"{name} must be a Decimal, decimal string, or integer")
    return _non_negative_decimal(raw_value, name)


def _strict_positive_decimal(raw_value: object, name: str) -> Decimal:
    value = _strict_non_negative_decimal(raw_value, name)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _unit_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("unit must be a string")
    unit = value.strip()
    if not _UNIT_NAME_RE.fullmatch(unit):
        raise ValueError("unit must match ^[a-z][a-z0-9_-]{0,63}$")
    return unit


def _component_name(value: object, name: str) -> str:
    if not isinstance(value, str) or not _UNIT_NAME_RE.fullmatch(value):
        raise ValueError(f"{name} must match ^[a-z][a-z0-9_-]{{0,63}}$")
    return value


def _quote_confidence(
    pricing: TaggedPricingModel,
    components: tuple[CostComponent, ...],
) -> CostConfidence:
    if isinstance(pricing, ZeroOrEnergyPricing) and pricing.watts is not None:
        return "estimated"
    if isinstance(pricing, (PerRequestPricing, PerUnitPricing)) and all(
        item.estimated_count == item.ceiling_count for item in components
    ):
        return "exact"
    if isinstance(pricing, ZeroOrEnergyPricing):
        return "exact"
    return "bounded"


def _quote_assumptions(pricing: TaggedPricingModel) -> tuple[str, ...]:
    if isinstance(pricing, ActiveIdlePricing):
        scale = (
            "scale-to-zero enabled" if pricing.scale_to_zero else "standing endpoint remains idle"
        )
        return (
            "active duration is bounded by capability execution_timeout_ms",
            "idle duration is bounded by provider/operator pricing configuration",
            f"durations round up to {pricing.minimum_billing_increment_seconds} second increments",
            scale,
        )
    if isinstance(pricing, PerTokenPricing):
        return (
            "input tokens are explicit or estimated from request bytes or text",
            "max_output_tokens bounds completion spend",
            "aggregate micro-dollar rounding is allocated to the output component",
        )
    if isinstance(pricing, PerUnitPricing):
        return ("unit_count is explicit, provider-enforced, and never inferred from payload text",)
    if isinstance(pricing, PerRequestPricing):
        return ("one admitted invocation is billed as exactly one request",)
    if isinstance(pricing, (GpuHourPricing, PerSecondPricing, PerVmSecondPricing)):
        return ("capability execution_timeout_ms is the billable duration bound",)
    if pricing.watts is not None:
        return ("energy cost uses configured watts and USD per kilowatt-hour",)
    return ("no monetary charge is configured",)


def _cost_mapping(provider_cost: object) -> Mapping[str, Any]:
    """Return the provider's cost map from flat, nested, or model-like inputs."""

    if isinstance(provider_cost, Mapping):
        cost = provider_cost.get("cost")
        if isinstance(cost, Mapping):
            return cost
        config = provider_cost.get("config")
        if isinstance(config, Mapping):
            config_cost = config.get("cost")
            if isinstance(config_cost, Mapping):
                return config_cost
        return provider_cost

    cost = getattr(provider_cost, "cost", None)
    if isinstance(cost, Mapping):
        return cost

    config = getattr(provider_cost, "config", None)
    if isinstance(config, Mapping):
        config_cost = config.get("cost")
        if isinstance(config_cost, Mapping):
            return config_cost

    raise ValueError("provider cost must be a mapping or expose a mapping 'cost'")


def _required_non_negative_decimal(provider_cost: object, key: str) -> Decimal:
    cost = _cost_mapping(provider_cost)
    if key not in cost:
        raise ValueError(f"provider cost missing required key {key!r}")

    return _non_negative_decimal(cost[key], f"provider cost {key!r}")


def _non_negative_decimal(raw_value: object, name: str) -> Decimal:
    if isinstance(raw_value, bool):
        raise ValueError(f"{name} must be a decimal value")

    try:
        value = Decimal(str(raw_value))
    except (DecimalException, ValueError) as exc:
        raise ValueError(f"{name} must be a decimal value") from exc

    if not value.is_finite():
        raise ValueError(f"{name} must be finite")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _optional_non_negative_decimal(raw_value: object, name: str) -> Decimal | None:
    if raw_value is None:
        return None
    return _non_negative_decimal(raw_value, name)


_MISSING = object()


def _first_present(payload: Mapping[str, Any], *keys: str) -> object:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return _MISSING


def _token_count(payload: EstimatePayload, *keys: str) -> object:
    token_count = _first_present(payload, *keys)
    if token_count is not _MISSING:
        return token_count

    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        return _first_present(usage, *keys)
    return _MISSING


def _estimate_tokens(
    payload: EstimatePayload,
    capability: Capability,
) -> tuple[Decimal, Decimal]:
    return _estimate_input_token_count(payload), _estimate_output_token_count(payload)


def _estimate_input_token_count(payload: EstimatePayload) -> Decimal:
    input_tokens = _token_count(payload, "input_tokens", "prompt_tokens")
    observed_estimate = _estimate_input_tokens(payload)
    if input_tokens is _MISSING:
        return observed_estimate
    declared = _non_negative_decimal(input_tokens, "input_tokens")
    return max(declared, observed_estimate)


def _estimate_output_token_count(payload: EstimatePayload) -> Decimal:
    output_tokens = _token_count(payload, "output_tokens", "completion_tokens")
    if output_tokens is not _MISSING:
        return _non_negative_decimal(output_tokens, "output_tokens")

    max_output_tokens = _first_present(
        payload,
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "max_new_tokens",
    )
    if max_output_tokens is _MISSING:
        return Decimal(256)
    return _non_negative_decimal(max_output_tokens, "max_output_tokens")


def _estimate_output_token_upper_bound(payload: Mapping[str, Any]) -> Decimal:
    max_output_tokens = _first_present(
        payload,
        "max_tokens",
        "max_output_tokens",
        "max_completion_tokens",
        "max_new_tokens",
    )
    if max_output_tokens is _MISSING:
        raise ValueError("max_output_tokens is required for per-token upper_bound")
    maximum = _non_negative_decimal(max_output_tokens, "max_output_tokens")
    if maximum <= 0:
        raise ValueError("max_output_tokens must be positive for bounded admission")
    return maximum


_MAX_OUTPUT_KEYS = ("max_tokens", "max_output_tokens", "max_completion_tokens", "max_new_tokens")


def output_token_ceiling(payload: Mapping[str, Any], default: int | None = None) -> Decimal:
    """The admitted completion ceiling: the request's max-token field, else ``default``."""
    if default is not None and _first_present(payload, *_MAX_OUTPUT_KEYS) is _MISSING:
        return Decimal(default)
    return _estimate_output_token_upper_bound(payload)


def _cached_token_count(payload: EstimatePayload, input_tokens: Decimal) -> Decimal:
    raw = _token_count(payload, "cached_tokens")
    if raw is _MISSING:
        return Decimal(0)
    return min(_non_negative_decimal(raw, "cached_tokens"), input_tokens)


def _input_token_upper_bound(payload: EstimatePayload, estimated: Decimal) -> Decimal:
    declared_bytes = _first_present(payload, "input_bytes")
    byte_count = Decimal(_input_text_bytes(payload))
    if declared_bytes is not _MISSING:
        byte_count = max(
            byte_count,
            _non_negative_decimal(declared_bytes, "input_bytes"),
        )
    # Byte-level tokenizers cannot emit more content tokens than the number of
    # input bytes. Explicit counts may be larger and are retained when supplied
    # by a trusted tokenizer. Either way, client metadata cannot lower the cap.
    return max(estimated, byte_count)


def _estimate_input_tokens(payload: EstimatePayload) -> Decimal:
    input_bytes = _first_present(payload, "input_bytes")
    if input_bytes is not _MISSING:
        return _non_negative_decimal(input_bytes, "input_bytes") / Decimal(4)

    total_chars = sum(
        _text_length(payload[key])
        for key in ("system", "messages", "prompt", "input", "texts")
        if key in payload and payload[key] is not None
    )
    return Decimal(total_chars) / Decimal(4)


def _input_text_bytes(payload: Mapping[str, Any]) -> int:
    return sum(
        _text_byte_length(payload[key])
        for key in ("system", "messages", "prompt", "input", "texts")
        if key in payload and payload[key] is not None
    )


def _text_byte_length(value: object) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-8"))
    if isinstance(value, Mapping):
        return sum(_text_byte_length(item) for item in value.values())
    if isinstance(value, list):
        return sum(_text_byte_length(item) for item in value)
    return 0


def _text_length(value: object) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, Mapping):
        return sum(
            _text_length(value[key])
            for key in ("content", "text", "prompt", "input")
            if key in value and value[key] is not None
        )
    if isinstance(value, list):
        return sum(_text_length(item) for item in value)
    return 0


def _usd(value: Decimal, *, rounding: str = ROUND_HALF_UP) -> Decimal:
    try:
        return value.quantize(_USD_QUANTUM, rounding=rounding)
    except DecimalException as exc:
        # Honor the estimator's ValueError-only contract: a cost too large to
        # quantize to the USD quantum (e.g. an absurd per_second_active rate) is
        # an invalid input, not an unhandled decimal error.
        raise ValueError(f"cost estimate is out of representable USD range: {value}") from exc


__all__ = [
    "ActiveIdlePricing",
    "CostComponent",
    "CostConfidence",
    "CostQuote",
    "CostEstimator",
    "EstimatePayload",
    "GpuHourPricing",
    "InputPriceTier",
    "output_token_ceiling",
    "PerRequestPricing",
    "PerRequestEstimator",
    "PerSecondPricing",
    "PerSecondEstimator",
    "PerTokenPricing",
    "PerTokenEstimator",
    "PerUnitPricing",
    "PerVmSecondPricing",
    "PricingModel",
    "PricingModelProtocol",
    "ProviderCost",
    "TaggedPricingModel",
    "get_estimator",
    "parse_pricing_model",
    "quote_cost",
]
