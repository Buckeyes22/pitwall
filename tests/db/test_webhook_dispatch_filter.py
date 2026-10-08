from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from pitwall.db import _register_codecs
from pitwall.db.repository import WebhookSubscriptionRepository
from pitwall.webhook_dispatcher.secret_store import WebhookSecretCipher


@pytest.mark.anyio
async def test_dispatch_filter_uses_gin_compatible_array_containment() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    cipher = WebhookSecretCipher({"v1": bytes(range(32))}, "v1")

    await WebhookSubscriptionRepository(pool, cipher).list_for_dispatch(
        consumer="llm.filter", event_type="lease.stopped"
    )

    sql = " ".join(conn.fetch.await_args.args[0].split())
    assert "event_types @> ARRAY[$2]::text[]" in sql
    assert "= ANY(event_types)" not in sql


_PG_URL = os.getenv("PITWALL_TEST_DATABASE_URL", "")


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _PG_URL, reason="PITWALL_TEST_DATABASE_URL not set")
async def test_dispatch_filter_returns_completion_and_lease_subscriptions() -> None:
    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1, init=_register_codecs)
    assert pool is not None
    cipher = WebhookSecretCipher({"v1": bytes(range(32))}, "v1")
    repo = WebhookSubscriptionRepository(pool, cipher)
    consumer = "integration.dispatch.filter"
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pitwall.webhook_subscriptions WHERE consumer = $1", consumer
            )
        await repo.create(
            consumer,
            "https://completion.example/hook",
            hmac_secret="completion-secret",
            event_types=["workload.completed"],
        )
        await repo.create(
            consumer,
            "https://lease.example/hook",
            hmac_secret="lease-secret",
            event_types=["lease.ready", "lease.stopped"],
        )

        completion = await repo.list_for_dispatch(
            consumer=consumer, event_type="workload.completed"
        )
        ready = await repo.list_for_dispatch(consumer=consumer, event_type="lease.ready")
        stopped = await repo.list_for_dispatch(consumer=consumer, event_type="lease.stopped")
        expiring = await repo.list_for_dispatch(consumer=consumer, event_type="lease.expiring")

        assert [item.webhook_url for item in completion] == ["https://completion.example/hook"]
        assert [item.webhook_url for item in ready] == ["https://lease.example/hook"]
        assert [item.webhook_url for item in stopped] == ["https://lease.example/hook"]
        assert expiring == []
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pitwall.webhook_subscriptions WHERE consumer = $1", consumer
            )
        await pool.close()
