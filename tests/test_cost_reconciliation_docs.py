"""Spend must be reconciled from pod uptime, and the doc must say why.

Live: a default billing query returned daily buckets ending 23 days before the
run with podGpuAmount 0 throughout; an explicit same-day window returned zero
records though three pods had been billed that day; and granularity: "hourly"
came back as bucketSize: "day" without an error.
"""

from __future__ import annotations

from pathlib import Path

DOC = Path(__file__).parents[1] / "docs" / "operations" / "cost-reconciliation.md"


def test_the_runbook_exists() -> None:
    assert DOC.is_file()


def test_it_records_all_three_billing_limits() -> None:
    text = DOC.read_text().lower()
    for phrase in ("reporting lag", "granularity", "pod uptime"):
        assert phrase in text, f"the runbook does not mention {phrase!r}"
