"""Overlapping health writes never lose a failure count (review finding #17).

The probe and the OpenAI proxy each read a provider, compute the next cooldown state in Python,
and write absolute counters. Two writers that read the same row used to both write n + 1.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

import pitwall.reconciler as recon
from pitwall.api.routes import openai as openai_route
from pitwall.db.repository import ProviderRepository
from pitwall.routing.cooldown import apply_probe_result
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)


async def _seed(pool: Any) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config)"
            " VALUES ('cap_h', 'llm.h', '1.0.0', 'llm', 'per_token', '{}')"
        )
        await conn.execute(
            "INSERT INTO pitwall.providers"
            " (id, capability_id, name, provider_type, config, priority, health_status)"
            " VALUES ('prov_h', 'cap_h', 'prov_h', 'public_endpoint',"
            " '{\"base_url\": \"https://example.invalid\"}', 1, 'healthy')"
        )


async def _failures(pool: Any) -> int:
    async with pool.acquire() as conn:
        return int(
            await conn.fetchval(
                "SELECT consecutive_failures FROM pitwall.providers WHERE id = 'prov_h'"
            )
        )


async def test_two_probe_results_from_one_snapshot_both_count(pg_pool: Any) -> None:
    await _seed(pg_pool)
    (snapshot,) = await recon.fetch_providers_for_health_probe(pg_pool)

    def failed(row: Any) -> tuple[Any, None]:
        return apply_probe_result(row, passed=False, now=NOW), None

    assert await recon.persist_provider_health(pg_pool, snapshot, failed)
    assert await recon.persist_provider_health(pg_pool, snapshot, failed)

    assert await _failures(pg_pool) == 2


async def test_two_proxy_failures_from_one_snapshot_both_count(pg_pool: Any) -> None:
    await _seed(pg_pool)
    repo = ProviderRepository(pg_pool)
    snapshot = await repo.get("prov_h")
    assert snapshot is not None

    for _ in range(2):
        await openai_route._record_upstream_outcome(
            provider_repo=repo,
            provider_id="prov_h",
            state=snapshot,
            classification="failure",
            now=NOW,
        )

    assert await _failures(pg_pool) == 2
