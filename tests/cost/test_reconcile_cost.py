"""Hermetic tests for provider billing truth-up against cost_daily."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.cost.reconcile_cost import (
    PITWALL_COST_TRUTH_UP_LOCK_KEY,
    AsyncpgCostTruthUpRepository,
    CostReconcileAdjustment,
    CostReconcilePlan,
    CostReconcileWindow,
    ProviderActualCostResult,
    ProviderActualCostWindow,
    ProviderActualWorkloadCost,
    RecordedCostWindow,
    reconcile_cost,
    reconcile_provider_actual_cost,
)

pytestmark = pytest.mark.anyio

_DAY = dt.date(2026, 6, 1)


def _window(
    *,
    day: dt.date = _DAY,
    capability_class: str = "embedding",
    provider_type: str = "serverless_lb",
) -> CostReconcileWindow:
    return CostReconcileWindow(
        day=day,
        capability_class=capability_class,
        provider_type=provider_type,
    )


def test_reconcile_cost_emits_positive_adjustment_for_provider_overage() -> None:
    plan = reconcile_cost(
        recorded=[
            RecordedCostWindow(
                window=_window(),
                recorded_usd=Decimal("10.000000"),
                workload_count=3,
            )
        ],
        provider_actuals=[
            ProviderActualCostWindow(
                window=_window(),
                actual_usd=Decimal("12.345678"),
                source="runpod-billing",
            )
        ],
    )

    assert len(plan.adjustments) == 1
    adjustment = plan.adjustments[0]
    assert adjustment.window == _window()
    assert adjustment.recorded_usd == Decimal("10.000000")
    assert adjustment.provider_actual_usd == Decimal("12.345678")
    assert adjustment.adjustment_usd == Decimal("2.345678")
    assert adjustment.direction == "increase"
    assert adjustment.sources == ("runpod-billing",)
    assert plan.total_adjustment_usd == Decimal("2.345678")


def test_reconcile_cost_emits_negative_adjustment_for_provider_underrun() -> None:
    plan = reconcile_cost(
        recorded=[
            RecordedCostWindow(
                window=_window(),
                recorded_usd=Decimal("12.000000"),
                workload_count=2,
            )
        ],
        provider_actuals=[
            ProviderActualCostWindow(
                window=_window(),
                actual_usd=Decimal("9.250000"),
                source="runpod-billing",
            )
        ],
    )

    assert len(plan.adjustments) == 1
    adjustment = plan.adjustments[0]
    assert adjustment.adjustment_usd == Decimal("-2.750000")
    assert adjustment.direction == "decrease"
    assert plan.total_adjustment_usd == Decimal("-2.750000")


def test_reconcile_cost_omits_windows_within_tolerance() -> None:
    plan = reconcile_cost(
        recorded=[
            RecordedCostWindow(
                window=_window(),
                recorded_usd=Decimal("10.000000"),
                workload_count=1,
            )
        ],
        provider_actuals=[
            ProviderActualCostWindow(
                window=_window(),
                actual_usd=Decimal("10.000001"),
                source="runpod-billing",
            )
        ],
        tolerance_usd=Decimal("0.000001"),
    )

    assert plan.adjustments == ()
    assert plan.total_adjustment_usd == Decimal("0.000000")
    assert plan.window_count == 1


def test_reconcile_cost_uses_recorded_provider_window_union() -> None:
    provider_only = _window(capability_class="llm")
    recorded_only = _window(provider_type="lambda_cloud")

    plan = reconcile_cost(
        recorded=[
            RecordedCostWindow(
                window=recorded_only,
                recorded_usd=Decimal("5.000000"),
                workload_count=1,
            )
        ],
        provider_actuals=[
            ProviderActualCostWindow(
                window=provider_only,
                actual_usd=Decimal("7.500000"),
                source="lambda-billing",
            )
        ],
    )

    assert [item.window for item in plan.adjustments] == [recorded_only, provider_only]
    assert [item.adjustment_usd for item in plan.adjustments] == [
        Decimal("-5.000000"),
        Decimal("7.500000"),
    ]


def test_reconcile_cost_groups_duplicates_and_sorts_deterministically() -> None:
    first = _window(day=dt.date(2026, 6, 1), capability_class="embedding")
    second = _window(day=dt.date(2026, 6, 2), capability_class="llm")

    plan = reconcile_cost(
        recorded=[
            RecordedCostWindow(window=second, recorded_usd=Decimal("1.000000")),
            RecordedCostWindow(window=first, recorded_usd=Decimal("2.000000")),
            RecordedCostWindow(window=first, recorded_usd=Decimal("0.500000")),
        ],
        provider_actuals=[
            ProviderActualCostWindow(
                window=first,
                actual_usd=Decimal("4.000000"),
                source="runpod-b",
            ),
            ProviderActualCostWindow(
                window=second,
                actual_usd=Decimal("1.250000"),
                source="runpod-a",
            ),
            ProviderActualCostWindow(
                window=first,
                actual_usd=Decimal("0.500000"),
                source="runpod-a",
            ),
        ],
    )

    assert [item.window for item in plan.adjustments] == [first, second]
    assert plan.adjustments[0].recorded_usd == Decimal("2.500000")
    assert plan.adjustments[0].provider_actual_usd == Decimal("4.500000")
    assert plan.adjustments[0].sources == ("runpod-a", "runpod-b")
    assert plan.adjustments[1].adjustment_usd == Decimal("0.250000")


def test_reconcile_cost_quantizes_money_to_six_decimal_places() -> None:
    plan = reconcile_cost(
        recorded=[
            RecordedCostWindow(
                window=_window(),
                recorded_usd=Decimal("1.0000004"),
            )
        ],
        provider_actuals=[
            ProviderActualCostWindow(
                window=_window(),
                actual_usd=Decimal("1.0000015"),
                source="runpod-billing",
            )
        ],
    )

    assert plan.adjustments[0].recorded_usd == Decimal("1.000000")
    assert plan.adjustments[0].provider_actual_usd == Decimal("1.000002")
    assert plan.adjustments[0].adjustment_usd == Decimal("0.000002")


def test_reconcile_plan_serializes_decimals_as_strings() -> None:
    plan = reconcile_cost(
        recorded=[RecordedCostWindow(window=_window(), recorded_usd=Decimal("3"))],
        provider_actuals=[
            ProviderActualCostWindow(
                window=_window(),
                actual_usd=Decimal("4.25"),
                source="runpod-billing",
            )
        ],
    )

    assert plan.to_serializable_dict() == {
        "window_count": 1,
        "adjustment_count": 1,
        "total_adjustment_usd": "1.250000",
        "adjustments": [
            {
                "day": "2026-06-01",
                "capability_class": "embedding",
                "provider_type": "serverless_lb",
                "recorded_usd": "3.000000",
                "provider_actual_usd": "4.250000",
                "adjustment_usd": "1.250000",
                "direction": "increase",
                "sources": ["runpod-billing"],
            }
        ],
    }


def test_money_inputs_must_be_decimal() -> None:
    with pytest.raises(TypeError, match="recorded_usd must be Decimal"):
        RecordedCostWindow(window=_window(), recorded_usd=1.25)
    with pytest.raises(TypeError, match="actual_usd must be Decimal"):
        ProviderActualCostWindow(window=_window(), actual_usd=1.25, source="runpod")
    with pytest.raises(TypeError, match="tolerance_usd must be Decimal"):
        reconcile_cost(recorded=[], provider_actuals=[], tolerance_usd=0.01)


@dataclass
class _PoolAndConnection:
    pool: MagicMock
    conn: MagicMock


def _workload_row(
    *,
    workload_id: str = "wkl-provider-1",
    day: dt.date = _DAY,
    provider_id: str = "prov-fixture",
    state: str = "completed",
    actual_usd: Decimal | None = Decimal("1.000000"),
    provenance: str | None = None,
) -> dict[str, Any]:
    return {
        "id": workload_id,
        "provider_id": provider_id,
        "state": state,
        "day": day,
        "capability_class": "embedding",
        "provider_type": "serverless_lb",
        "cost_actual_usd": actual_usd,
        "cost_actual_provenance": provenance,
    }


def _mock_pool(rows: Iterable[dict[str, Any]]) -> _PoolAndConnection:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=list(rows))
    conn.execute = AsyncMock(return_value="SELECT 1")
    conn.executemany = AsyncMock(return_value=None)
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=transaction)
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acq)
    return _PoolAndConnection(pool=pool, conn=conn)


def _workload_actual(
    *,
    workload_id: str = "wkl-provider-1",
    amount: str = "2.000000",
    source: str = "provider-billing-api",
) -> ProviderActualWorkloadCost:
    return ProviderActualWorkloadCost(
        workload_id=workload_id,
        actual_usd=Decimal(amount),
        source=source,
    )


def _provider_actual_result() -> ProviderActualCostResult:
    return ProviderActualCostResult(
        provider_id="prov-fixture",
        availability="available",
        source="provider-billing-api",
        observed_at=dt.datetime(2026, 6, 2, tzinfo=dt.UTC),
        workloads=(_workload_actual(),),
    )


async def test_asyncpg_truth_up_persists_workload_actual_and_refreshes_rollup() -> None:
    pair = _mock_pool([_workload_row()])
    repo = AsyncpgCostTruthUpRepository(pair.pool)
    observed_at = dt.datetime(2026, 6, 2, tzinfo=dt.UTC)

    plan, applied = await repo.truth_up(
        provider_id="prov-fixture",
        observed_at=observed_at,
        start_day=_DAY,
        end_day=dt.date(2026, 6, 2),
        provider_actuals=(_workload_actual(),),
    )

    pair.conn.transaction.assert_called_once_with()
    pair.conn.execute.assert_awaited_once_with(
        "SELECT pg_advisory_xact_lock($1)",
        PITWALL_COST_TRUTH_UP_LOCK_KEY,
    )
    fetch_sql = pair.conn.fetch.await_args.args[0]
    assert "FROM pitwall.workloads" in fetch_sql
    assert "FOR UPDATE OF w" in fetch_sql
    assert plan.adjustment_count == 1
    assert plan.adjustments[0].workload_id == "wkl-provider-1"
    assert applied == 1
    update_call, refresh_call = pair.conn.executemany.await_args_list
    assert "SET cost_actual_usd = $1" in update_call.args[0]
    assert "cost_actual_provenance = $2" in update_call.args[0]
    assert update_call.args[1] == [
        (
            Decimal("2.000000"),
            "provider-billing-api",
            observed_at,
            "wkl-provider-1",
        )
    ]
    assert "COALESCE(SUM(w.cost_actual_usd), 0)" in refresh_call.args[0]
    assert refresh_call.args[1] == [(_DAY, "embedding", "serverless_lb")]


async def test_asyncpg_truth_up_rejects_wrong_provider_nonterminal_and_range() -> None:
    cases = [
        (_workload_row(provider_id="prov-other"), "does not belong"),
        (_workload_row(state="running"), "is not terminal"),
        (_workload_row(day=dt.date(2026, 6, 2)), "falls outside"),
    ]
    for row, message in cases:
        pair = _mock_pool([row])
        repo = AsyncpgCostTruthUpRepository(pair.pool)
        with pytest.raises(ValueError, match=message):
            await repo.truth_up(
                provider_id="prov-fixture",
                observed_at=dt.datetime(2026, 6, 2, tzinfo=dt.UTC),
                start_day=_DAY,
                end_day=dt.date(2026, 6, 2),
                provider_actuals=(_workload_actual(),),
            )
        pair.conn.executemany.assert_not_awaited()


class _MemoryTruthUpRepository:
    def __init__(self) -> None:
        self.amount = Decimal("1.000000")
        self.source: str | None = None
        self.call_count = 0

    async def truth_up(
        self,
        *,
        provider_id: str,
        observed_at: dt.datetime,
        start_day: dt.date,
        end_day: dt.date,
        provider_actuals: Iterable[ProviderActualWorkloadCost],
        tolerance_usd: Decimal,
    ) -> tuple[CostReconcilePlan, int]:
        del observed_at, start_day, end_day, tolerance_usd
        self.call_count += 1
        assert provider_id == "prov-fixture"
        actual = tuple(provider_actuals)[0]
        if self.amount == actual.actual_usd and self.source == actual.source:
            return CostReconcilePlan(adjustments=(), window_count=1), 0
        adjustment = actual.actual_usd - self.amount
        plan = CostReconcilePlan(
            adjustments=(
                CostReconcileAdjustment(
                    window=_window(),
                    workload_id=actual.workload_id,
                    recorded_usd=self.amount,
                    provider_actual_usd=actual.actual_usd,
                    adjustment_usd=adjustment,
                    sources=(actual.source,),
                ),
            ),
            window_count=1,
        )
        self.amount = actual.actual_usd
        self.source = actual.source
        return plan, 1


async def test_provider_actual_truth_up_replay_is_idempotent() -> None:
    repository = _MemoryTruthUpRepository()
    actual = _provider_actual_result()

    first = await reconcile_provider_actual_cost(
        repository,
        start_day=_DAY,
        end_day=dt.date(2026, 6, 2),
        provider_actual=actual,
    )
    replay = await reconcile_provider_actual_cost(
        repository,
        start_day=_DAY,
        end_day=dt.date(2026, 6, 2),
        provider_actual=actual,
    )

    assert first.status == "reconciled"
    assert first.applied_count == 1
    assert first.plan.adjustments[0].workload_id == "wkl-provider-1"
    assert replay.status == "in_sync"
    assert replay.idempotent_noop is True
    assert replay.applied_count == 0
    assert repository.amount == Decimal("2.000000")


async def test_unavailable_provider_actual_is_safe_and_performs_no_repository_io() -> None:
    repository = _MemoryTruthUpRepository()
    unavailable = ProviderActualCostResult.unavailable(
        provider_id="together",
        source="together-api",
        observed_at=dt.datetime(2026, 6, 2, tzinfo=dt.UTC),
        reason="provider API exposes usage but no actual invoice amount",
    )

    result = await reconcile_provider_actual_cost(
        repository,
        start_day=_DAY,
        end_day=dt.date(2026, 6, 2),
        provider_actual=unavailable,
    )

    assert result.status == "actual_unavailable"
    assert result.plan == CostReconcilePlan(adjustments=(), window_count=0)
    assert repository.call_count == 0
    serialized = result.to_serializable_dict()
    assert serialized["status"] == "actual_unavailable"
    assert serialized["idempotent_noop"] is False
    assert serialized["provider_actual"] == {
        "provider_id": "together",
        "availability": "unavailable",
        "source": "together-api",
        "observed_at": "2026-06-02T00:00:00+00:00",
        "currency": "USD",
        "workload_count": 0,
        "unavailable_reason": "provider API exposes usage but no actual invoice amount",
        "workloads": [],
    }


def test_provider_actual_result_is_decimal_strict_unique_and_source_consistent() -> None:
    with pytest.raises(TypeError, match="actual_usd must be Decimal"):
        ProviderActualWorkloadCost(
            workload_id="wkl-1",
            actual_usd=2.0,
            source="billing",
        )
    with pytest.raises(ValueError, match="source must match"):
        ProviderActualCostResult(
            provider_id="runpod",
            availability="available",
            source="provider-billing",
            observed_at=dt.datetime(2026, 6, 2, tzinfo=dt.UTC),
            workloads=(_workload_actual(source="different-source"),),
        )
    duplicate = _workload_actual()
    with pytest.raises(ValueError, match="unique workload_id"):
        ProviderActualCostResult(
            provider_id="runpod",
            availability="available",
            source=duplicate.source,
            observed_at=dt.datetime(2026, 6, 2, tzinfo=dt.UTC),
            workloads=(duplicate, duplicate),
        )


def test_provider_actual_cost_overflow_is_a_bounded_value_error() -> None:
    with pytest.raises(ValueError, match="representable USD range"):
        ProviderActualWorkloadCost(
            workload_id="wkl-overflow",
            actual_usd=Decimal("1e999999"),
            source="provider-billing",
        )
