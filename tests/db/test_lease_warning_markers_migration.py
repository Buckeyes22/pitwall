from __future__ import annotations

from pathlib import Path

_MIGRATION = Path(__file__).resolve().parents[2] / "db/migrations/0026_lease_warning_markers.sql"


def test_warning_marker_migration_persists_thresholds_and_capability_name() -> None:
    sql = _MIGRATION.read_text()

    assert "warning_expires_at TIMESTAMPTZ" in sql
    assert "warning_thresholds INTEGER[] NOT NULL" in sql
    assert "capability_name TEXT" in sql
    assert "CREATE TRIGGER trg_leases_capability_name" in sql
    assert "ALTER COLUMN capability_name SET NOT NULL" not in sql
