"""Anonymous requests coalesce only when the answer cannot differ (review finding #12).

Two anonymous chat requests with sampling on used to share one upstream call, so the second
caller got the first caller's sample instead of its own.
"""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.core.enums import CapabilityClass
from pitwall.routing.coalescing import build_inference_coalescing_key


def _key(
    capability_class: CapabilityClass,
    params: dict[str, Any],
    *,
    idempotency_key: str | None = None,
) -> str:
    return build_inference_coalescing_key(
        idempotency_key=idempotency_key,
        capability_id="cap",
        provider_id="auto",
        capability_params=params,
        capability_class=capability_class,
    )


CHAT = {"messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.parametrize(
    "capability_class",
    [CapabilityClass.LLM, CapabilityClass.VISION, CapabilityClass.TRANSCRIBE],
)
def test_sampled_generation_never_shares(capability_class: CapabilityClass) -> None:
    assert _key(capability_class, CHAT) != _key(capability_class, CHAT)
    hot = {**CHAT, "temperature": 0.7}
    assert _key(capability_class, hot) != _key(capability_class, hot)


@pytest.mark.parametrize("temperature", [0, 0.0])
def test_greedy_generation_shares_by_content(temperature: float) -> None:
    greedy = {**CHAT, "temperature": temperature}
    assert _key(CapabilityClass.LLM, greedy) == _key(CapabilityClass.LLM, dict(greedy))


def test_several_choices_never_share_even_when_greedy() -> None:
    several = {**CHAT, "temperature": 0, "n": 2}
    assert _key(CapabilityClass.LLM, several) != _key(CapabilityClass.LLM, several)


@pytest.mark.parametrize("capability_class", [CapabilityClass.EMBEDDING, CapabilityClass.RERANK])
def test_scoring_capabilities_share_by_content(capability_class: CapabilityClass) -> None:
    params = {"input": ["a", "b"]}
    assert _key(capability_class, params) == _key(capability_class, dict(params))
    assert _key(capability_class, params) != _key(capability_class, {"input": ["c"]})


@pytest.mark.parametrize("capability_class", [CapabilityClass.CUSTOM, CapabilityClass.GPU_LEASE])
def test_unknown_behaviour_never_shares(capability_class: CapabilityClass) -> None:
    params = {"input": "x", "temperature": 0}
    assert _key(capability_class, params) != _key(capability_class, params)


def test_idempotency_keyed_requests_are_unchanged() -> None:
    first = _key(CapabilityClass.LLM, CHAT, idempotency_key="idem-1")
    assert first == _key(CapabilityClass.LLM, CHAT, idempotency_key="idem-1")
    assert first != _key(CapabilityClass.LLM, CHAT, idempotency_key="idem-2")
