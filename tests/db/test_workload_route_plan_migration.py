"""ROUTE-01 append-only workload plan persistence contract (migration 0031)."""

from __future__ import annotations

import json

import pytest

from tests.db.schema_catalog import Catalog

pytestmark = pytest.mark.integration

_PLAN_ID = "plan_" + "0123456789abcdef" * 2
_INSERT = (
    "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state, submitted_at, "
    "route_plan_id, route_plan, route_attempts) "
    "VALUES ('wl_rp', 'cap', 'prov', 'run', 'queued', now(), $1, $2::jsonb, $3::jsonb)"
)


async def test_route_plan_columns_and_index(db_catalog: Catalog) -> None:
    await db_catalog.expect_columns(
        "workloads",
        route_plan_id="text",
        route_plan="jsonb",
        route_attempts="jsonb not null default '[]'::jsonb",
    )
    index = await db_catalog.index("workloads", "idx_workloads_route_plan_id")

    assert index.columns == ("route_plan_id",)


async def test_route_plan_checks_require_a_paired_safe_document(db_catalog: Catalog) -> None:
    constraints = await db_catalog.constraints("workloads")

    assert constraints["workloads_route_plan_pair_check"].contains(
        "(route_plan_id IS NULL AND route_plan IS NULL) "
        "OR (route_plan_id IS NOT NULL AND route_plan IS NOT NULL)"
    )
    assert constraints["workloads_route_plan_object_check"].contains(
        "jsonb_typeof(route_plan) = 'object'"
    )
    assert constraints["workloads_route_plan_id_shape_check"].contains(
        "route_plan_id ~ '^plan_[0-9a-f]{32}$'"
    )
    identity = constraints["workloads_route_plan_identity_check"]
    assert identity.contains("route_plan ? 'plan_id'")
    assert identity.contains("route_plan ->> 'plan_id' IS NOT NULL")
    assert identity.contains("route_plan ->> 'plan_id' = route_plan_id")
    assert constraints["workloads_route_attempts_shape_check"].contains(
        "jsonb_array_length(route_attempts) <= 100"
    )
    assert constraints["workloads_route_plan_size_check"].contains(
        "pg_column_size(route_plan) <= 1048576"
    )


async def test_the_database_refuses_an_unpaired_or_mismatched_plan(db_catalog: Catalog) -> None:
    plan = json.dumps({"plan_id": _PLAN_ID})
    ok = await db_catalog.rejects(_INSERT, _PLAN_ID, plan, "[]")
    unpaired = await db_catalog.rejects(_INSERT, _PLAN_ID, None, "[]")
    bad_shape = await db_catalog.rejects(_INSERT, "plan_x", json.dumps({"plan_id": "plan_x"}), "[]")
    mismatch = await db_catalog.rejects(_INSERT, _PLAN_ID, json.dumps({"plan_id": "other"}), "[]")
    too_many = await db_catalog.rejects(_INSERT, _PLAN_ID, plan, json.dumps([{}] * 101))

    assert ok is None
    assert all(failure is not None for failure in (unpaired, bad_shape, mismatch, too_many))
