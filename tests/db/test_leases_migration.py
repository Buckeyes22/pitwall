"""The pod lease table, state machine, expiry index, and readiness rule, read back from Postgres."""

from __future__ import annotations

import pytest

from tests.db.schema_catalog import Catalog, squash

pytestmark = pytest.mark.integration

_READINESS_KEYS = ("runtime_seen_at", "port_mappings_seen_at", "probe_passed_at")


async def test_leases_table_has_the_spec_columns(db_catalog: Catalog) -> None:
    await db_catalog.expect_columns(
        "leases",
        id="text not null",
        provider_id="text not null",
        # Optional since 0028: the provider-neutral external_resource_id may stand in for it.
        runpod_pod_id="text",
        state="text not null",
        created_at="timestamptz not null",
        expires_at="timestamptz not null",
        renewal_policy="text not null",
        endpoints="jsonb",
        readiness="jsonb",
        cost_accrued_usd="numeric(12,6)",
        last_health_at="timestamptz",
        terminated_at="timestamptz",
        terminated_reason="text",
    )
    assert await db_catalog.primary_key("leases") == ("id",)


async def test_lease_state_machine_matches_spec(db_catalog: Catalog) -> None:
    states = (await db_catalog.constraint("leases", "leases_state_check")).literals

    assert states == [
        "creating",
        "waiting_runtime",
        "waiting_probe",
        "active",
        "stopping",
        "stopped",
        "failed",
        "expired",
    ]


async def test_leases_have_the_active_expiry_index(db_catalog: Catalog) -> None:
    index = await db_catalog.index("leases", "idx_leases_expires")

    assert index.columns == ("state", "expires_at")
    assert squash(index.predicate) == squash("state = 'active'")


async def test_active_leases_must_carry_every_readiness_signal(db_catalog: Catalog) -> None:
    check = await db_catalog.constraint("leases", "leases_active_readiness_signals")
    assert check.kind == "check"
    assert check.contains("lease_active_has_readiness_signals(state, endpoints, readiness)")

    async def allowed(state: str, endpoints: str | None, readiness: str | None) -> bool:
        return bool(
            await db_catalog.connection.fetchval(
                "SELECT pitwall.lease_active_has_readiness_signals($1, $2::jsonb, $3::jsonb)",
                state,
                endpoints,
                readiness,
            )
        )

    complete = "{" + ", ".join(f'"{key}": "2026-01-01T00:00:00Z"' for key in _READINESS_KEYS) + "}"
    assert await allowed("active", "{}", complete)
    assert await allowed("creating", None, None)  # only active leases need the signals
    assert not await allowed("active", None, complete)
    assert not await allowed("active", "{}", None)
    for missing in _READINESS_KEYS:
        partial = "{" + ", ".join(f'"{k}": "x"' for k in _READINESS_KEYS if k != missing) + "}"
        assert not await allowed("active", "{}", partial), missing
