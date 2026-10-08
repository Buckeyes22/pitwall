"""Idempotency-key reservations against real Postgres (migration 0043, ``body_hash``)."""

from __future__ import annotations

import asyncpg
import pytest

from pitwall.core.idempotency import (
    IdempotencyMismatch,
    IdempotencyReservation,
    _hash_input,
    reserve_idempotency_key,
)
from tests.integration.conftest import pg_pool as pg_pool
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


async def test_different_body_is_refused_before_the_winner_persists_its_input(
    pg_pool: asyncpg.Pool,
) -> None:
    hash_a = _hash_input({"prompt": "A"})
    hash_b = _hash_input({"prompt": "B"})
    async with pg_pool.acquire() as conn:
        first = await reserve_idempotency_key(
            conn, key="idem-real", body_hash=hash_a, workload_id="wkl_pending_idem-real"
        )
        assert first == IdempotencyReservation(is_new=True, workload_id="wkl_pending_idem-real")

        with pytest.raises(IdempotencyMismatch):
            await reserve_idempotency_key(
                conn, key="idem-real", body_hash=hash_b, workload_id="wkl_pending_idem-real"
            )
        replay = await reserve_idempotency_key(
            conn, key="idem-real", body_hash=hash_a, workload_id="wkl_pending_idem-real"
        )
        assert replay == IdempotencyReservation(is_new=False, workload_id="wkl_pending_idem-real")
        stored = await conn.fetchval(
            "SELECT body_hash FROM pitwall.idempotency_keys WHERE idempotency_key = $1",
            "idem-real",
        )
        assert stored == hash_a


async def test_rows_created_before_the_migration_replay_without_a_hash(
    pg_pool: asyncpg.Pool,
) -> None:
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.idempotency_keys (idempotency_key, workload_id) VALUES ($1, $2)",
            "idem-legacy",
            "wkl_legacy",
        )
        replay = await reserve_idempotency_key(
            conn, key="idem-legacy", body_hash="any", workload_id="wkl_new"
        )
    assert replay == IdempotencyReservation(is_new=False, workload_id="wkl_legacy")
