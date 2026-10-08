"""Migration 0034: the capabilities cost_mode check admits ``zero`` (free-tier gateway)."""

from __future__ import annotations

import os
from pathlib import Path

import asyncpg
import pytest

from pitwall.db import _register_codecs
from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "db/migrations/0034_capabilities_zero_cost_mode.sql"
_PG_URL = os.getenv("PITWALL_TEST_DATABASE_URL", "")


def test_0034_widens_the_cost_mode_check() -> None:
    records = discover_migrations(_ROOT / "db/migrations")
    assert "0034_capabilities_zero_cost_mode" in [record.version for record in records]
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "capabilities_cost_mode_check" in sql
    assert "'zero'" in sql
    assert "'per_second'" in sql and "'per_request'" in sql and "'per_token'" in sql


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _PG_URL, reason="PITWALL_TEST_DATABASE_URL not set")
async def test_zero_cost_mode_capability_inserts_and_bogus_mode_is_still_rejected() -> None:
    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1, init=_register_codecs)
    assert pool is not None
    capability_id = "cap_0034_zero_integration"
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.capabilities WHERE id = $1", capability_id)
            await conn.execute(
                """
                INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config, source)
                VALUES ($1, $2, '1.0.0', 'llm', 'zero', '{}'::jsonb, 'api')
                """,
                capability_id,
                "zero.integration.0034",
            )
            with pytest.raises(asyncpg.CheckViolationError):
                await conn.execute(
                    """
                    INSERT INTO pitwall.capabilities (id, name, version, class, cost_mode, config, source)
                    VALUES ($1, $2, '1.0.0', 'llm', 'bogus', '{}'::jsonb, 'api')
                    """,
                    capability_id + "_bogus",
                    "bogus.integration.0034",
                )
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pitwall.capabilities WHERE id = ANY($1::text[])",
                [capability_id, capability_id + "_bogus"],
            )
        await pool.close()
