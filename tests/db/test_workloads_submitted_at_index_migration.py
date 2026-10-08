"""Migration 0037: a submitted_at index serves the budget gate's month-to-date query."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog, MigrationSandbox

pytestmark = pytest.mark.integration


async def test_0037_has_the_plain_index_and_no_partial_one(db_catalog: Catalog) -> None:
    indexes = await db_catalog.indexes("workloads")

    plain = indexes["idx_workloads_submitted_at"]
    assert plain.columns == ("submitted_at",)
    assert plain.predicate is None
    assert not plain.unique
    assert "idx_workloads_month_spend" not in indexes


async def test_0037_is_safe_to_reapply(db_sandbox: MigrationSandbox) -> None:
    # Guards the migration's `IF NOT EXISTS` create and `IF EXISTS` drop: re-running it, as a
    # repaired ledger would, must not fail and must leave the same index state.
    await db_sandbox.apply_through("0037_workloads_submitted_at_index")
    catalog = Catalog(db_sandbox.connection)
    before = await catalog.indexes("workloads")

    await db_sandbox.reapply("0037_workloads_submitted_at_index")

    after = await catalog.indexes("workloads")
    assert set(after) == set(before)
    assert after["idx_workloads_submitted_at"] == before["idx_workloads_submitted_at"]
    assert "idx_workloads_month_spend" not in after
