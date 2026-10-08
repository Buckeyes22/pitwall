"""Migration 0034: the capabilities cost_mode check admits ``zero`` (free-tier gateway)."""

from __future__ import annotations

import os

import asyncpg
import pytest

from pitwall.db import _register_codecs
from tests.db.schema_catalog import Catalog

pytestmark = pytest.mark.integration
_PG_URL = os.getenv("PITWALL_TEST_DATABASE_URL", "")


async def test_0034_widens_the_cost_mode_check(db_catalog: Catalog) -> None:
    check = await db_catalog.constraint("capabilities", "capabilities_cost_mode_check")

    assert set(check.literals) == {"per_second", "per_request", "per_token", "zero"}


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
