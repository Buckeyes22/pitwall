"""No month-to-date spend query is written outside ``cost/budget_gate.py`` (review finding #5)."""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "pitwall"
_MONTH_TRUNC = re.compile(r"date_trunc\('month'")


def test_only_the_budget_gate_defines_month_to_date_workload_spend() -> None:
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "budget_gate.py" and path.parent.name == "cost":
            continue
        text = path.read_text(encoding="utf-8")
        for match in _MONTH_TRUNC.finditer(text):
            window = text[max(0, match.start() - 400) : match.end() + 200]
            if "pitwall.workloads" in window:
                line = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{path.relative_to(SRC)}:{line}")
    assert offenders == [], (
        "month-to-date workload spend must use MONTH_TO_DATE_SPEND_SQL, MONTH_SPEND_AT_SQL, "
        f"or MONTH_TO_DATE_WHERE from pitwall.cost.budget_gate: {offenders}"
    )
