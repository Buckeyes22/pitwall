"""Behaviour pins for BudgetGate admission against an in-memory connection.

The fake connection answers the three queries the gate issues (runtime limits,
month-to-date spend, workload insert) and records every call, so these tests pin
admission decisions, boundaries, persisted values, and operator log lines without
a database.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from decimal import Decimal
from typing import Any

import pytest

from pitwall.cost.budget_gate import (
    MONTH_SPEND_AT_SQL,
    PITWALL_BUDGET_LOCK_KEY,
    BudgetGate,
    BudgetRejected,
    month_to_date_spend,
)
from pitwall.cost.budget_limits import BudgetLimits

_LOCK_SQL = "SELECT pg_advisory_xact_lock($1)"
_PLACEHOLDER = re.compile(r"^\$(\d+)")


def _normalise(sql: str) -> str:
    return " ".join(sql.split())


def _insert_columns(sql: str, args: tuple[Any, ...]) -> dict[str, Any]:
    """Map each inserted column to the value bound for it (literals kept as text)."""
    match = re.match(r"INSERT INTO \S+ \((.*?)\) VALUES \((.*?)\)", _normalise(sql))
    assert match is not None, sql
    names = [name.strip() for name in match.group(1).split(",")]
    values = [value.strip() for value in match.group(2).split(",")]
    bound: dict[str, Any] = {}
    for name, value in zip(names, values, strict=True):
        placeholder = _PLACEHOLDER.match(value)
        bound[name] = args[int(placeholder.group(1)) - 1] if placeholder else value.strip("'")
    return bound


class _Tx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Conn:
    def __init__(
        self,
        *,
        spend: Decimal = Decimal("0"),
        limits: tuple[Decimal, Decimal] | None = None,
        existing_id: str | None = None,
    ) -> None:
        self.spend = spend
        self.limits = limits
        self.existing_id = existing_id
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def transaction(self) -> _Tx:
        return _Tx()

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append((sql, args))
        return "SELECT 1"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any] | None:
        self.calls.append((sql, args))
        if "FROM pitwall.budget_limits" in _normalise(sql):
            if self.limits is None:
                return None
            return {
                "monthly_budget_usd": self.limits[0],
                "per_request_max_usd": self.limits[1],
                "updated_at": None,
                "updated_by": "operator",
                "reason": "test",
            }
        return {"s": self.spend}

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append((sql, args))
        text = _normalise(sql)
        if text.startswith("SELECT id FROM pitwall.workloads WHERE idempotency_key ="):
            return self.existing_id
        assert text.startswith("INSERT INTO pitwall.workloads"), text
        return _insert_columns(sql, args)["id"]


class _Acquire:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Conn:
        return self.conn

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


def _gate(
    conn: _Conn, *, monthly: str = "100", per_request: str = "10", workload_id: str = "wkl_1"
) -> BudgetGate:
    return BudgetGate(
        _Pool(conn),
        monthly_budget_usd=Decimal(monthly),
        per_request_max_usd=Decimal(per_request),
        workload_id_factory=lambda: workload_id,
    )


def _inserted_workload(conn: _Conn) -> dict[str, Any]:
    inserts = [
        _insert_columns(sql, args)
        for sql, args in conn.calls
        if _normalise(sql).startswith("INSERT INTO pitwall.workloads")
    ]
    assert len(inserts) == 1
    return inserts[0]


class _Quote:
    """A structured quote whose estimate and ceiling differ."""

    def __init__(self, estimate: object, ceiling: object) -> None:
        self._estimate = estimate
        self._ceiling = ceiling

    def estimate(self) -> object:
        return self._estimate

    def upper_bound(self) -> object:
        return self._ceiling

    def to_serializable_dict(self) -> dict[str, object]:
        return {"model": "per_unit", "ceiling": str(self._ceiling), "estimate": "x"}


class _CeilingOnly:
    def __init__(self, ceiling: object) -> None:
        self._ceiling = ceiling

    def upper_bound(self) -> object:
        return self._ceiling


# --- month-to-date spend -------------------------------------------------------------------


async def test_month_spend_at_an_instant_queries_that_month() -> None:
    conn = _Conn(spend=Decimal("12.5"))
    at = dt.datetime(2026, 3, 15, tzinfo=dt.UTC)

    assert await month_to_date_spend(conn, at=at) == Decimal("12.5")
    assert conn.calls == [(MONTH_SPEND_AT_SQL, (at,))]


# --- limits --------------------------------------------------------------------------------


async def test_effective_limits_fall_back_to_configured_defaults_through_the_pool() -> None:
    gate = _gate(_Conn(), monthly="100", per_request="10")

    assert await gate.effective_limits() == BudgetLimits(
        Decimal("100"), Decimal("10"), "environment"
    )


async def test_effective_limits_read_the_given_connection_not_the_pool() -> None:
    pool_conn = _Conn(limits=(Decimal("1"), Decimal("1")))
    gate = _gate(pool_conn, monthly="100", per_request="10")

    limits = await gate.effective_limits(_Conn())

    assert (limits.monthly_budget_usd, limits.per_request_max_usd) == (
        Decimal("100"),
        Decimal("10"),
    )
    assert pool_conn.calls == []


# --- check_available -----------------------------------------------------------------------


async def test_check_available_takes_the_budget_lock_and_admits_at_the_exact_caps() -> None:
    conn = _Conn(spend=Decimal("90"))
    gate = _gate(conn, monthly="100", per_request="10")

    # estimate == per-request cap and spend + estimate == monthly budget: both admitted.
    await gate.check_available(Decimal("10"))

    lock_sql, lock_args = conn.calls[0]
    assert _normalise(lock_sql) == _LOCK_SQL
    assert lock_args == (PITWALL_BUDGET_LOCK_KEY,)


async def test_check_available_on_a_given_connection_uses_its_limits() -> None:
    pool_conn = _Conn(limits=(Decimal("100"), Decimal("1")))
    caller_conn = _Conn()
    gate = _gate(pool_conn, monthly="100", per_request="10")

    await gate.check_available(Decimal("5"), _conn=caller_conn)

    assert pool_conn.calls == []


async def test_per_request_rejection_logs_estimate_and_cap(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = _gate(_Conn(), per_request="1")

    with (
        caplog.at_level(logging.WARNING, logger="pitwall.cost.budget_gate"),
        pytest.raises(BudgetRejected) as rejected,
    ):
        await gate.check_available(Decimal("5"))

    assert rejected.value.reason == "per_request_cap"
    assert [record.getMessage() for record in caplog.records] == [
        "per-request cap exceeded: 5.000000 > 1.000000"
    ]


async def test_monthly_rejection_logs_spend_estimate_and_budget(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gate = _gate(_Conn(spend=Decimal("98")), monthly="100", per_request="10")

    with (
        caplog.at_level(logging.WARNING, logger="pitwall.cost.budget_gate"),
        pytest.raises(BudgetRejected) as rejected,
    ):
        await gate.check_available(Decimal("3"))

    assert rejected.value.reason == "monthly_budget"
    assert [record.getMessage() for record in caplog.records] == [
        "monthly budget would exceed under advisory lock: 98.000000 + 3.000000 > 100.000000"
    ]


# --- try_launch / try_launch_admission -----------------------------------------------------


async def test_try_launch_defaults_to_inference_and_persists_every_column() -> None:
    conn = _Conn()
    gate = _gate(conn, workload_id="wkl_plain")
    submitted = dt.datetime(2026, 5, 1, 12, tzinfo=dt.UTC)

    workload_id = await gate.try_launch(
        capability_id="cap_1",
        provider_id="prov_1",
        estimate_usd=Decimal("2"),
        submitted_at=submitted,
    )

    assert workload_id == "wkl_plain"
    assert _inserted_workload(conn) == {
        "id": "wkl_plain",
        "capability_id": "cap_1",
        "provider_id": "prov_1",
        "type": "inference",
        "state": "queued",
        "cost_estimate_usd": Decimal("2"),
        "submitted_at": submitted,
        "cost_ceiling_usd": Decimal("2"),
        "cost_quote": None,
    }


async def test_try_launch_admission_defaults_to_inference() -> None:
    conn = _Conn()

    await _gate(conn).try_launch_admission(
        capability_id="cap_1", provider_id="prov_1", estimate_usd=Decimal("1")
    )

    assert _inserted_workload(conn)["type"] == "inference"


async def test_idempotent_admission_persists_estimate_ceiling_and_compact_quote() -> None:
    conn = _Conn()
    submitted = dt.datetime(2026, 5, 1, 12, tzinfo=dt.UTC)

    admission = await _gate(conn, workload_id="wkl_key").try_launch_admission(
        capability_id="cap_1",
        provider_id="prov_1",
        estimate_usd=_Quote(Decimal("1"), Decimal("4")),
        submitted_at=submitted,
        idempotency_key="key-1",
    )

    assert admission.workload_id == "wkl_key"
    assert admission.is_new is True
    assert _inserted_workload(conn) == {
        "id": "wkl_key",
        "capability_id": "cap_1",
        "provider_id": "prov_1",
        "type": "inference",
        "state": "queued",
        "cost_estimate_usd": Decimal("1"),
        "submitted_at": submitted,
        "idempotency_key": "key-1",
        "cost_ceiling_usd": Decimal("4"),
        "cost_quote": '{"ceiling":"4","estimate":"x","model":"per_unit"}',
    }


async def test_ceiling_only_estimate_persists_no_quote() -> None:
    conn = _Conn()

    await _gate(conn).try_launch_admission(
        capability_id="cap_1", provider_id="prov_1", estimate_usd=_CeilingOnly(Decimal("3"))
    )

    workload = _inserted_workload(conn)
    assert workload["cost_estimate_usd"] == Decimal("3")
    assert workload["cost_ceiling_usd"] == Decimal("3")
    assert workload["cost_quote"] is None


async def test_idempotency_hit_logs_the_existing_workload(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn = _Conn(existing_id="wkl_existing")

    with caplog.at_level(logging.INFO, logger="pitwall.cost.budget_gate"):
        admission = await _gate(conn).try_launch_admission(
            capability_id="cap_1",
            provider_id="prov_1",
            estimate_usd=Decimal("1"),
            idempotency_key="key-1",
        )

    assert admission.is_new is False
    assert [record.getMessage() for record in caplog.records] == [
        "idempotency key hit: returning existing workload wkl_existing"
    ]


# --- estimate validation -------------------------------------------------------------------


def _exactly(message: str) -> Any:
    return pytest.raises(ValueError, match=f"^{re.escape(message)}$")


@pytest.mark.parametrize(
    ("estimate", "message"),
    [
        (_Quote("nope", Decimal("1")), "estimate_usd must be a decimal value"),
        (_Quote(Decimal("1"), "nope"), "ceiling_usd must be a decimal value"),
        (_CeilingOnly("nope"), "estimate_usd must be a decimal value"),
        (_Quote(Decimal("-1"), Decimal("1")), "estimate_usd must be non-negative"),
        (_Quote(Decimal("0"), Decimal("-1")), "ceiling_usd must be non-negative"),
        (
            _Quote(Decimal("2"), Decimal("1")),
            "ceiling_usd must be greater than or equal to estimate_usd",
        ),
        (1.5, "estimate_usd must be a Decimal, decimal string, or integer"),
    ],
)
async def test_admission_rejects_malformed_estimates_naming_the_field(
    estimate: Any, message: str
) -> None:
    conn = _Conn()

    with _exactly(message):
        await _gate(conn).check_available(estimate)

    assert conn.calls == []
