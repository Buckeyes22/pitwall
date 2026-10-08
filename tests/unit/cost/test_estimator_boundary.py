from __future__ import annotations

from decimal import Decimal

import pytest

from pitwall.core.models import Capability
from pitwall.cost.estimator import (
    ActiveIdlePricing,
    CostQuote,
    PerTokenPricing,
    PerUnitPricing,
    ZeroOrEnergyPricing,
    get_estimator,
)

_CREATED_AT = "2026-05-26T14:00:00Z"


def _capability(
    *,
    cost_mode: str,
    execution_timeout_ms: int = 1_000,
) -> Capability:
    return Capability(
        id=f"cap_estimator_boundary_{cost_mode}",
        name=f"embedding.boundary.{cost_mode}",
        version="1.0.0",
        **{"class": "embedding"},
        cost_mode=cost_mode,
        defaults={"execution_timeout_ms": execution_timeout_ms},
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _estimate(
    mode: str,
    provider_cost: object,
    payload: dict[str, object],
    *,
    execution_timeout_ms: int = 1_000,
) -> Decimal:
    capability = _capability(cost_mode=mode, execution_timeout_ms=execution_timeout_ms)
    return get_estimator(mode).estimate(capability, provider_cost, payload)


@pytest.mark.parametrize(
    ("mode", "provider_cost", "payload"),
    [
        ("per_request", {"per_request": Decimal("-0.000001")}, {}),
        ("per_second", {"per_second_active": Decimal("-0.000001")}, {}),
        (
            "per_token",
            {
                "per_million_input_tokens": Decimal("-0.01"),
                "per_million_output_tokens": Decimal("0"),
            },
            {"input_tokens": 1, "output_tokens": 0},
        ),
    ],
)
def test_negative_cost_inputs_raise_value_error(
    mode: str,
    provider_cost: object,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="non-negative"):
        _estimate(mode, provider_cost, payload)


@pytest.mark.parametrize(
    ("mode", "provider_cost", "payload"),
    [
        ("per_request", {"per_request": Decimal("0")}, {}),
        ("per_second", {"per_second_active": Decimal("0")}, {}),
        (
            "per_token",
            {
                "per_million_input_tokens": Decimal("0"),
                "per_million_output_tokens": Decimal("0"),
            },
            {"input_tokens": 1, "output_tokens": 1},
        ),
    ],
)
def test_zero_cost_inputs_are_valid(
    mode: str,
    provider_cost: object,
    payload: dict[str, object],
) -> None:
    assert _estimate(mode, provider_cost, payload) == Decimal("0.000000")


@pytest.mark.parametrize(
    ("mode", "provider_cost", "payload"),
    [
        ("per_request", {"per_request": Decimal("1e30")}, {}),
        ("per_second", {"per_second_active": Decimal("1e30")}, {}),
        (
            "per_token",
            {
                "per_million_input_tokens": Decimal("1e36"),
                "per_million_output_tokens": Decimal("0"),
            },
            {"input_tokens": 1, "output_tokens": 0},
        ),
    ],
)
def test_huge_cost_inputs_raise_value_error(
    mode: str,
    provider_cost: object,
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="out of representable USD range"):
        _estimate(mode, provider_cost, payload)


def test_active_idle_quote_preserves_every_bound_and_assumption() -> None:
    quote = CostQuote(
        ActiveIdlePricing(
            active_rate_per_second="0.25",
            idle_rate_per_second="0.05",
            minimum_billing_increment_seconds="2",
            scale_to_zero=False,
            max_idle_seconds="6",
        ),
        _capability(cost_mode="per_second", execution_timeout_ms=2_500),
        {"active_seconds": "1", "idle_seconds": "3"},
    )

    assert quote.to_serializable_dict() == {
        "model": "active_idle",
        "components": [
            {
                "name": "active_execution",
                "unit": "second",
                "rate": "0.25",
                "ceiling_rate": "0.25",
                "count": "2",
                "ceiling_count": "4",
                "estimate": "0.500000",
                "ceiling": "1.000000",
            },
            {
                "name": "idle_standing",
                "unit": "second",
                "rate": "0.05",
                "ceiling_rate": "0.05",
                "count": "4",
                "ceiling_count": "6",
                "estimate": "0.200000",
                "ceiling": "0.300000",
            },
        ],
        "estimate": "0.700000",
        "ceiling": "1.300000",
        "confidence": "bounded",
        "provenance": "provider_config",
        "currency": "USD",
        "assumptions": [
            "active duration is bounded by capability execution_timeout_ms",
            "idle duration is bounded by provider/operator pricing configuration",
            "durations round up to 2 second increments",
            "standing endpoint remains idle",
        ],
    }


def test_energy_quote_preserves_physical_units_and_estimated_provenance() -> None:
    quote = CostQuote(
        ZeroOrEnergyPricing(watts="500", usd_per_kwh="0.2"),
        _capability(cost_mode="zero", execution_timeout_ms=2_500),
        {"expected_seconds": "1"},
    )

    assert quote.to_serializable_dict() == {
        "model": "zero",
        "components": [
            {
                "name": "energy",
                "unit": "kilowatt_hour",
                "rate": "0.2",
                "ceiling_rate": "0.2",
                "count": "0.0001388888888888888888888888889",
                "ceiling_count": "0.0003472222222222222222222222222",
                "estimate": "0.000028",
                "ceiling": "0.000070",
            }
        ],
        "estimate": "0.000028",
        "ceiling": "0.000070",
        "confidence": "estimated",
        "provenance": "operator_energy_config",
        "currency": "USD",
        "assumptions": ["energy cost uses configured watts and USD per kilowatt-hour"],
    }


def test_token_quote_counts_nested_utf8_bytes_in_the_admission_ceiling() -> None:
    quote = CostQuote(
        PerTokenPricing(
            per_million_input_tokens="10",
            per_million_output_tokens="20",
        ),
        _capability(cost_mode="per_token", execution_timeout_ms=2_500),
        {
            "messages": [{"role": "user", "content": "é"}],
            "input_tokens": 1,
            "output_tokens": 2,
            "max_output_tokens": 4,
        },
    )

    assert quote.to_serializable_dict() == {
        "model": "per_token",
        "components": [
            {
                "name": "input_tokens",
                "unit": "input_token",
                "rate": "0.00001",
                "ceiling_rate": "0.00001",
                "count": "1",
                "ceiling_count": "6",
                "estimate": "0.000010",
                "ceiling": "0.000060",
            },
            {
                "name": "output_tokens",
                "unit": "output_token",
                "rate": "0.00002",
                "ceiling_rate": "0.00002",
                "count": "2",
                "ceiling_count": "4",
                "estimate": "0.000040",
                "ceiling": "0.000080",
            },
        ],
        "estimate": "0.000050",
        "ceiling": "0.000140",
        "confidence": "bounded",
        "provenance": "provider_config",
        "currency": "USD",
        "assumptions": [
            "input tokens are explicit or estimated from request bytes or text",
            "max_output_tokens bounds completion spend",
            "aggregate micro-dollar rounding is allocated to the output component",
        ],
    }


def test_strict_unit_pricing_validation_names_the_rejected_field() -> None:
    with pytest.raises(ValueError, match="unit must be a string"):
        PerUnitPricing(unit=1, rate_per_unit="1")
    with pytest.raises(ValueError, match="rate_per_unit must be a Decimal"):
        PerUnitPricing(unit="image", rate_per_unit=1.0)
