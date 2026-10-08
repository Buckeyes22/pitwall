"""Spend must be reconciled from pod uptime, and the doc must say why.

Live: a default billing query returned daily buckets ending 23 days before the
run with podGpuAmount 0 throughout; an explicit same-day window returned zero
records though three pods had been billed that day; and granularity: "hourly"
came back as bucketSize: "day" without an error.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

from pitwall.cost.reconcile_cost import CostTruthUpStatus
from pitwall.providers.interface import ProviderCapability

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "operations" / "cost-reconciliation.md"


def _section(heading: str) -> str:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(heading)}$(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"{DOC.name} has no '## {heading}' section"
    return match.group(1)


def _code_spans(body: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", body))


def test_the_runbook_exists() -> None:
    assert DOC.is_file()


def test_it_records_all_three_billing_observations_as_wire_fields() -> None:
    # One quoted response field per observed limit: reporting lag (all-zero default buckets), an
    # empty same-day window, and a granularity that was not honoured.
    spans = _code_spans(_section("RunPod reporting observations"))

    assert {"podGpuAmount", "recordCount: 0", 'granularity: "hourly"', 'bucketSize: "day"'} <= spans


def test_operator_response_names_a_real_truth_up_status() -> None:
    statuses = set(get_args(CostTruthUpStatus))
    named = _code_spans(_section("Operator response")) & {
        "actual_unavailable",
        "reconciled",
        "in_sync",
    }

    assert named == {"actual_unavailable"}
    assert named <= statuses


def test_current_support_state_points_at_things_that_exist() -> None:
    body = _section("Current support state")
    spans = _code_spans(body)

    assert "ACTUAL_COST" in spans
    assert ProviderCapability["ACTUAL_COST"].value == "actual_cost"
    assert list((ROOT / "db" / "migrations").glob("0030_*.sql"))
    for path in re.findall(r"`(docs/[^`]+\.md)`", body):
        assert (ROOT / path).is_file(), path
