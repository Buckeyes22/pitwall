"""Route-plan quote normalization is the next append-only migration."""

from __future__ import annotations

from pathlib import Path

from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).parents[2]
_MIGRATION = _ROOT / "db/migrations/0032_normalize_route_plan_cost_quotes.sql"
_NEW_MIGRATION = _ROOT / "db/migrations/0043_idempotency_body_hash.sql"


def test_normalize_migration_is_next_append_only_record() -> None:
    records = discover_migrations(_ROOT / "db/migrations")

    assert _MIGRATION.name in {rec.filename for rec in records}
    assert records[-1].filename == _NEW_MIGRATION.name
    assert records[-1].version == "0043_idempotency_body_hash"


def test_normalize_migration_rewrites_only_route_plan_quotes() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")

    assert "cost_quote ->> 'model' = 'route_plan'" in sql
    assert "cost_quote ? 'plan_id'" in sql
    assert "cost_quote - 'plan_id'" in sql
    assert "'unit', 'attempt'" in sql
    assert "'ceiling_count', '1'" in sql
    assert "jsonb_array_elements(cost_quote -> 'components')" in sql
