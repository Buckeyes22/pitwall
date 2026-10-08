"""Migration 0037: a submitted_at index serves the budget gate's month-to-date query."""

from __future__ import annotations

from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "db/migrations/0037_workloads_submitted_at_index.sql"
)


def test_0037_creates_the_plain_index_and_drops_the_partial_one() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert (
        "CREATE INDEX IF NOT EXISTS idx_workloads_submitted_at ON pitwall.workloads (submitted_at)"
        in sql
    )
    assert "DROP INDEX IF EXISTS pitwall.idx_workloads_month_spend" in sql
