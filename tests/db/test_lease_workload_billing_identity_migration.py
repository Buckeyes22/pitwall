"""Static contracts for RP-01's exact Pod billing identity migration."""

from pathlib import Path

_MIGRATION = (
    Path(__file__).parents[2] / "db/migrations/0030_lease_workload_billing_identity.sql"
).read_text(encoding="utf-8")


def test_migration_adds_nullable_non_inferred_workload_identity() -> None:
    assert "ADD COLUMN workload_id TEXT REFERENCES pitwall.workloads(id)" in _MIGRATION
    assert "ON DELETE SET NULL" in _MIGRATION
    assert "UPDATE pitwall.leases" not in _MIGRATION


def test_migration_enforces_exclusive_workload_and_provider_resource_mapping() -> None:
    assert "CREATE UNIQUE INDEX idx_leases_workload_billing_identity" in _MIGRATION
    assert "CREATE UNIQUE INDEX idx_leases_provider_resource_billing_identity" in _MIGRATION
    assert "WHERE workload_id IS NOT NULL" in _MIGRATION
