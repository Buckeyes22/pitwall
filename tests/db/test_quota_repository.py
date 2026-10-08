"""Tests for QuotaRepository: list_all, upsert, add_usage, record_sample, model_id_map."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from pitwall.db import _register_codecs
from pitwall.db.quota_repository import ModelIdMapping, QuotaRepository
from pitwall.routing.quota import QuotaRecord
from tests.conftest import make_asyncpg_pool

_NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
_PG_URL = os.getenv("PITWALL_TEST_DATABASE_URL", "")


def _record(**overrides: Any) -> QuotaRecord:
    base: dict[str, Any] = {
        "provider_id": "prov_gw",
        "pool_key": "alpha-pool",
        "free_type": "recurring-monthly",
        "window_start": _NOW - dt.timedelta(days=10),
        "reset_at": _NOW + dt.timedelta(days=20),
        "budget_units": Decimal("5000000"),
        "used_units": Decimal("1000000"),
        "tos_verdict": "ok",
        "evidence": {"source": "test"},
        "updated_at": _NOW,
    }
    base.update(overrides)
    return QuotaRecord(**base)


def _row(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "provider_id": "prov_gw",
        "pool_key": "alpha-pool",
        "free_type": "recurring-monthly",
        "window_start": _NOW - dt.timedelta(days=10),
        "reset_at": _NOW + dt.timedelta(days=20),
        "budget_units": "5000000",
        "used_units": "1000000",
        "tos_verdict": "ok",
        "evidence": {"source": "test"},
        "updated_at": _NOW,
    }
    base.update(overrides)
    return base


@pytest.mark.anyio
async def test_list_all_returns_quota_records() -> None:
    pool = make_asyncpg_pool(fetch=[_row()])
    repo = QuotaRepository(pool)

    records = await repo.list_all()

    assert len(records) == 1
    [record] = records
    assert record.provider_id == "prov_gw"
    assert record.pool_key == "alpha-pool"
    assert record.budget_units == Decimal("5000000")
    assert record.used_units == Decimal("1000000")
    assert record.tos_verdict == "ok"
    assert record.evidence == {"source": "test"}

    pool.conn.fetch.assert_awaited_once()
    sql = pool.conn.fetch.await_args.args[0]
    assert "SELECT * FROM pitwall.provider_quotas" in sql


@pytest.mark.anyio
async def test_upsert_uses_on_conflict_clause_with_provider_pool_key() -> None:
    pool = make_asyncpg_pool()
    repo = QuotaRepository(pool)

    await repo.upsert(_record())

    pool.conn.execute.assert_awaited_once()
    sql = pool.conn.execute.await_args.args[0]
    assert "INSERT INTO pitwall.provider_quotas" in sql
    assert "ON CONFLICT (provider_id, pool_key) DO UPDATE" in sql


@pytest.mark.anyio
async def test_add_usage_increments_used_units_via_numeric_cast() -> None:
    pool = make_asyncpg_pool()
    repo = QuotaRepository(pool)

    await repo.add_usage("prov_gw", "alpha-pool", Decimal("250"))

    pool.conn.execute.assert_awaited_once()
    sql = pool.conn.execute.await_args.args[0]
    assert "used_units = (used_units::numeric + $3)::text" in sql


@pytest.mark.anyio
async def test_record_sample_inserts_quota_samples() -> None:
    pool = make_asyncpg_pool()
    repo = QuotaRepository(pool)

    await repo.record_sample("prov_gw", _NOW, Decimal("1000"), _NOW + dt.timedelta(days=20))

    pool.conn.execute.assert_awaited_once()
    sql = pool.conn.execute.await_args.args[0]
    assert "INSERT INTO pitwall.provider_quota_samples" in sql


@pytest.mark.anyio
async def test_list_model_ids_returns_mappings() -> None:
    rows = [
        {"model_id": "alpha/a1", "capability": "coding.chat", "provider": "prov_gw"},
        {"model_id": "beta/b1", "capability": "coding.chat", "provider": "prov_gw2"},
    ]
    pool = make_asyncpg_pool(fetch=rows)
    repo = QuotaRepository(pool)

    mappings = await repo.list_model_ids()

    assert len(mappings) == 2
    assert isinstance(mappings[0], ModelIdMapping)
    assert mappings[0].model_id == "alpha/a1"
    assert mappings[0].capability == "coding.chat"
    assert mappings[0].provider == "prov_gw"

    sql = pool.conn.fetch.await_args.args[0]
    assert "SELECT model_id, capability, provider FROM pitwall.model_id_map" in sql


@pytest.mark.anyio
async def test_upsert_model_id_uses_on_conflict_clause() -> None:
    pool = make_asyncpg_pool()
    repo = QuotaRepository(pool)

    await repo.upsert_model_id("alpha/a1", "coding.chat", "prov_gw")

    pool.conn.execute.assert_awaited_once()
    sql = pool.conn.execute.await_args.args[0]
    assert "INSERT INTO pitwall.model_id_map" in sql
    assert "ON CONFLICT (model_id) DO UPDATE" in sql


@pytest.mark.anyio
async def test_set_lockout_writes_only_over_an_older_sequence() -> None:
    pool = make_asyncpg_pool()
    repo = QuotaRepository(pool)

    await repo.set_lockout("prov_gw", "beta/b1", {"failures": 2, "seq": 7})

    pool.conn.execute.assert_awaited_once()
    sql, provider_id, document, seq = pool.conn.execute.await_args.args
    assert "COALESCE((evidence->'lockout'->>'seq')::bigint, -1) < $3" in sql
    assert (provider_id, seq) == ("prov_gw", 7)
    assert document["model_id"] == "beta/b1" and document["seq"] == 7


@pytest.mark.anyio
async def test_set_lockout_requires_a_sequence() -> None:
    repo = QuotaRepository(make_asyncpg_pool())

    with pytest.raises(ValueError, match="seq"):
        await repo.set_lockout("prov_gw", "beta/b1", {"failures": 2})


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _PG_URL, reason="PITWALL_TEST_DATABASE_URL not set")
async def test_set_lockout_applied_out_of_order_leaves_the_newer_row() -> None:
    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1, init=_register_codecs)
    assert pool is not None
    provider_id = "prov_lockout_order"
    try:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
            await conn.execute(
                """
                INSERT INTO pitwall.providers (id, name, adapter_id, credential_ref, provider_type, config, priority)
                VALUES ($1, $1, 'model_studio', 'MODEL_STUDIO_API_KEY', 'model_studio', '{}'::jsonb, 50)
                """,
                provider_id,
            )
            await conn.execute(
                "INSERT INTO pitwall.provider_quotas (provider_id, pool_key, free_type, tos_verdict)"
                " VALUES ($1, '', 'subscription-credits', 'caution'), ($1, 'b', 'subscription-credits', 'caution')",
                provider_id,
            )
        repo = QuotaRepository(pool)
        newer = {"failures": 2, "locked_until": None, "reason": None, "seq": 2}
        older = {"failures": 1, "locked_until": "2026-09-10T12:02:00+00:00", "seq": 1}
        await repo.set_lockout(provider_id, "m1", newer)
        await repo.set_lockout(provider_id, "m1", older)  # the delayed, older write

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT evidence FROM pitwall.provider_quotas WHERE provider_id = $1", provider_id
            )
        assert len(rows) == 2
        for row in rows:
            assert row["evidence"]["lockout"]["seq"] == 2
            assert row["evidence"]["lockout"]["failures"] == 2

        await repo.set_lockout(provider_id, "m1", {"failures": 3, "seq": 3})
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT evidence FROM pitwall.provider_quotas WHERE provider_id = $1 LIMIT 1",
                provider_id,
            )
        assert row is not None and row["evidence"]["lockout"]["failures"] == 3
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
        await pool.close()
