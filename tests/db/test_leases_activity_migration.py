from __future__ import annotations

from pathlib import Path

from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "db" / "migrations" / "0024_leases_activity.sql"


def test_lease_activity_migration_has_exact_additive_columns_and_index() -> None:
    sql = _MIGRATION.read_text()

    assert "ALTER TABLE pitwall.leases" in sql
    assert "ADD COLUMN last_traffic_at TIMESTAMPTZ" in sql
    assert "ADD COLUMN ready_at TIMESTAMPTZ" in sql
    assert "ADD COLUMN idle_timeout_min INTEGER" in sql
    assert "idle_timeout_min IS NULL OR idle_timeout_min >= 5" in sql
    assert "ADD COLUMN max_usd_per_hour NUMERIC(12,4)" in sql
    assert "max_usd_per_hour IS NULL OR max_usd_per_hour > 0" in sql
    assert "CREATE INDEX idx_leases_idle" in sql
    assert "ON pitwall.leases(state, last_traffic_at)" in sql
    assert "WHERE idle_timeout_min IS NOT NULL" in sql


def test_lease_activity_migration_is_discovered_from_disk_in_version_order() -> None:
    on_disk = sorted(path.name for path in (_ROOT / "db" / "migrations").glob("*.sql"))
    names = [record.filename for record in discover_migrations()]

    assert names == on_disk
    assert "0024_leases_activity.sql" in names
