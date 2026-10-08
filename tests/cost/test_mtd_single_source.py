"""Month-to-date spend has one definition, and its month boundary is UTC-safe."""

from __future__ import annotations

import ast
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pitwall.cost import budget_gate
from pitwall.cost.budget_gate import month_to_date_spend

SRC = Path(__file__).resolve().parents[2] / "src" / "pitwall"
_BUDGET_GATE = SRC / "cost" / "budget_gate.py"


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


def test_all_readers_use_shared_function() -> None:
    """No module other than the budget gate spells a workload month-to-date query."""
    offenders: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        if path == _BUDGET_GATE:
            continue
        for text in _string_constants(path):
            lowered = text.lower()
            if "date_trunc('month'" in lowered and "workloads" in lowered:
                offenders.append(f"{path.relative_to(SRC)}: {text[:60]!r}")
            if "pitwall.workloads" in lowered and "submitted_at >=" in lowered:
                offenders.append(f"{path.relative_to(SRC)}: {text[:60]!r}")
    assert offenders == []


@pytest.mark.parametrize(
    "module",
    [
        "pitwall.cost.alerts",
        "pitwall.cost.budget_limits",
        "pitwall.cost.exporter",
        "pitwall.tui.cost",
        "pitwall.audit.capability",
        "pitwall.doctor",
    ],
)
def test_reader_modules_call_the_shared_function(module: str) -> None:
    path = SRC.parent / Path(*module.split(".")).with_suffix(".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "month_to_date_spend" in called, f"{module} does not call month_to_date_spend"


class _RecordingConn:
    """A connection whose session time zone is deliberately not UTC."""

    session_time_zone = "America/Los_Angeles"

    def __init__(self) -> None:
        self.queries: list[tuple[str, tuple[Any, ...]]] = []

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Decimal]:
        self.queries.append((query, args))
        return {"s": Decimal("12.5")}


def _compact(query: str) -> str:
    return " ".join(query.split())


def _boundary_is_session_independent(query: str) -> bool:
    """Every ``submitted_at`` bound is a UTC month start turned into an absolute instant.

    ``submitted_at >= <timestamp>`` compares in the session time zone, which moves the month
    boundary; ``<timestamp> AT TIME ZONE 'UTC'`` fixes the instant, so the session zone is moot.
    """
    bounds = re.findall(r"submitted_at\s*(?:>=|<)\s*(.+?)(?= AND |$)", _compact(query))
    return bool(bounds) and all(
        "date_trunc('month'" in bound and bound.endswith("AT TIME ZONE 'UTC'") for bound in bounds
    )


@pytest.mark.anyio
async def test_month_boundary_utc_under_non_utc_session() -> None:
    conn = _RecordingConn()

    spend = await month_to_date_spend(conn)

    assert spend == Decimal("12.5")
    ((query, args),) = conn.queries
    assert conn.session_time_zone != "UTC"
    assert _boundary_is_session_independent(query)
    assert args == ()


@pytest.mark.anyio
async def test_month_boundary_utc_for_a_given_month() -> None:
    conn = _RecordingConn()
    at = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)

    await month_to_date_spend(conn, at=at)

    ((query, args),) = conn.queries
    assert _boundary_is_session_independent(query)
    assert len(re.findall(r"submitted_at\s*(?:>=|<)", _compact(query))) == 2
    assert args == (at,)


def test_sql_constants_have_the_utc_safe_boundary() -> None:
    assert _boundary_is_session_independent(budget_gate.MONTH_TO_DATE_SPEND_SQL)
    assert _boundary_is_session_independent(budget_gate.MONTH_SPEND_AT_SQL)
    assert _boundary_is_session_independent(budget_gate.MONTH_TO_DATE_WHERE)
