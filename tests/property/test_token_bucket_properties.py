"""Property-based tests for the token bucket rate limiter.

Grounded in src/pitwall/rate_limits/algorithm.py (verified 2026-05-30):
    refill_tokens(*, tokens, capacity, elapsed_s, refill_window_s=10.0) -> float
        clamps to <= capacity; non-positive elapsed never adds tokens.
    TokenBucket(*, capacity>0, tokens=None, ...)
        .refill(*, now_s=None) -> float
        .try_consume(amount=1.0, *, now_s=None) -> bool   # refills first, all-or-nothing
        .resize(capacity, *, now_s=None) -> None
        .retry_after_s(amount=1.0) -> float

Determinism: instance ops pass now_s=0.0. Because the bucket stamps its
last-refill time from time.monotonic() at construction (a large positive),
elapsed = 0.0 - that = hugely negative, so refill is a no-op and consume math
is exact.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from pitwall.rate_limits.algorithm import (
    TokenBucket,
    refill_tokens,
)

pytestmark = pytest.mark.property

_capacity = st.integers(min_value=1, max_value=10_000)
_tokens = st.floats(min_value=0.0, max_value=10_000.0, allow_nan=False, allow_infinity=False)
_elapsed = st.floats(min_value=0.0, max_value=1_000.0, allow_nan=False, allow_infinity=False)
_window = st.floats(min_value=0.1, max_value=100.0, allow_nan=False, allow_infinity=False)
_amount = st.floats(min_value=0.01, max_value=10_000.0, allow_nan=False, allow_infinity=False)


# --- pure refill_tokens ----------------------------------------------------
@given(tokens=_tokens, capacity=_capacity, elapsed=_elapsed, window=_window)
def test_refill_never_exceeds_capacity_and_nonneg(
    tokens: float, capacity: int, elapsed: float, window: float
) -> None:
    out = refill_tokens(tokens=tokens, capacity=capacity, elapsed_s=elapsed, refill_window_s=window)
    assert out <= capacity + 1e-9
    assert out >= 0.0


@given(tokens=_tokens, capacity=_capacity, e1=_elapsed, e2=_elapsed, window=_window)
def test_refill_monotone_in_elapsed(
    tokens: float, capacity: int, e1: float, e2: float, window: float
) -> None:
    assume(e1 <= e2)
    lo = refill_tokens(tokens=tokens, capacity=capacity, elapsed_s=e1, refill_window_s=window)
    hi = refill_tokens(tokens=tokens, capacity=capacity, elapsed_s=e2, refill_window_s=window)
    assert hi >= lo - 1e-9


# --- TokenBucket consume semantics (deterministic via now_s=0.0) -----------
@given(capacity=_capacity, init=_tokens, amount=_amount)
def test_try_consume_all_or_nothing(capacity: int, init: float, amount: float) -> None:
    bucket = TokenBucket(capacity=capacity, tokens=init)
    before = bucket.tokens  # post-construction (clamped to capacity)
    ok = bucket.try_consume(amount, now_s=0.0)  # no-op refill => exact math
    if ok:
        assert bucket.tokens == pytest.approx(before - amount)
    else:
        assert bucket.tokens == before
        assert before < amount


@given(capacity=_capacity, init=_tokens, amount=_amount)
def test_retry_after_zero_iff_enough_tokens(capacity: int, init: float, amount: float) -> None:
    bucket = TokenBucket(capacity=capacity, tokens=init)
    enough = bucket.tokens >= amount
    ra = bucket.retry_after_s(amount)
    assert (ra == 0.0) is enough
    assert ra >= 0.0


def test_constructor_and_resize_reject_nonpositive_capacity() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0)
    bucket = TokenBucket(capacity=5)
    with pytest.raises(ValueError):
        bucket.resize(0)


# --- stateful: 0 <= tokens <= capacity holds after every operation ---------
class TokenBucketMachine(RuleBasedStateMachine):
    def __init__(self) -> None:
        super().__init__()
        self.bucket = TokenBucket(capacity=100, tokens=100.0)
        self.now = 0.0

    @rule(dt=st.floats(min_value=0.0, max_value=50.0, allow_nan=False, allow_infinity=False))
    def advance_and_refill(self, dt: float) -> None:
        self.now += dt  # monotonic clock
        self.bucket.refill(now_s=self.now)

    @rule(amount=st.floats(min_value=0.01, max_value=200.0, allow_nan=False, allow_infinity=False))
    def consume(self, amount: float) -> None:
        self.bucket.try_consume(amount, now_s=self.now)

    @rule(cap=st.integers(min_value=1, max_value=500))
    def resize(self, cap: int) -> None:
        self.bucket.resize(cap, now_s=self.now)

    @invariant()
    def tokens_within_bounds(self) -> None:
        assert -1e-9 <= self.bucket.tokens <= self.bucket.capacity + 1e-9


TestTokenBucketMachine = TokenBucketMachine.TestCase
