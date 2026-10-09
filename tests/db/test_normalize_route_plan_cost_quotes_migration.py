"""Migration 0032 rewrites route-plan admission quotes into the canonical CostQuote shape."""

from __future__ import annotations

import json

import pytest

from tests.db.schema_catalog import MigrationSandbox

pytestmark = pytest.mark.integration

_PLAN_ID = "plan_" + "0123456789abcdef" * 2
_LEGACY_ROUTE_PLAN_QUOTE = {
    "model": "route_plan",
    "plan_id": _PLAN_ID,
    "components": [{"name": "attempt-1", "estimate": "0.10", "ceiling": "0.20"}],
}
_OTHER_QUOTE = {"model": "catalogue", "plan_id": "keep-me", "components": []}
# Already canonical: model is route_plan but there is no top-level plan_id, so the migration's
# `cost_quote ? 'plan_id'` guard must leave it alone (a second run must not rewrite it again).
_CANONICAL_ROUTE_PLAN_QUOTE = {
    "model": "route_plan",
    "components": [
        {
            "name": "attempt-1",
            "unit": "attempt",
            "rate": "0.10",
            "ceiling_rate": "0.20",
            "count": "1",
            "ceiling_count": "1",
            "estimate": "0.10",
            "ceiling": "0.20",
        }
    ],
    "assumptions": [f"route plan {_PLAN_ID}"],
}


async def test_only_route_plan_quotes_are_rewritten(db_sandbox: MigrationSandbox) -> None:
    await db_sandbox.apply_through("0031_workload_route_plan")
    connection = db_sandbox.connection
    for workload_id, quote in (
        ("wl_plan", _LEGACY_ROUTE_PLAN_QUOTE),
        ("wl_other", _OTHER_QUOTE),
        ("wl_canonical", _CANONICAL_ROUTE_PLAN_QUOTE),
        ("wl_none", None),
    ):
        await connection.execute(
            "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state, "
            "submitted_at, cost_quote) VALUES ($1, 'cap', 'prov', 'run', 'completed', now(), "
            "$2::jsonb)",
            workload_id,
            None if quote is None else json.dumps(quote),
        )

    await db_sandbox.apply_through("0032_normalize_route_plan_cost_quotes")

    quotes = {
        row["id"]: json.loads(row["cost_quote"]) if row["cost_quote"] is not None else None
        for row in await connection.fetch("SELECT id, cost_quote::text FROM pitwall.workloads")
    }
    assert quotes["wl_other"] == _OTHER_QUOTE
    assert quotes["wl_none"] is None
    assert quotes["wl_canonical"] == _CANONICAL_ROUTE_PLAN_QUOTE
    rewritten = quotes["wl_plan"]
    assert rewritten is not None
    assert "plan_id" not in rewritten
    assert rewritten["model"] == "route_plan"
    assert rewritten["components"] == [
        {
            "name": "attempt-1",
            "unit": "attempt",
            "rate": "0.10",
            "ceiling_rate": "0.20",
            "count": "1",
            "ceiling_count": "1",
            "estimate": "0.10",
            "ceiling": "0.20",
        }
    ]
    assert rewritten["assumptions"] == [f"route plan {_PLAN_ID}"]
