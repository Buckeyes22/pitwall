"""Behaviour pins for cached-prompt-token capture and float token validation."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.cost.usage import TokenUsage, parse_usage_json, parse_usage_sse


def _body(details: object) -> dict[str, Any]:
    return {
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "prompt_tokens_details": details,
        }
    }


def test_cached_prompt_tokens_are_captured_from_prompt_token_details() -> None:
    assert parse_usage_json(_body({"cached_tokens": 4})) == TokenUsage(
        prompt_tokens=10, completion_tokens=2, total_tokens=12, cached_tokens=4
    )


def test_cached_prompt_tokens_are_captured_from_a_streamed_usage_frame() -> None:
    frame = (
        'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 2,'
        ' "prompt_tokens_details": {"cached_tokens": 7}}}\n\ndata: [DONE]\n\n'
    )

    usage = parse_usage_sse(frame)

    assert usage is not None
    assert usage.cached_tokens == 7


# The rule: a cached count is a non-negative JSON integer; booleans, negatives, strings,
# and null carry no usable count and are recorded as zero.
@pytest.mark.parametrize("cached", [-1, True, "5", None])
def test_cached_tokens_that_are_not_a_non_negative_int_count_as_zero(cached: object) -> None:
    usage = parse_usage_json(_body({"cached_tokens": cached}))

    assert usage is not None
    assert usage.cached_tokens == 0
    assert type(usage.cached_tokens) is int


def test_missing_prompt_token_details_count_as_zero_cached() -> None:
    usage = parse_usage_json(_body("not a mapping"))

    assert usage is not None
    assert usage.cached_tokens == 0


def test_negative_float_token_counts_are_rejected() -> None:
    assert parse_usage_json({"usage": {"prompt_tokens": -1.0, "completion_tokens": 1}}) is None
