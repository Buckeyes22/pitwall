"""Per-token pricing: cached input, context tiers, reasoning, and default ceilings."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode
from pitwall.core.models import Capability
from pitwall.cost.estimator import PerTokenPricing, output_token_ceiling, parse_pricing_model
from pitwall.cost.usage import parse_usage_json

TIERED = {
    "kind": "per_token",
    "per_million_input_tokens": "0.4",
    "per_million_output_tokens": "1.6",
    "per_million_cached_input_tokens": "0.08",
    "input_tiers": [
        {
            "above_input_tokens": 256000,
            "per_million_input_tokens": "1.2",
            "per_million_output_tokens": "4.8",
            "per_million_cached_input_tokens": "0.24",
        }
    ],
    "default_max_output_tokens": 131072,
}


NOW = dt.datetime(2026, 9, 26, tzinfo=dt.UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_ms",
        name="ms.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.PER_TOKEN,
        source=CapabilitySource.YAML,
        created_at=NOW,
        updated_at=NOW,
    )


def _pricing() -> PerTokenPricing:
    pricing = parse_pricing_model({"cost": TIERED})
    assert isinstance(pricing, PerTokenPricing)
    return pricing


def test_legacy_pricing_is_unchanged() -> None:
    plain = parse_pricing_model(
        {
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "2",
            }
        }
    )
    assert plain.estimate(
        _capability(), {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
    ) == Decimal("3")


def test_cached_tokens_use_the_cached_price() -> None:
    cost = _pricing().estimate(
        _capability(), {"prompt_tokens": 200_000, "cached_tokens": 100_000, "completion_tokens": 0}
    )
    assert cost == Decimal("0.048")  # 100k * 0.4 + 100k * 0.08 per million (below the 256k tier)


def test_cached_tokens_above_the_tier_use_the_tier_cached_price() -> None:
    cost = _pricing().estimate(
        _capability(),
        {"prompt_tokens": 1_000_000, "cached_tokens": 500_000, "completion_tokens": 0},
    )
    assert cost == Decimal("0.72")  # 500k * 1.2 + 500k * 0.24 per million (above the 256k tier)


def test_tier_applies_above_the_threshold() -> None:
    cost = _pricing().estimate(
        _capability(), {"prompt_tokens": 300_000, "completion_tokens": 1_000_000}
    )
    assert cost == Decimal("5.16")  # 300k * 1.2 + 1M * 4.8 per million


def test_upper_bound_uses_the_highest_tier_and_default_ceiling() -> None:
    bound = _pricing().upper_bound(_capability(), {"prompt_tokens": 1000})
    expected = (Decimal(1000) * Decimal("1.2") + Decimal(131072) * Decimal("4.8")) / Decimal(
        1_000_000
    )
    assert expected <= bound < expected + Decimal("0.00001")


def test_output_ceiling_prefers_the_request_then_the_default() -> None:
    assert output_token_ceiling({"max_tokens": 900}, 131072) == Decimal(900)
    assert output_token_ceiling({}, 131072) == Decimal(131072)
    with pytest.raises(ValueError):
        output_token_ceiling({}, None)


def test_usage_reads_cached_tokens_and_counts_reasoning_inside_completion() -> None:
    usage = parse_usage_json(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 40},
                "completion_tokens_details": {"reasoning_tokens": 30},
            }
        }
    )
    assert usage is not None
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cached_tokens) == (100, 50, 40)
