"""Migration 0022 adds capabilities.served_model_id (text-level assertions; hermetic)."""

from __future__ import annotations

from pathlib import Path

from pitwall.migrations import discover_migrations

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_MIGRATION = _REPO_ROOT / "db" / "migrations" / "0022_capability_served_model.sql"


def test_served_model_migration_adds_nullable_text_column() -> None:
    sql = _MIGRATION.read_text()
    assert "ALTER TABLE pitwall.capabilities" in sql
    assert "ADD COLUMN served_model_id TEXT NULL" in sql


def test_served_model_migration_is_discovered_before_lease_activity() -> None:
    names = [record.filename for record in discover_migrations()]

    assert names.index("0022_capability_served_model.sql") < names.index("0024_leases_activity.sql")
    assert names == sorted(names)
