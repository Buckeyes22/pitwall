"""Behaviour pins for the tagged pricing models: amounts, bounds, and error contracts.

Every expectation here is a hand-computed dollar amount, a component breakdown, or the
exact operator-facing error text. They are pure and hermetic so they can run inside the
mutation sandbox.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import pytest

from pitwall.core.models import Capability
from pitwall.cost.estimator import (
    ActiveIdlePricing,
    CostComponent,
    CostQuote,
    GpuHourPricing,
    InputPriceTier,
    PerRequestPricing,
    PerSecondPricing,
    PerTokenPricing,
    PerUnitPricing,
    PerVmSecondPricing,
    ZeroOrEnergyPricing,
    output_token_ceiling,
    parse_pricing_model,
)

_CREATED_AT = "2026-05-26T14:00:00Z"
_SUB_MICRO_RATE = Decimal("0.0000001")


def _capability(*, execution_timeout_ms: int = 1_000, cost_mode: str = "zero") -> Capability:
    return Capability(
        id="cap_quote_contract",
        name="embedding.quote.contract",
        version="1.0.0",
        **{"class": "embedding"},
        cost_mode=cost_mode,
        defaults={"execution_timeout_ms": execution_timeout_ms},
        created_at=_CREATED_AT,
        updated_at=_CREATED_AT,
    )


def _exactly(message: str) -> Any:
    return pytest.raises(ValueError, match=f"^{re.escape(message)}$")


def _validation(message: str) -> Any:
    # pydantic renders field-validator errors as "Value error, <message> [type=...".
    return pytest.raises(ValueError, match=re.escape(f"Value error, {message} ["))


def _by_name(components: tuple[CostComponent, ...]) -> dict[str, CostComponent]:
    return {component.name: component for component in components}


# --- duration-priced models: estimate rounds half-up, ceiling rounds up ---------------------


@pytest.mark.parametrize(
    "pricing",
    [
        GpuHourPricing(per_second_active=_SUB_MICRO_RATE),
        PerVmSecondPricing(rate_per_second=_SUB_MICRO_RATE),
        PerSecondPricing(rate_per_second=_SUB_MICRO_RATE),
    ],
)
def test_duration_pricing_estimate_rounds_half_up_while_ceiling_rounds_up(pricing: Any) -> None:
    # 0.0000001 USD/s for one second is a tenth of a micro-dollar: the estimate rounds it
    # away, the admission ceiling must never under-reserve it.
    capability = _capability(execution_timeout_ms=1_000)

    assert pricing.estimate(capability, {}) == Decimal("0.000000")
    assert pricing.upper_bound(capability, {}) == Decimal("0.000001")


def test_per_request_estimate_rounds_half_up_while_ceiling_rounds_up() -> None:
    pricing = PerRequestPricing(per_request=_SUB_MICRO_RATE)
    capability = _capability()

    assert pricing.estimate(capability, {}) == Decimal("0.000000")
    assert pricing.upper_bound(capability, {}) == Decimal("0.000001")


def test_per_second_bid_rate_raises_only_the_ceiling() -> None:
    pricing = PerSecondPricing(
        rate_per_second=Decimal("0.001"), bid_rate_per_second=Decimal("0.004")
    )
    capability = _capability(execution_timeout_ms=2_500)

    (component,) = pricing.quote_components(capability, {})

    assert component.estimated_count == Decimal("2.5")
    assert component.ceiling_count == Decimal("2.5")
    assert pricing.estimate(capability, {}) == Decimal("0.002500")
    assert pricing.upper_bound(capability, {}) == Decimal("0.010000")


# --- per-request ---------------------------------------------------------------------------


_PER_REQUEST_CAP_MESSAGE = (
    "per_request pricing covers exactly one admitted invocation; "
    "request_count and max_requests are not accepted as client-supplied caps"
)


@pytest.mark.parametrize("payload", [{"request_count": 3}, {"max_requests": 3}])
def test_per_request_refuses_each_client_supplied_count_on_its_own(
    payload: dict[str, Any],
) -> None:
    pricing = PerRequestPricing(per_request=Decimal("0.01"))

    with _exactly(_PER_REQUEST_CAP_MESSAGE):
        pricing.quote_components(_capability(), payload)


def test_per_request_and_keyless_zero_quotes_are_exact() -> None:
    capability = _capability()

    per_request = CostQuote(PerRequestPricing(per_request=Decimal("0.01")), capability, {})
    free = CostQuote(ZeroOrEnergyPricing(), capability, {})

    assert per_request.confidence == "exact"
    assert free.confidence == "exact"
    assert per_request.model_dump()["confidence"] == "exact"


# --- zero / energy -------------------------------------------------------------------------


def test_energy_needs_both_watts_and_price_otherwise_it_is_free() -> None:
    capability = _capability(execution_timeout_ms=3_600_000)

    for pricing in (
        ZeroOrEnergyPricing(watts=Decimal("500")),
        ZeroOrEnergyPricing(usd_per_kwh=Decimal("0.30")),
    ):
        (component,) = pricing.quote_components(capability, {})
        assert component.name == "zero"
        assert pricing.estimate(capability, {}) == Decimal("0.000000")
        assert pricing.upper_bound(capability, {}) == Decimal("0.000000")


def test_energy_estimate_uses_expected_seconds_and_ceiling_uses_timeout() -> None:
    # 1000 W for one hour (timeout) = 1 kWh at 0.30 USD; expected 30 minutes = 0.5 kWh.
    pricing = ZeroOrEnergyPricing(watts=Decimal("1000"), usd_per_kwh=Decimal("0.30"))
    capability = _capability(execution_timeout_ms=3_600_000)
    payload = {"expected_seconds": 1800}

    (component,) = pricing.quote_components(capability, payload)

    assert component.name == "energy"
    assert component.estimated_count == Decimal("0.5")
    assert component.ceiling_count == Decimal("1")
    assert pricing.estimate(capability, payload) == Decimal("0.150000")
    assert pricing.upper_bound(capability, payload) == Decimal("0.300000")


def test_energy_without_expected_seconds_charges_the_whole_timeout() -> None:
    pricing = ZeroOrEnergyPricing(watts=Decimal("1000"), usd_per_kwh=Decimal("0.30"))
    capability = _capability(execution_timeout_ms=1_800_000)

    assert pricing.estimate(capability, {}) == Decimal("0.150000")
    assert pricing.upper_bound(capability, {}) == Decimal("0.150000")


def test_energy_rejects_expected_seconds_beyond_timeout_and_negative_seconds() -> None:
    pricing = ZeroOrEnergyPricing(watts=Decimal("1000"), usd_per_kwh=Decimal("0.30"))
    capability = _capability(execution_timeout_ms=1_000)

    with _exactly(
        "capability execution timeout must be greater than or equal to the estimated count"
    ):
        pricing.quote_components(capability, {"expected_seconds": 2})
    with _exactly("expected_seconds must be non-negative"):
        pricing.quote_components(capability, {"expected_seconds": "-1"})


def test_legacy_zero_mode_carries_energy_settings_into_the_tagged_model() -> None:
    pricing = parse_pricing_model({"watts": "250", "usd_per_kwh": "0.20"}, cost_mode="zero")

    assert pricing == ZeroOrEnergyPricing(watts=Decimal("250"), usd_per_kwh=Decimal("0.20"))


@pytest.mark.parametrize("key", ["watts", "usd_per_kwh"])
def test_legacy_zero_mode_names_the_rejected_energy_setting(key: str) -> None:
    with _exactly(f"{key} must be non-negative"):
        parse_pricing_model({key: "-1"}, cost_mode="zero")


# --- per-unit ------------------------------------------------------------------------------


def test_per_unit_ceiling_defaults_to_the_declared_count() -> None:
    pricing = PerUnitPricing(unit="frame", rate_per_unit=Decimal("0.02"))
    capability = _capability()

    (component,) = pricing.quote_components(capability, {"unit_count": 5})

    assert (component.name, component.unit) == ("run_units", "frame")
    assert component.estimated_count == component.ceiling_count == Decimal("5")
    assert pricing.estimate(capability, {"unit_count": 5}) == Decimal("0.100000")
    assert pricing.upper_bound(capability, {"unit_count": 5}) == Decimal("0.100000")


def test_per_unit_accepts_a_single_unit_ceiling() -> None:
    pricing = PerUnitPricing(unit="frame", rate_per_unit=Decimal("0.02"))

    assert pricing.upper_bound(_capability(), {"unit_count": 1, "max_unit_count": 1}) == Decimal(
        "0.020000"
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "unit_count is required for per_unit pricing"),
        ({"unit_count": "-1"}, "unit_count must be non-negative"),
        ({"unit_count": 0, "max_unit_count": 0}, "max_unit_count must be positive"),
        ({"unit_count": 0, "max_unit_count": "-2"}, "max_unit_count must be non-negative"),
        (
            {"unit_count": 3, "max_unit_count": 2},
            "max_unit_count must be greater than or equal to the estimated count",
        ),
    ],
)
def test_per_unit_errors_name_the_offending_field(payload: dict[str, Any], message: str) -> None:
    pricing = PerUnitPricing(unit="frame", rate_per_unit=Decimal("0.02"))

    with _exactly(message):
        pricing.quote_components(_capability(), payload)


def test_per_unit_validators_name_the_rejected_setting() -> None:
    with _validation("unit must be a string"):
        PerUnitPricing(unit=5, rate_per_unit=Decimal("1"))
    with _validation("unit must match ^[a-z][a-z0-9_-]{0,63}$"):
        PerUnitPricing(unit="Frames", rate_per_unit=Decimal("1"))
    with _validation("rate_per_unit must be non-negative"):
        PerUnitPricing(unit="frame", rate_per_unit="-1")


def test_cost_component_rejects_a_name_outside_the_identifier_grammar() -> None:
    with _exactly("name must match ^[a-z][a-z0-9_-]{0,63}$"):
        CostComponent(
            name="Bad Name",
            unit="frame",
            rate=Decimal("1"),
            ceiling_rate=Decimal("1"),
            estimated_count=Decimal("1"),
            ceiling_count=Decimal("1"),
            estimate=Decimal("1.000000"),
            ceiling=Decimal("1.000000"),
        )


# --- active / idle -------------------------------------------------------------------------


def _active_idle(**overrides: Any) -> ActiveIdlePricing:
    settings: dict[str, Any] = {
        "active_rate_per_second": Decimal("0.01"),
        "idle_rate_per_second": Decimal("0.001"),
        "max_idle_seconds": Decimal("100"),
    }
    settings.update(overrides)
    return ActiveIdlePricing(**settings)


def test_active_idle_estimate_uses_declared_active_seconds_and_ceiling_the_timeout() -> None:
    pricing = _active_idle()
    capability = _capability(execution_timeout_ms=10_000)
    payload = {"active_seconds": 4, "idle_seconds": 20}

    # estimate: 4 s * 0.01 + 20 s * 0.001; ceiling: 10 s * 0.01 + 100 s * 0.001
    assert pricing.estimate(capability, payload) == Decimal("0.060000")
    assert pricing.upper_bound(capability, payload) == Decimal("0.200000")


def test_active_idle_estimate_without_idle_bound_uses_the_declared_idle_time() -> None:
    # A standing endpoint without an idle bound cannot be admitted, but an informational
    # estimate is still the declared idle time.
    pricing = _active_idle(max_idle_seconds=None)
    capability = _capability(execution_timeout_ms=10_000)
    payload = {"active_seconds": 4, "idle_seconds": 20}

    assert pricing.estimate(capability, payload) == Decimal("0.060000")
    with _exactly(
        "max_idle_seconds pricing configuration is required for active_idle "
        "pricing without an immediate scale-to-zero guarantee"
    ):
        pricing.upper_bound(capability, payload)


@pytest.mark.parametrize(
    "pricing",
    [
        _active_idle(max_idle_seconds=None, scale_to_zero=True),
        _active_idle(max_idle_seconds=None, idle_rate_per_second=Decimal("0")),
    ],
)
def test_active_idle_without_standing_cost_bounds_idle_time_at_zero(
    pricing: ActiveIdlePricing,
) -> None:
    capability = _capability(execution_timeout_ms=10_000)

    components = _by_name(pricing.quote_components(capability, {}))

    assert components["idle_standing"].estimated_count == Decimal("0")
    assert components["idle_standing"].ceiling_count == Decimal("0")
    assert components["active_execution"].ceiling_count == Decimal("10")
    assert pricing.upper_bound(capability, {}) == Decimal("0.100000")
    with _exactly("max_idle_seconds must be greater than or equal to the estimated count"):
        pricing.quote_components(capability, {"idle_seconds": 1})


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"max_active_seconds": 1},
            "max_active_seconds must come from the capability execution timeout, "
            "not the client payload",
        ),
        (
            {"max_idle_seconds": 1},
            "max_idle_seconds must be provider/operator pricing configuration, "
            "not the client payload",
        ),
        (
            {"active_seconds": 11},
            "max_active_seconds must be greater than or equal to the estimated count",
        ),
        (
            {"idle_seconds": 101},
            "max_idle_seconds must be greater than or equal to the estimated count",
        ),
        ({"active_seconds": "-1"}, "active_seconds must be non-negative"),
    ],
)
def test_active_idle_rejects_client_bounds_and_out_of_bound_counts(
    payload: dict[str, Any], message: str
) -> None:
    with _exactly(message):
        _active_idle().quote_components(_capability(execution_timeout_ms=10_000), payload)


def test_active_idle_billable_seconds_overflow_is_a_value_error() -> None:
    pricing = _active_idle(minimum_billing_increment_seconds="1E-999999")

    with _exactly("billable seconds are out of representable range"):
        pricing.estimate(_capability(execution_timeout_ms=10_000), {"idle_seconds": 0})


def test_rate_times_count_overflow_is_a_value_error() -> None:
    pricing = GpuHourPricing(per_second_active="1E+999999")

    with _exactly("cost estimate is out of representable USD range"):
        pricing.estimate(_capability(execution_timeout_ms=10_000), {})


# --- assumptions ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pricing", "expected"),
    [
        (
            _active_idle(scale_to_zero=True),
            (
                "active duration is bounded by capability execution_timeout_ms",
                "idle duration is bounded by provider/operator pricing configuration",
                "durations round up to 1 second increments",
                "scale-to-zero enabled",
            ),
        ),
        (
            PerUnitPricing(unit="frame", rate_per_unit=Decimal("1")),
            ("unit_count is explicit, provider-enforced, and never inferred from payload text",),
        ),
        (
            PerRequestPricing(per_request=Decimal("1")),
            ("one admitted invocation is billed as exactly one request",),
        ),
        (
            GpuHourPricing(per_second_active=Decimal("1")),
            ("capability execution_timeout_ms is the billable duration bound",),
        ),
        (
            PerVmSecondPricing(rate_per_second=Decimal("1")),
            ("capability execution_timeout_ms is the billable duration bound",),
        ),
        (ZeroOrEnergyPricing(), ("no monetary charge is configured",)),
    ],
)
def test_quote_assumptions_state_the_bounding_rule(pricing: Any, expected: tuple[str, ...]) -> None:
    payload: dict[str, Any] = {"unit_count": 1} if isinstance(pricing, PerUnitPricing) else {}

    assert CostQuote(pricing, _capability(), payload).assumptions == expected


# --- per-token -----------------------------------------------------------------------------


def _cached_pricing(cached_rate: str = "0.3") -> PerTokenPricing:
    return PerTokenPricing(
        per_million_input_tokens=Decimal("3"),
        per_million_output_tokens=Decimal("15"),
        per_million_cached_input_tokens=Decimal(cached_rate),
    )


def test_cached_input_tokens_are_billed_at_the_cached_rate_in_the_estimate() -> None:
    # 600 uncached * 3/M + 400 cached * 0.3/M + 200 output * 15/M
    payload = {"input_tokens": 1000, "output_tokens": 200, "cached_tokens": 400}

    assert _cached_pricing().estimate(_capability(), payload) == Decimal("0.004920")


def test_cached_tokens_without_a_cached_rate_are_billed_as_input() -> None:
    pricing = PerTokenPricing(
        per_million_input_tokens=Decimal("3"), per_million_output_tokens=Decimal("15")
    )
    payload = {"input_tokens": 1000, "output_tokens": 200, "cached_tokens": 400}

    assert pricing.estimate(_capability(), payload) == Decimal("0.006000")


def test_a_single_cached_token_is_billed_at_the_cached_rate() -> None:
    pricing = _cached_pricing(cached_rate="1")
    payload = {"input_tokens": 1, "output_tokens": 0, "cached_tokens": 1}

    assert pricing.estimate(_capability(), payload) == Decimal("0.000001")


def test_cached_tokens_are_capped_at_the_input_count() -> None:
    payload = {"input_tokens": 100, "output_tokens": 0, "cached_tokens": 900}

    # all 100 input tokens are cached: 100 * 0.3/M = 0.00003
    assert _cached_pricing().estimate(_capability(), payload) == Decimal("0.000030")


def _tiered_pricing() -> PerTokenPricing:
    return PerTokenPricing(
        per_million_input_tokens=Decimal("1"),
        per_million_output_tokens=Decimal("2"),
        input_tiers=(
            InputPriceTier(
                above_input_tokens=1000,
                per_million_input_tokens=Decimal("4"),
                per_million_output_tokens=Decimal("8"),
            ),
        ),
    )


def test_tier_applies_only_strictly_above_its_threshold() -> None:
    pricing = _tiered_pricing()
    capability = _capability()

    at_threshold = {"input_tokens": 1000, "output_tokens": 100, "max_tokens": 100}
    above_threshold = {"input_tokens": 1001, "output_tokens": 100, "max_tokens": 100}

    # 1000 * 1/M + 100 * 2/M
    assert pricing.estimate(capability, at_threshold) == Decimal("0.001200")
    # 1001 * 4/M + 100 * 8/M
    assert pricing.estimate(capability, above_threshold) == Decimal("0.004804")
    assert pricing.upper_bound(capability, above_threshold) == Decimal("0.004804")


def test_quote_below_a_tier_reserves_the_highest_tier_rate_in_the_ceiling() -> None:
    pricing = _tiered_pricing()
    payload = {"input_tokens": 500, "max_tokens": 100}

    components = _by_name(pricing.quote_components(_capability(), payload))

    assert components["input_tokens"].rate == Decimal("0.000001")
    assert components["input_tokens"].ceiling_rate == Decimal("0.000004")
    assert components["input_tokens"].estimate == Decimal("0.000500")
    assert components["input_tokens"].ceiling == Decimal("0.002000")
    assert components["output_tokens"].rate == Decimal("0.000002")
    assert components["output_tokens"].ceiling_rate == Decimal("0.000008")
    assert components["output_tokens"].estimate == Decimal("0.000200")
    assert components["output_tokens"].ceiling == Decimal("0.000800")
    assert pricing.estimate(_capability(), payload) == Decimal("0.000700")
    assert pricing.upper_bound(_capability(), payload) == Decimal("0.002800")


def test_output_ceiling_rounds_up_to_the_next_micro_dollar() -> None:
    pricing = PerTokenPricing(
        per_million_input_tokens=Decimal("0"), per_million_output_tokens=Decimal("0.1")
    )
    payload = {"input_tokens": 0, "output_tokens": 0, "max_tokens": 3}

    assert pricing.estimate(_capability(), payload) == Decimal("0.000000")
    assert pricing.upper_bound(_capability(), payload) == Decimal("0.000001")


def test_default_max_output_tokens_bounds_a_request_without_a_max() -> None:
    pricing = PerTokenPricing(
        per_million_input_tokens=Decimal("0"),
        per_million_output_tokens=Decimal("10"),
        default_max_output_tokens=300,
    )

    # no max field: 256 estimated, 300 ceiling tokens at 10/M
    assert pricing.upper_bound(_capability(), {"input_tokens": 0}) == Decimal("0.003000")
    # an explicit max wins over the default
    assert pricing.upper_bound(_capability(), {"input_tokens": 0, "max_tokens": 100}) == (
        Decimal("0.001000")
    )


def test_output_token_ceiling_prefers_the_request_field_over_the_default() -> None:
    assert output_token_ceiling({"max_tokens": 100}, 300) == Decimal("100")
    assert output_token_ceiling({}, 300) == Decimal("300")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"output_tokens": 200, "max_tokens": 100},
            "max_output_tokens must be greater than or equal to the estimated count",
        ),
        ({"output_tokens": 0, "max_tokens": "-1"}, "max_output_tokens must be non-negative"),
        (
            {"output_tokens": 0, "max_tokens": 0},
            "max_output_tokens must be positive for bounded admission",
        ),
    ],
)
def test_per_token_output_bound_errors(payload: dict[str, Any], message: str) -> None:
    pricing = PerTokenPricing(
        per_million_input_tokens=Decimal("1"), per_million_output_tokens=Decimal("1")
    )

    with _exactly(message):
        pricing.quote_components(_capability(), {"input_tokens": 0, **payload})


def test_declared_input_bytes_raise_the_input_ceiling_above_the_token_estimate() -> None:
    pricing = PerTokenPricing(
        per_million_input_tokens=Decimal("1"), per_million_output_tokens=Decimal("1")
    )
    payload = {"input_tokens": 10, "input_bytes": 400, "max_tokens": 1, "output_tokens": 1}

    components = _by_name(pricing.quote_components(_capability(), payload))

    # estimate: max(10 declared, 400 bytes / 4) = 100 tokens; ceiling: 400 bytes
    assert components["input_tokens"].estimated_count == Decimal("100")
    assert components["input_tokens"].ceiling_count == Decimal("400")


@pytest.mark.parametrize("key", ["system", "prompt", "input"])
def test_every_text_field_counts_utf8_bytes_into_the_input_ceiling(key: str) -> None:
    pricing = PerTokenPricing(
        per_million_input_tokens=Decimal("1"), per_million_output_tokens=Decimal("1")
    )
    # four characters, eight UTF-8 bytes
    payload = {key: "éééé", "max_tokens": 1, "output_tokens": 1}

    components = _by_name(pricing.quote_components(_capability(), payload))

    assert components["input_tokens"].estimated_count == Decimal("1")
    assert components["input_tokens"].ceiling_count == Decimal("8")


def test_non_text_items_add_no_input_bytes() -> None:
    pricing = PerTokenPricing(
        per_million_input_tokens=Decimal("1"), per_million_output_tokens=Decimal("1")
    )
    payload = {"input": ["abcd", 7, None], "max_tokens": 1, "output_tokens": 1}

    components = _by_name(pricing.quote_components(_capability(), payload))

    assert components["input_tokens"].ceiling_count == Decimal("4")


def test_cached_rate_without_declared_cached_tokens_bills_every_input_token_in_full() -> None:
    payload = {"input_tokens": 1000, "output_tokens": 0}

    # 1000 * 3/M, nothing at the cached rate
    assert _cached_pricing().estimate(_capability(), payload) == Decimal("0.003000")


def test_negative_cached_tokens_are_rejected_by_name() -> None:
    payload = {"input_tokens": 1000, "output_tokens": 0, "cached_tokens": "-1"}

    with _exactly("cached_tokens must be non-negative"):
        _cached_pricing().estimate(_capability(), payload)


def test_quote_above_a_tier_reports_the_tier_rates_on_each_component() -> None:
    payload = {"input_tokens": 2000, "output_tokens": 100, "max_tokens": 100}

    components = _by_name(_tiered_pricing().quote_components(_capability(), payload))

    assert components["input_tokens"].rate == Decimal("0.000004")
    assert components["input_tokens"].estimate == Decimal("0.008000")
    assert components["output_tokens"].rate == Decimal("0.000008")
    assert components["output_tokens"].estimate == Decimal("0.000800")


def test_output_spend_overflow_is_a_value_error() -> None:
    pricing = PerTokenPricing(
        per_million_input_tokens=Decimal("0"), per_million_output_tokens="1E+999999"
    )
    payload = {"input_tokens": 0, "output_tokens": 10**7, "max_tokens": 10**7}

    with _exactly("cost estimate is out of representable USD range"):
        pricing.quote_components(_capability(), payload)
