"""Migration 0038 reclassifies only the providers a lease teardown disarmed (finding #2)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

_MIGRATION = Path(__file__).resolve().parents[2] / "db/migrations/0038_disarmed_provider_health.sql"


async def test_only_lease_disarmed_unhealthy_providers_become_disarmed(pg_pool: Any) -> None:
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config)"
            " VALUES ('cap_serve', 'llm.serve', '1.0.0', 'llm', 'per_token', '{}')"
        )
        providers = {
            # id: (health, config, latest audit action)
            "prov_disarmed": ("unhealthy", {"openai_proxy_port": 8000}, "lease_closed"),
            "prov_operator": ("unhealthy", {"openai_proxy_port": 8000}, "update"),
            "prov_failing": ("unhealthy", {}, None),
            "prov_armed": ("unhealthy", {"active_lease_id": "lease_x"}, "lease_closed"),
        }
        for provider_id, (health, config, action) in providers.items():
            await conn.execute(
                "INSERT INTO pitwall.providers"
                " (id, capability_id, name, provider_type, config, priority, health_status)"
                " VALUES ($1, 'cap_serve', $1, 'pod_lease', $2::jsonb, 1, $3)",
                provider_id,
                json.dumps(config),
                health,
            )
            if provider_id == "prov_operator":
                await _audit(conn, provider_id, "lease_closed")
            if action is not None:
                await _audit(conn, provider_id, action)

        await conn.execute(_MIGRATION.read_text(encoding="utf-8"))

        rows = await conn.fetch("SELECT id, health_status FROM pitwall.providers ORDER BY id")
    assert {row["id"]: row["health_status"] for row in rows} == {
        "prov_armed": "unhealthy",
        "prov_disarmed": "disarmed",
        "prov_failing": "unhealthy",
        "prov_operator": "unhealthy",
    }


async def _audit(conn: Any, provider_id: str, action: str) -> None:
    await conn.execute(
        "INSERT INTO pitwall.config_audit (actor, action, entity_type, entity_id, change_reason)"
        " VALUES ('system:lease', $1, 'provider', $2, 'test')",
        action,
        provider_id,
    )
