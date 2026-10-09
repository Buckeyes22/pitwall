"""RP-01's exact Pod billing identity migration (0030), read back from the migrated schema."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog, MigrationSandbox, squash

pytestmark = pytest.mark.integration


async def test_migration_adds_nullable_workload_identity_that_clears_on_delete(
    db_catalog: Catalog,
) -> None:
    await db_catalog.expect_columns("leases", workload_id="text")
    foreign_keys = [
        c for c in (await db_catalog.constraints("leases")).values() if c.kind == "foreign key"
    ]
    (workload_key,) = [c for c in foreign_keys if c.columns == ("workload_id",)]

    assert workload_key.contains("REFERENCES workloads(id)")
    assert workload_key.on_delete == "SET NULL"


async def test_migration_enforces_exclusive_workload_and_provider_resource_mapping(
    db_catalog: Catalog,
) -> None:
    by_workload = await db_catalog.index("leases", "idx_leases_workload_billing_identity")
    by_resource = await db_catalog.index("leases", "idx_leases_provider_resource_billing_identity")

    assert by_workload.unique
    assert by_workload.columns == ("workload_id",)
    assert squash(by_workload.predicate) == squash("workload_id IS NOT NULL")
    assert by_resource.unique
    assert by_resource.columns == ("provider_id", "external_resource_id")
    assert "workload_idisnotnull" in squash(by_resource.predicate)


async def test_migration_infers_no_identity_for_existing_leases(
    db_sandbox: MigrationSandbox,
) -> None:
    await db_sandbox.apply_through("0029_cost_quote_truth_up")
    connection = db_sandbox.connection
    await connection.execute(
        "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state, submitted_at) "
        "VALUES ('wl_1', 'cap', 'prov', 'pod', 'completed', now())"
    )
    await connection.execute(
        "INSERT INTO pitwall.leases (id, provider_id, runpod_pod_id, state, created_at, "
        "expires_at, renewal_policy) VALUES ('lease_1', 'prov', 'pod_1', 'stopped', now(), "
        "now() + interval '1 hour', 'none')"
    )

    await db_sandbox.apply_through("0030_lease_workload_billing_identity")

    assert await connection.fetchval("SELECT workload_id FROM pitwall.leases") is None
