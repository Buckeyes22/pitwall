"""Hermetic tests for token-bucket rate-limit admission."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from pitwall.rate_limits import (
    TokenBucket,
    parse_retry_after,
    refill_tokens,
    seconds_until_available,
)


def test_retry_after_parser_accepts_seconds_and_http_dates() -> None:
    now = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)

    assert parse_retry_after("3", now=now, max_delay_s=10) == 3.0
    assert parse_retry_after("Thu, 28 May 2026 12:00:04 GMT", now=now, max_delay_s=10) == 4.0
    assert parse_retry_after("120", now=now, max_delay_s=10) == 10.0
    assert parse_retry_after("not a retry header", now=now) is None


def test_refill_reaches_full_capacity_in_ten_seconds() -> None:
    assert refill_tokens(tokens=0, capacity=20, elapsed_s=5) == 10
    assert refill_tokens(tokens=0, capacity=20, elapsed_s=10) == 20
    assert refill_tokens(tokens=19, capacity=20, elapsed_s=10) == 20


def test_refill_tokens_validates_inputs_and_allows_one_second_window() -> None:
    with pytest.raises(ValueError, match="^capacity must be > 0$"):
        refill_tokens(tokens=0, capacity=0, elapsed_s=1)
    with pytest.raises(ValueError, match="^tokens must be >= 0$"):
        refill_tokens(tokens=-1, capacity=1, elapsed_s=1)
    with pytest.raises(ValueError, match="^elapsed_s must be >= 0$"):
        refill_tokens(tokens=0, capacity=1, elapsed_s=-1)
    with pytest.raises(ValueError, match="^refill_window_s must be > 0$"):
        refill_tokens(tokens=0, capacity=1, elapsed_s=1, refill_window_s=0)

    assert refill_tokens(tokens=0, capacity=4, elapsed_s=0.25, refill_window_s=1) == 1


def test_seconds_until_available_uses_ten_second_refill_window() -> None:
    assert seconds_until_available(tokens=0, capacity=20, tokens_needed=4) == 2
    assert seconds_until_available(tokens=3, capacity=20, tokens_needed=4) == 0.5
    assert seconds_until_available(tokens=4, capacity=20, tokens_needed=4) == 0


def test_seconds_until_available_validates_inputs_and_defaults_to_one_token() -> None:
    with pytest.raises(ValueError, match="^capacity must be > 0$"):
        seconds_until_available(tokens=0, capacity=0)
    with pytest.raises(ValueError, match="^tokens must be >= 0$"):
        seconds_until_available(tokens=-1, capacity=1)
    with pytest.raises(ValueError, match="^tokens_needed must be > 0$"):
        seconds_until_available(tokens=0, capacity=1, tokens_needed=0)
    with pytest.raises(ValueError, match="^refill_window_s must be > 0$"):
        seconds_until_available(tokens=0, capacity=1, refill_window_s=0)

    assert seconds_until_available(tokens=1, capacity=10) == 0
    # The defaults are one token and a 10 s window: 4 tokens refill at 0.4/s, so an empty bucket
    # waits 2.5 s (2 tokens would wait 5 s; an 11 s window 2.75 s). Checked by behaviour, not by
    # __kwdefaults__, which a wrapper such as mutmut's trampoline hides.
    assert seconds_until_available(tokens=0, capacity=4) == pytest.approx(2.5)
    assert (
        seconds_until_available(
            tokens=0,
            capacity=4,
            tokens_needed=1,
            refill_window_s=1,
        )
        == 0.25
    )
    assert seconds_until_available(tokens=11, capacity=10, tokens_needed=11) == 0


def test_in_memory_bucket_consumes_refills_and_resizes() -> None:
    bucket = TokenBucket(capacity=10, tokens=10, last_refilled_at_s=0)

    assert bucket.try_consume(10, now_s=0) is True
    assert bucket.try_consume(1, now_s=0) is False
    assert bucket.retry_after_s() == 1

    assert bucket.try_consume(1, now_s=1) is True
    assert bucket.tokens == 0

    bucket.resize(2, now_s=11)
    assert bucket.capacity == 2
    assert bucket.tokens == 2


def test_impossible_token_request_has_infinite_retry_after() -> None:
    assert math.isinf(seconds_until_available(tokens=0, capacity=1, tokens_needed=2))


@pytest.mark.parametrize("window", [math.nan, math.inf, -math.inf])
def test_token_bucket_rejects_non_finite_window(window: float) -> None:
    with pytest.raises(ValueError, match="refill_window_s"):
        TokenBucket(capacity=1, refill_window_s=window)


@pytest.mark.parametrize("window", [math.nan, math.inf, -math.inf])
def test_refill_functions_reject_non_finite_windows(window: float) -> None:
    with pytest.raises(ValueError, match="refill_window_s"):
        refill_tokens(tokens=0, capacity=1, elapsed_s=1, refill_window_s=window)
    with pytest.raises(ValueError, match="refill_window_s"):
        seconds_until_available(tokens=0, capacity=1, refill_window_s=window)
