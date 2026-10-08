"""Doctor's database probe reports the budget gate's month-to-date spend against real Postgres."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from pitwall.doctor import _database_probe
from pitwall.migrations import discover_migrations
from tests._migration_ledger import MIGRATION_DIR, restore_migration_ledger
from tests.integration.conftest import PG_URL, requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_probe_reads_gate_spend_including_raw_pod_workloads(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "15")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "10")
    now = dt.datetime.now(dt.UTC)
    async with pg_pool.acquire() as conn:
        # The raw-schema fixture applies migrations without a ledger; doctor reads the ledger.
        versions = {record.version for record in discover_migrations(MIGRATION_DIR)}
        await restore_migration_ledger(conn, applied_versions=versions)
        await conn.execute(
            """
            INSERT INTO pitwall.workloads
                (id, capability_id, provider_id, type, state, submitted_at, cost_actual_usd)
            VALUES ('wkl_raw_probe', 'runpod_direct', 'runpod_direct', 'inference', 'completed', $1, 2.5)
            """,
            now,
        )
    facts = await _database_probe(PG_URL, 5.0)
    assert facts.burn is not None
    assert facts.burn.gate_spend_usd == Decimal("2.5")


async def test_probe_counts_disarmed_providers_apart_from_enabled(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        versions = {record.version for record in discover_migrations(MIGRATION_DIR)}
        await restore_migration_ledger(conn, applied_versions=versions)
        await conn.execute(
            "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config)"
            " VALUES ('cap_serve', 'llm.serve', '1.0.0', 'llm', 'per_token', '{}')"
        )
        for provider_id, health in (("prov_armed", "healthy"), ("prov_idle", "disarmed")):
            await conn.execute(
                "INSERT INTO pitwall.providers"
                " (id, capability_id, name, provider_type, config, priority, health_status)"
                " VALUES ($1, 'cap_serve', $1, 'public_endpoint', '{}', 1, $2)",
                provider_id,
                health,
            )
    facts = await _database_probe(PG_URL, 5.0)
    assert (facts.enabled_providers, facts.healthy_providers, facts.disarmed_providers) == (1, 1, 1)
