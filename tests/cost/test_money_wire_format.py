"""Cost reads put money on the wire as decimal strings, never JSON floats."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

from pitwall.cost.read_models import (
    CostSummaryEntry,
    CostSummaryRead,
    RecentWorkloadsRead,
    WorkloadCostRead,
    WorkloadCostRecord,
)


def test_cost_read_money_is_string() -> None:
    summary = CostSummaryRead(
        total_usd=Decimal("1.250000"),
        entries=(
            CostSummaryEntry(
                day=dt.date(2026, 9, 1),
                capability_class="llm",
                provider_type="runpod",
                workload_count=1,
                cost_usd=Decimal("1.250000"),
            ),
        ),
    )
    workloads = RecentWorkloadsRead(
        workloads=(
            WorkloadCostRecord(
                fields={"id": "wkl-cost"},
                cost=WorkloadCostRead(estimate=Decimal("1.250000"), actual=Decimal("1.000000")),
            ),
            WorkloadCostRecord(
                fields={"id": "wkl-open"},
                cost=WorkloadCostRead(estimate=Decimal("0.100000"), actual=None),
            ),
        )
    )

    wire_summary = summary.to_legacy_serializable_dict()
    wire_workloads = workloads.to_legacy_serializable_dict()["workloads"]

    assert wire_summary["total_usd"] == "1.250000"
    assert wire_summary["entries"][0]["cost_usd"] == "1.250000"
    assert wire_workloads[0]["cost_estimate_usd"] == "1.250000"
    assert wire_workloads[0]["cost_actual_usd"] == "1.000000"
    assert wire_workloads[1]["cost_actual_usd"] is None
    assert "1.250000" in json.dumps(wire_summary)
    assert not _contains_float(wire_summary)
    assert not _contains_float(wire_workloads)


def _contains_float(value: object) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_float(item) for item in value)
    return False
