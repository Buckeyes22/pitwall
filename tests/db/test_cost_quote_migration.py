"""Static contract for COST-01 migration 0029.

The integration suite applies the complete migration ledger. These assertions
keep the durable quote/truth-up intent reviewable even before CORE-01's 0028 is
integrated ahead of this branch.
"""

from pathlib import Path

_MIGRATION = (Path(__file__).parents[2] / "db/migrations/0029_cost_quote_truth_up.sql").read_text(
    encoding="utf-8"
)


def test_migration_retains_quote_and_truth_up_fields() -> None:
    assert "cost_ceiling_usd NUMERIC(12,6)" in _MIGRATION
    assert "cost_quote JSONB" in _MIGRATION
    assert "cost_actual_provenance TEXT" in _MIGRATION
    assert "cost_reconciled_at TIMESTAMPTZ" in _MIGRATION


def test_migration_backfills_only_the_known_legacy_ceiling() -> None:
    assert "SET cost_ceiling_usd = cost_estimate_usd" in _MIGRATION
    assert "SET cost_quote" not in _MIGRATION
    assert "workloads_cost_ceiling_covers_estimate" in _MIGRATION
    assert "jsonb_typeof(cost_quote) = 'object'" in _MIGRATION
    assert "workloads_cost_actual_provenance_requires_actual" in _MIGRATION
    assert "workloads_cost_reconciled_requires_source" in _MIGRATION
