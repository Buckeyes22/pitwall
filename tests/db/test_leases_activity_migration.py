"""Migration 0024: the additive lease activity columns, checks, and idle index."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog, squash

pytestmark = pytest.mark.integration


async def test_lease_activity_migration_has_exact_additive_columns(db_catalog: Catalog) -> None:
    await db_catalog.expect_columns(
        "leases",
        last_traffic_at="timestamptz",
        ready_at="timestamptz",
        idle_timeout_min="int4",
        max_usd_per_hour="numeric(12,4)",
    )


async def test_lease_activity_migration_bounds_the_policy_columns(db_catalog: Catalog) -> None:
    constraints = await db_catalog.constraints("leases")
    idle = next(c for c in constraints.values() if c.columns == ("idle_timeout_min",))
    budget = next(c for c in constraints.values() if c.columns == ("max_usd_per_hour",))

    assert idle.kind == budget.kind == "check"
    assert idle.contains("idle_timeout_min IS NULL OR idle_timeout_min >= 5")
    assert budget.contains("max_usd_per_hour IS NULL OR max_usd_per_hour > 0")


async def test_lease_activity_migration_indexes_only_idle_managed_leases(
    db_catalog: Catalog,
) -> None:
    index = await db_catalog.index("leases", "idx_leases_idle")

    assert index.columns == ("state", "last_traffic_at")
    assert not index.unique
    assert squash(index.predicate) == squash("idle_timeout_min IS NOT NULL")
