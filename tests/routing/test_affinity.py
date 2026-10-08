"""Cache-affinity pinning for prompt-cache-heavy pools (research §14 Phase 4)."""

from __future__ import annotations

from typing import Any

from pitwall.routing.affinity import AFFINITY_BONUS, affinity_bonus
from pitwall.routing.scoring import explain_score
from pitwall.routing.types import Hints, ObservedMetrics


def _prompt_cache_provider(*, prompt_cache: bool) -> dict[str, Any]:
    return {
        "id": "prov_gateway",
        "config": {
            "gateway": {
                "catalog": {
                    "pool_key": "cache-pool",
                    "prompt_cache": prompt_cache,
                }
            }
        },
    }


def test_affinity_bonus_is_five_on_cache_key_match() -> None:
    provider = _prompt_cache_provider(prompt_cache=True)
    hints = Hints(cache_key="prompt-hash-1")
    observed = ObservedMetrics(last_cache_key="prompt-hash-1")

    assert affinity_bonus(provider, hints, observed) == 5.0
    assert AFFINITY_BONUS == 5.0


def test_affinity_bonus_is_zero_on_cache_key_mismatch() -> None:
    provider = _prompt_cache_provider(prompt_cache=True)
    hints = Hints(cache_key="prompt-hash-2")
    observed = ObservedMetrics(last_cache_key="prompt-hash-1")

    assert affinity_bonus(provider, hints, observed) == 0.0


def test_affinity_bonus_is_zero_without_a_cache_key_hint() -> None:
    provider = _prompt_cache_provider(prompt_cache=True)
    observed = ObservedMetrics(last_cache_key="prompt-hash-1")

    assert affinity_bonus(provider, Hints(), observed) == 0.0
    assert affinity_bonus(provider, None, observed) == 0.0


def test_affinity_bonus_is_zero_without_prompt_cache_evidence() -> None:
    provider = _prompt_cache_provider(prompt_cache=False)
    hints = Hints(cache_key="prompt-hash-1")
    observed = ObservedMetrics(last_cache_key="prompt-hash-1")

    assert affinity_bonus(provider, hints, observed) == 0.0


def test_explain_score_adds_affinity_bonus_into_score_before_multiplier() -> None:
    provider = _prompt_cache_provider(prompt_cache=True)
    hints = Hints(cache_key="prompt-hash-1")
    observed = ObservedMetrics(last_cache_key="prompt-hash-1")

    explanation = explain_score(provider, hints, observed)

    assert explanation.affinity_bonus == 5.0
    assert explanation.score_before_multiplier == 105.0
    assert explanation.final_score == 105.0
    assert explanation.to_dict()["affinity_bonus"] == 5.0


def test_plan_identity_changes_with_hints_cache_key() -> None:
    provider = _prompt_cache_provider(prompt_cache=True)
    observed = ObservedMetrics(last_cache_key="prompt-hash-1")

    pinned = explain_score(provider, Hints(cache_key="prompt-hash-1"), observed).to_dict()
    unpinned = explain_score(provider, Hints(cache_key="prompt-hash-9"), observed).to_dict()
    unhinted = explain_score(provider, None, observed).to_dict()

    assert pinned["final_score"] != unpinned["final_score"]
    assert pinned["final_score"] != unhinted["final_score"]
    assert unpinned["affinity_bonus"] == 0.0
    assert unhinted["affinity_bonus"] == 0.0
