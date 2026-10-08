"""Structured COST-01 pricing and current-adapter fixture contracts."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import (
    ActiveIdlePricing,
    CostComponent,
    CostQuote,
    PerUnitPricing,
    PricingModel,
    parse_pricing_model,
    quote_cost,
)
from pitwall.providers.runpod import RunPodProvider
from pitwall.providers.vast import VastProvider

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "cost"
_NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)


def _capability(
    cost_mode: CostMode = CostMode.PER_SECOND,
    *,
    execution_timeout_ms: int = 60_000,
) -> Capability:
    return Capability(
        id="cap_cost_quote",
        name="cost.quote",
        version="1",
        class_=CapabilityClass.GPU_LEASE,
        cost_mode=cost_mode,
        defaults={"execution_timeout_ms": execution_timeout_ms},
        source=CapabilitySource.API,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(config: dict[str, Any], *, provider_type: ProviderType) -> ProviderRecord:
    return ProviderRecord(
        id="prov_cost_fixture",
        capability_id="cap_cost_quote",
        name="cost-fixture",
        provider_type=provider_type,
        config=config,
        priority=1,
        source=CapabilitySource.API,
        updated_at=_NOW,
    )


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"), parse_float=Decimal)


def test_active_idle_quote_names_components_and_rounds_billing_increments() -> None:
    quote = quote_cost(
        capability=_capability(execution_timeout_ms=120_000),
        provider_cost={
            "kind": "active_idle",
            "active_rate_per_second": "0.01",
            "idle_rate_per_second": "0.001",
            "minimum_billing_increment_seconds": "60",
            "scale_to_zero": False,
            "max_idle_seconds": "300",
        },
        payload={
            "active_seconds": "61",
            "idle_seconds": "1",
        },
    )

    assert isinstance(quote.pricing, ActiveIdlePricing)
    assert quote.estimate() == Decimal("1.260000")
    assert quote.upper_bound() == Decimal("1.500000")
    assert [component.name for component in quote.components] == [
        "active_execution",
        "idle_standing",
    ]
    assert [component.estimated_count for component in quote.components] == [
        Decimal("120"),
        Decimal("60"),
    ]
    assert [component.ceiling_count for component in quote.components] == [
        Decimal("120"),
        Decimal("300"),
    ]


def test_active_idle_requires_idle_bound_unless_scale_to_zero_is_explicit() -> None:
    standing = ActiveIdlePricing(
        active_rate_per_second="0.01",
        idle_rate_per_second="0.001",
    )
    assert standing.estimate(_capability(), {"idle_seconds": "2"}) == Decimal("0.602000")
    with pytest.raises(ValueError, match="max_idle_seconds pricing configuration is required"):
        standing.upper_bound(_capability(), {"idle_seconds": "2"})

    scale_to_zero = ActiveIdlePricing(
        active_rate_per_second="0.01",
        idle_rate_per_second="0.001",
        scale_to_zero=True,
    )
    assert scale_to_zero.upper_bound(_capability(), {}) == Decimal("0.600000")


def test_per_unit_quote_requires_explicit_count_and_never_infers_from_text() -> None:
    pricing = PerUnitPricing(unit="megapixel", rate_per_unit="0.123456789")
    with pytest.raises(ValueError, match="unit_count is required"):
        pricing.upper_bound(_capability(CostMode.PER_REQUEST), {"prompt": "make 4 images"})

    quote = CostQuote(
        pricing=pricing,
        capability=_capability(CostMode.PER_REQUEST),
        payload={"unit_count": "2.5", "max_unit_count": "3"},
    )
    assert quote.estimate() == Decimal("0.308642")
    assert quote.upper_bound() == Decimal("0.370371")
    assert quote.components[0].unit == "megapixel"
    assert quote.components[0].rate == Decimal("0.123456789")
    assert quote.components[0].estimated_count == Decimal("2.5")


@pytest.mark.parametrize(
    "pricing",
    [
        {
            "kind": "active_idle",
            "active_rate_per_second": "0.1",
            "idle_rate_per_second": "0.01",
            "scale_to_zero": False,
            "unknown": "rejected",
        },
        {
            "kind": "per_unit",
            "unit": "image",
            "rate_per_unit": "0.1",
            "unknown": "rejected",
        },
    ],
)
def test_new_pricing_variants_reject_unknown_fields(pricing: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        parse_pricing_model(pricing)


@pytest.mark.parametrize(
    ("pricing", "message"),
    [
        ({"kind": "per_unit", "unit": "Image", "rate_per_unit": "0.1"}, "unit must match"),
        ({"kind": "per_unit", "unit": "image", "rate_per_unit": 0.1}, "rate_per_unit"),
        (
            {
                "kind": "active_idle",
                "active_rate_per_second": 0.1,
                "idle_rate_per_second": "0.01",
            },
            "active_rate_per_second",
        ),
    ],
)
def test_new_pricing_variants_reject_ambiguous_or_float_inputs(
    pricing: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        parse_pricing_model(pricing)


def test_structured_quote_is_decimal_exact_and_json_safe() -> None:
    quote = quote_cost(
        capability=_capability(CostMode.PER_REQUEST),
        provider_cost={"kind": "per_unit", "unit": "image", "rate_per_unit": "0.125"},
        payload={"unit_count": "2", "max_unit_count": "3"},
    )

    python_shape = quote.model_dump()
    json_shape = quote.to_serializable_dict()

    assert python_shape["model"] == "per_unit"
    assert python_shape["estimate"] == Decimal("0.250000")
    assert python_shape["ceiling"] == Decimal("0.375000")
    assert json_shape == {
        "model": "per_unit",
        "components": [
            {
                "name": "run_units",
                "unit": "image",
                "rate": "0.125",
                "ceiling_rate": "0.125",
                "count": "2",
                "ceiling_count": "3",
                "estimate": "0.250000",
                "ceiling": "0.375000",
            }
        ],
        "estimate": "0.250000",
        "ceiling": "0.375000",
        "confidence": "bounded",
        "provenance": "provider_config",
        "currency": "USD",
        "assumptions": [
            "unit_count is explicit, provider-enforced, and never inferred from payload text"
        ],
    }
    json.dumps(json_shape)


def test_cost_component_rejects_optimistic_or_unquantized_bounds() -> None:
    with pytest.raises(ValueError, match="ceiling_rate must be greater"):
        CostComponent(
            name="execution",
            unit="second",
            rate=Decimal("0.2"),
            ceiling_rate=Decimal("0.1"),
            estimated_count=Decimal("1"),
            ceiling_count=Decimal("1"),
            estimate=Decimal("0.200000"),
            ceiling=Decimal("0.200000"),
        )
    with pytest.raises(ValueError, match="six-decimal USD quantum"):
        CostComponent(
            name="execution",
            unit="second",
            rate=Decimal("0.2"),
            ceiling_rate=Decimal("0.2"),
            estimated_count=Decimal("1"),
            ceiling_count=Decimal("1"),
            estimate=Decimal("0.1000001"),
            ceiling=Decimal("0.200000"),
        )


def test_token_components_preserve_legacy_aggregate_rounding() -> None:
    quote = quote_cost(
        capability=_capability(CostMode.PER_TOKEN),
        provider_cost={
            "kind": "per_token",
            "per_million_input_tokens": "0.4",
            "per_million_output_tokens": "0.4",
        },
        payload={"input_tokens": 1, "output_tokens": 1, "max_output_tokens": 1},
    )

    assert quote.estimate() == Decimal("0.000001")
    assert quote.upper_bound() == Decimal("0.000002")
    assert sum(
        (component.estimate for component in quote.components),
        start=Decimal("0"),
    ) == Decimal("0.000001")


@pytest.mark.parametrize(
    ("fixture_name", "provider", "provider_type", "expected_type"),
    [
        (
            "runpod_standing_endpoint.json",
            RunPodProvider(),
            ProviderType.SERVERLESS_LB,
            ActiveIdlePricing,
        ),
        (
            "vast_provider_unit.json",
            VastProvider(),
            ProviderType.POD_LEASE,
            PerUnitPricing,
        ),
    ],
)
def test_current_target_adapters_consume_new_pricing_fixtures(
    fixture_name: str,
    provider: RunPodProvider | VastProvider,
    provider_type: ProviderType,
    expected_type: type[ActiveIdlePricing] | type[PerUnitPricing],
) -> None:
    data = _fixture(fixture_name)
    capability = _capability(
        CostMode(data["capability_cost_mode"]),
        execution_timeout_ms=int(data.get("execution_timeout_ms", 60_000)),
    )
    record = _provider(data["provider_config"], provider_type=provider_type)

    pricing = provider.pricing_model(capability, record)
    quote = CostQuote(pricing=pricing, capability=capability, payload=data["payload"])

    assert provider.id == data["adapter"]
    assert isinstance(pricing, expected_type)
    assert quote.estimate() == Decimal(data["expected_estimate"])
    assert quote.upper_bound() == Decimal(data["expected_ceiling"])


def test_new_pricing_round_trips_without_precision_loss() -> None:
    pricing = PerUnitPricing(unit="video_second", rate_per_unit="0.123456789123456789")
    encoded = pricing.model_dump(mode="json")
    decoded = parse_pricing_model(encoded)

    assert decoded == pricing
    assert isinstance(decoded, PerUnitPricing)
    assert decoded.rate_per_unit == Decimal("0.123456789123456789")


def test_per_unit_overflow_is_a_bounded_value_error() -> None:
    pricing = PerUnitPricing(unit="output", rate_per_unit="1e999999")
    with pytest.raises(ValueError, match="representable USD range"):
        pricing.upper_bound(
            _capability(CostMode.PER_REQUEST),
            {"unit_count": "1e999999", "max_unit_count": "1e999999"},
        )


def test_tiny_positive_unit_charge_has_nonzero_conservative_ceiling() -> None:
    quote = quote_cost(
        capability=_capability(CostMode.PER_REQUEST),
        provider_cost={
            "kind": "per_unit",
            "unit": "output",
            "rate_per_unit": "0.0000004",
        },
        payload={"unit_count": "1"},
    )

    assert quote.estimate() == Decimal("0.000000")
    assert quote.upper_bound() == Decimal("0.000001")


@pytest.mark.parametrize(
    ("cost", "payload"),
    [
        ({"kind": "zero"}, {}),
        ({"kind": "gpu_hour", "per_second_active": "0.001"}, {}),
        ({"kind": "per_request", "per_request": "0.01"}, {}),
        (
            {
                "kind": "per_second",
                "rate_per_second": "0.001",
                "bid_rate_per_second": "0.002",
            },
            {},
        ),
        (
            {
                "kind": "per_token",
                "per_million_input_tokens": "0.3",
                "per_million_output_tokens": "0.6",
            },
            {"input_tokens": 2, "output_tokens": 1, "max_output_tokens": 2},
        ),
        ({"kind": "per_vm_second", "rate_per_second": "0.001"}, {}),
        (
            {
                "kind": "active_idle",
                "active_rate_per_second": "0.001",
                "idle_rate_per_second": "0.0001",
                "scale_to_zero": False,
                "max_idle_seconds": "2",
            },
            {"idle_seconds": "1"},
        ),
        (
            {"kind": "per_unit", "unit": "output", "rate_per_unit": "0.01"},
            {"unit_count": "1", "max_unit_count": "2"},
        ),
    ],
    ids=[
        "zero",
        "gpu-hour",
        "request",
        "second",
        "token",
        "vm-second",
        "active-idle",
        "provider-unit",
    ],
)
def test_every_active_tagged_variant_round_trips_and_has_a_conservative_quote(
    cost: dict[str, object],
    payload: dict[str, object],
) -> None:
    pricing = parse_pricing_model(cost)
    encoded = json.dumps(pricing.model_dump(mode="json"), sort_keys=True)
    restored = TypeAdapter(PricingModel).validate_json(encoded)
    quote = CostQuote(pricing=restored, capability=_capability(), payload=payload)

    assert restored == pricing
    assert isinstance(quote.estimate(), Decimal)
    assert quote.upper_bound() >= quote.estimate() >= Decimal("0")


def test_pricing_union_schema_snapshot_contains_only_active_variant_tags() -> None:
    schema = TypeAdapter(PricingModel).json_schema()
    discriminator = schema["discriminator"]

    assert discriminator["propertyName"] == "kind"
    assert set(discriminator["mapping"]) == {
        "active_idle",
        "gpu_hour",
        "per_request",
        "per_second",
        "per_token",
        "per_unit",
        "per_vm_second",
        "zero",
    }
    assert set(schema["$defs"]["ActiveIdlePricing"]["required"]) == {
        "active_rate_per_second",
        "idle_rate_per_second",
    }
    assert set(schema["$defs"]["PerUnitPricing"]["required"]) == {
        "unit",
        "rate_per_unit",
    }
