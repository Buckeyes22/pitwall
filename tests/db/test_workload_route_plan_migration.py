"""ROUTE-01 append-only workload plan persistence contract."""

from __future__ import annotations

from pathlib import Path

from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).parents[2]
_MIGRATION = _ROOT / "db/migrations/0031_workload_route_plan.sql"


def test_route_plan_migration_follows_billing_identity_record() -> None:
    records = discover_migrations(_ROOT / "db/migrations")
    versions = [record.version for record in records]

    assert "0031_workload_route_plan" in versions
    assert (
        versions.index("0031_workload_route_plan")
        == versions.index("0030_lease_workload_billing_identity") + 1
    )
    assert [
        record.filename for record in records if record.version == "0031_workload_route_plan"
    ] == [_MIGRATION.name]


def test_route_plan_migration_requires_paired_safe_document() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")

    assert "ADD COLUMN route_plan_id TEXT" in sql
    assert "ADD COLUMN route_plan JSONB" in sql
    assert "workloads_route_plan_pair_check" in sql
    assert "jsonb_typeof(route_plan) = 'object'" in sql
    assert "route_plan_id ~ '^plan_[0-9a-f]{32}$'" in sql
    assert "route_plan ? 'plan_id'" in sql
    assert "route_plan ->> 'plan_id' IS NOT NULL" in sql
    assert "route_plan ->> 'plan_id' = route_plan_id" in sql
    assert "ADD COLUMN route_attempts JSONB NOT NULL DEFAULT '[]'::jsonb" in sql
    assert "jsonb_array_length(route_attempts) <= 100" in sql
    assert "pg_column_size(route_plan) <= 1048576" in sql
    assert "idx_workloads_route_plan_id" in sql
