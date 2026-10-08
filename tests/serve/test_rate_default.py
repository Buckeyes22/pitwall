"""--plan-only prices the run from the live snapshot; a launch should too.

Launching without --rate-per-second failed with rate_required even though the
same snapshot that produced the printed estimate was already in hand.
"""

from __future__ import annotations

from decimal import Decimal

from pitwall.serve import _rate_from_price


def test_hourly_price_becomes_a_per_second_rate() -> None:
    assert _rate_from_price(Decimal("0.74")) == Decimal("0.000206")


def test_rounding_never_understates_the_rate() -> None:
    """Rounding down would bill less than the pod costs."""
    rate = _rate_from_price(Decimal("0.12"))
    assert rate * Decimal(3600) >= Decimal("0.12")
