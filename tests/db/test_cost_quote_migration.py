"""COST-01 migration 0029: the durable quote and truth-up fields, read back from the schema."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog, MigrationSandbox

pytestmark = pytest.mark.integration


async def test_migration_retains_quote_and_truth_up_fields(db_catalog: Catalog) -> None:
    await db_catalog.expect_columns(
        "workloads",
        cost_ceiling_usd="numeric(12,6)",
        cost_quote="jsonb",
        cost_actual_provenance="text",
        cost_reconciled_at="timestamptz",
    )
    constraints = await db_catalog.constraints("workloads")

    for name in (
        "workloads_cost_ceiling_covers_estimate",
        "workloads_cost_quote_object",
        "workloads_cost_actual_provenance_requires_actual",
        "workloads_cost_reconciled_requires_source",
    ):
        assert constraints[name].kind == "check", name
    assert constraints["workloads_cost_quote_object"].contains(
        "jsonb_typeof(cost_quote) = 'object'"
    )


async def test_migration_backfills_only_the_known_legacy_ceiling(
    db_sandbox: MigrationSandbox,
) -> None:
    await db_sandbox.apply_through("0028_provider_neutral_runtime")
    connection = db_sandbox.connection
    await connection.execute(
        "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state, "
        "submitted_at, cost_estimate_usd) VALUES ('wl_priced', 'cap', 'prov', 'run', "
        "'completed', now(), 1.250000), ('wl_unpriced', 'cap', 'prov', 'run', 'completed', now(), NULL)"
    )

    await db_sandbox.apply_through("0029_cost_quote_truth_up")

    rows = {
        row["id"]: row
        for row in await connection.fetch(
            "SELECT id, cost_estimate_usd, cost_ceiling_usd, cost_quote FROM pitwall.workloads"
        )
    }
    assert rows["wl_priced"]["cost_ceiling_usd"] == rows["wl_priced"]["cost_estimate_usd"]
    assert rows["wl_unpriced"]["cost_ceiling_usd"] is None
    # No quote is invented for rows that never had one.
    assert {row["cost_quote"] for row in rows.values()} == {None}
