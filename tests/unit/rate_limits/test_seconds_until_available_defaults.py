"""Behaviour pins for the token-bucket wait calculation at its defaults and edges."""

from __future__ import annotations

from pitwall.rate_limits.algorithm import seconds_until_available


def test_default_request_waits_for_exactly_one_token() -> None:
    # capacity 10 over the 10 s window refills one token per second.
    assert seconds_until_available(tokens=0.0, capacity=10) == 1.0


def test_request_for_the_full_capacity_is_possible_after_a_full_refill() -> None:
    # needing exactly the capacity is satisfiable: an empty 2-token bucket fills in 10 s.
    assert seconds_until_available(tokens=0.0, capacity=2, tokens_needed=2.0) == 10.0
