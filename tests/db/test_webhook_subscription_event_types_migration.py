from __future__ import annotations

from pathlib import Path

_MIGRATION = (
    Path(__file__).resolve().parents[2] / "db/migrations/0025_webhook_subscription_event_types.sql"
)


def test_event_type_migration_allows_expiring_and_preserves_completion_default() -> None:
    sql = _MIGRATION.read_text()

    assert "DEFAULT ARRAY['workload.completed']::TEXT[]" in sql
    assert "'lease.expiring'" in sql
