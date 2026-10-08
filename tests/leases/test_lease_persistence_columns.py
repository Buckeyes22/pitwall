"""The lease upsert must persist every operator-supplied control.

Migration 0024 added idle_timeout_min and max_usd_per_hour for the activity and
spend controls, and the Lease model carries both, but the INSERT listed twelve
columns and neither of these. A serve therefore recorded no idle timeout, and the
partial index the idle sweep relies on (WHERE idle_timeout_min IS NOT NULL) could
never match a serve-created lease.
"""

from __future__ import annotations

import re
from pathlib import Path

_LAUNCH = Path(__file__).resolve().parents[2] / "src" / "pitwall" / "api" / "leases" / "launch.py"

_REQUIRED = ("idle_timeout_min", "max_usd_per_hour")


def _upsert_sql() -> str:
    source = _LAUNCH.read_text()
    start = source.index("INSERT INTO pitwall.leases")
    end = source.index("RETURNING *", start)
    return source[start:end]


def test_upsert_inserts_the_operator_controls() -> None:
    sql = _upsert_sql()
    missing = [column for column in _REQUIRED if column not in sql]
    assert not missing, f"the lease upsert drops {missing}"


def test_upsert_updates_the_operator_controls_on_conflict() -> None:
    sql = _upsert_sql()
    conflict = sql[sql.index("ON CONFLICT") :]
    missing = [column for column in _REQUIRED if f"{column} = EXCLUDED.{column}" not in conflict]
    assert not missing, f"the conflict branch drops {missing}"


def test_placeholder_count_matches_the_column_count() -> None:
    """A hand-written INSERT drifts silently; count both sides."""
    sql = _upsert_sql()
    columns = sql[sql.index("(") + 1 : sql.index(")")]
    column_count = len([part for part in columns.split(",") if part.strip()])
    values = sql[sql.index("VALUES") : sql.index("ON CONFLICT")]
    placeholders = {int(match) for match in re.findall(r"\$(\d+)", values)}
    assert len(placeholders) == column_count, (
        f"{column_count} columns but {len(placeholders)} placeholders"
    )
