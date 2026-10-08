"""Runtime budget limits: resolution, validation, and status (no database)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.cost.budget_limits import BudgetLimits, BudgetLimitsError, read_limits, set_limits


class _Conn:
    def __init__(self, row: dict[str, Any] | None) -> None:
        self.row = row

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        assert "pitwall.budget_limits" in sql
        return self.row


@pytest.mark.anyio
async def test_no_row_means_the_environment_defaults_apply() -> None:
    limits = await read_limits(
        _Conn(None), default_monthly=Decimal("5"), default_per_request=Decimal("10")
    )
    assert limits == BudgetLimits(Decimal("5"), Decimal("10"), "environment")


@pytest.mark.anyio
async def test_a_runtime_row_overrides_the_defaults() -> None:
    at = dt.datetime(2026, 9, 26, 18, 0, tzinfo=dt.UTC)
    row = {
        "monthly_budget_usd": Decimal("50"),
        "per_request_max_usd": Decimal("10"),
        "updated_at": at,
        "updated_by": "mcp",
        "reason": "4x4090 batch",
    }
    limits = await read_limits(
        _Conn(row), default_monthly=Decimal("5"), default_per_request=Decimal("10")
    )
    assert (limits.monthly_budget_usd, limits.source, limits.reason) == (
        Decimal("50"),
        "runtime",
        "4x4090 batch",
    )
    assert limits.to_dict()["monthly_budget_usd"] == "50"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("monthly", "per_request", "reason", "message"),
    [
        (None, None, "why", "at least one"),
        (Decimal("0"), None, "why", "positive"),
        (None, Decimal("-1"), "why", "positive"),
        (Decimal("50"), None, "   ", "reason"),
        # Regression (admin fuzz): NUMERIC(14, 6) overflows above 1e8 and rounds tiny
        # values to zero, and Postgres text cannot hold NUL; each was a database 500.
        ("1e100", None, "why", "between 0.000001 and 99999999.999999"),
        (None, "99999999.9999996", "why", "between 0.000001 and 99999999.999999"),
        ("0.0000000001", None, "why", "between 0.000001 and 99999999.999999"),
        (Decimal("50"), None, "why\x00", "NUL"),
    ],
)
async def test_invalid_changes_are_refused_before_any_write(
    monthly: Any, per_request: Any, reason: str, message: str
) -> None:
    class _NoPool:
        def acquire(self) -> Any:
            raise AssertionError("no database access for an invalid change")

    with pytest.raises(BudgetLimitsError, match=message):
        await set_limits(
            _NoPool(),
            monthly_budget_usd=monthly,
            per_request_max_usd=per_request,
            reason=reason,
            actor="cli",
        )
