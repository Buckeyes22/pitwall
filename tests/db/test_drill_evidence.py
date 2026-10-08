"""Drill evidence filters by type in SQL, before the row limit."""

from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

from pitwall.db import _register_codecs
from pitwall.db.drill_evidence import get_drill_evidence, persist_drill_evidence
from tests.conftest import make_asyncpg_pool

_PG_URL = os.getenv("PITWALL_TEST_DATABASE_URL", "")


async def test_drill_type_filter_is_a_sql_predicate_before_the_limit() -> None:
    pool = make_asyncpg_pool(
        fetch=[
            {
                "id": 1,
                "actor": "system",
                "entity_type": "drill",
                "entity_id": "d1",
                "new_value": json.dumps({"drill_type": "wanted"}),
                "change_reason": None,
                "created_at": None,
            }
        ]
    )

    rows = await get_drill_evidence(pool, drill_type="wanted", limit=1)

    sql = pool.conn.fetch.await_args.args[0]
    args = pool.conn.fetch.await_args.args[1:]
    assert "COALESCE(new_value ->> 'drill_type'" in sql
    assert "jsonb_typeof(new_value) = 'string'" in sql
    assert "(new_value #>> '{}')::jsonb ->> 'drill_type'" in sql
    assert ") = $2" in sql
    assert sql.index(") = $2") < sql.index("LIMIT")
    assert "wanted" in args
    assert args[-1] == 1
    assert [row["entity_id"] for row in rows] == ["d1"]


async def test_without_drill_type_the_sql_has_no_type_predicate() -> None:
    pool = make_asyncpg_pool(fetch=[])

    await get_drill_evidence(pool)

    assert "drill_type" not in pool.conn.fetch.await_args.args[0]


@pytest.mark.integration
@pytest.mark.skipif(not _PG_URL, reason="PITWALL_TEST_DATABASE_URL not set")
async def test_newer_drills_of_other_types_do_not_hide_the_wanted_type() -> None:
    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1, init=_register_codecs)
    assert pool is not None
    suffix = uuid.uuid4().hex[:8]
    wanted_type = f"wanted_{suffix}"
    wanted_id = f"drill-wanted-{suffix}"
    newer_id = f"drill-newer-{suffix}"
    try:
        await persist_drill_evidence(pool, wanted_id, wanted_type, {"drill_type": wanted_type})
        await persist_drill_evidence(pool, newer_id, "newer", {"drill_type": "newer"})

        rows = await get_drill_evidence(pool, drill_type=wanted_type, limit=1)

        assert [row["entity_id"] for row in rows] == [wanted_id]
        assert rows[0]["new_value"]["drill_type"] == wanted_type
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pitwall.config_audit WHERE entity_id = ANY($1::text[])",
                [wanted_id, newer_id],
            )
        await pool.close()


@pytest.mark.integration
@pytest.mark.skipif(not _PG_URL, reason="PITWALL_TEST_DATABASE_URL not set")
async def test_double_encoded_legacy_rows_are_still_found_by_type() -> None:
    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1, init=_register_codecs)
    assert pool is not None
    suffix = uuid.uuid4().hex[:8]
    drill_type = f"legacy_{suffix}"
    legacy_id = f"drill-legacy-{suffix}"
    normal_id = f"drill-normal-{suffix}"
    evidence = {"drill_type": drill_type, "ok": True}
    try:
        async with pool.acquire() as conn:
            # A JSON string scalar holding the serialized object: what the old reader decoded.
            await conn.execute(
                """
                INSERT INTO pitwall.config_audit
                    (actor, action, entity_type, entity_id, new_value)
                VALUES ('system', 'create', 'drill', $1, $2::jsonb)
                """,
                legacy_id,
                json.dumps(json.dumps(evidence)),
            )
        await persist_drill_evidence(pool, normal_id, drill_type, evidence)

        rows = await get_drill_evidence(pool, drill_type=drill_type)

        assert {row["entity_id"] for row in rows} == {legacy_id, normal_id}
        assert all(row["new_value"] == evidence for row in rows)
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pitwall.config_audit WHERE entity_id = ANY($1::text[])",
                [legacy_id, normal_id],
            )
        await pool.close()
