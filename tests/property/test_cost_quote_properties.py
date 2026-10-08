"""Property invariants for structured standing and provider-unit quotes."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode
from pitwall.core.models import Capability
from pitwall.cost.estimator import ActiveIdlePricing, PerUnitPricing, quote_cost

pytestmark = pytest.mark.property

_NOW = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)


def _capability(*, execution_timeout_seconds: int = 60) -> Capability:
    return Capability(
        id="cap_quote_property",
        name="quote.property",
        version="1",
        class_=CapabilityClass.GPU_LEASE,
        cost_mode=CostMode.PER_REQUEST,
        defaults={"execution_timeout_ms": execution_timeout_seconds * 1_000},
        source=CapabilitySource.API,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _fixed(value: int, places: int = 6) -> Decimal:
    return Decimal(value).scaleb(-places)


@given(
    rate_units=st.integers(min_value=0, max_value=10_000_000),
    count_units=st.integers(min_value=0, max_value=1_000_000),
    extra_units=st.integers(min_value=0, max_value=1_000_000),
)
def test_per_unit_quotes_are_non_negative_monotone_and_bounded(
    rate_units: int,
    count_units: int,
    extra_units: int,
) -> None:
    rate = _fixed(rate_units)
    count = _fixed(count_units, places=3)
    ceiling_count = max(
        count + _fixed(extra_units, places=3),
        Decimal("0.001"),
    )
    quote = quote_cost(
        capability=_capability(),
        provider_cost={
            "kind": "per_unit",
            "unit": "output",
            "rate_per_unit": str(rate),
        },
        payload={"unit_count": str(count), "max_unit_count": str(ceiling_count)},
    )

    assert quote.estimate() >= Decimal("0")
    assert quote.upper_bound() >= quote.estimate()

    higher = quote_cost(
        capability=_capability(),
        provider_cost=quote.pricing,
        payload={
            "unit_count": str(count),
            "max_unit_count": str(ceiling_count + Decimal("0.001")),
        },
    )
    assert higher.upper_bound() >= quote.upper_bound()


@given(
    active_rate_units=st.integers(min_value=0, max_value=1_000_000),
    idle_rate_units=st.integers(min_value=0, max_value=1_000_000),
    active_seconds=st.integers(min_value=0, max_value=3_600),
    active_extra=st.integers(min_value=0, max_value=3_600),
    idle_seconds=st.integers(min_value=0, max_value=3_600),
    idle_extra=st.integers(min_value=0, max_value=3_600),
    increment=st.integers(min_value=1, max_value=300),
)
def test_active_idle_ceiling_covers_estimate_and_component_sum(
    active_rate_units: int,
    idle_rate_units: int,
    active_seconds: int,
    active_extra: int,
    idle_seconds: int,
    idle_extra: int,
    increment: int,
) -> None:
    quote = quote_cost(
        capability=_capability(execution_timeout_seconds=active_seconds + active_extra),
        provider_cost={
            "kind": "active_idle",
            "active_rate_per_second": str(_fixed(active_rate_units)),
            "idle_rate_per_second": str(_fixed(idle_rate_units)),
            "minimum_billing_increment_seconds": str(increment),
            "scale_to_zero": False,
            "max_idle_seconds": str(idle_seconds + idle_extra),
        },
        payload={
            "active_seconds": str(active_seconds),
            "idle_seconds": str(idle_seconds),
        },
    )

    assert isinstance(quote.pricing, ActiveIdlePricing)
    assert quote.estimate() == sum(
        (component.estimate for component in quote.components),
        start=Decimal("0"),
    )
    assert quote.upper_bound() == sum(
        (component.ceiling for component in quote.components),
        start=Decimal("0"),
    )
    assert quote.upper_bound() >= quote.estimate() >= Decimal("0")


@given(
    rate_units=st.integers(min_value=0, max_value=10**18),
)
def test_per_unit_model_json_round_trip_preserves_decimal(rate_units: int) -> None:
    rate = _fixed(rate_units, places=18)
    pricing = PerUnitPricing(unit="video_second", rate_per_unit=str(rate))

    restored = PerUnitPricing.model_validate(pricing.model_dump(mode="json"))

    assert restored == pricing
    assert restored.rate_per_unit == rate
