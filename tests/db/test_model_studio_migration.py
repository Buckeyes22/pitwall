"""Migration 0035: Model Studio provider type/adapter and subscription quota windows."""

from __future__ import annotations

import datetime as dt
import os
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from pitwall.core.enums import ProviderAdapterId, ProviderType
from pitwall.db import _register_codecs
from pitwall.db.quota_repository import QuotaRepository
from pitwall.migrations import discover_migrations

_ROOT = Path(__file__).resolve().parents[2]
_MIGRATION = _ROOT / "db/migrations/0035_model_studio.sql"
_PG_URL = os.getenv("PITWALL_TEST_DATABASE_URL", "")


def test_0035_is_the_latest_migration_and_widens_every_check() -> None:
    records = discover_migrations(_ROOT / "db/migrations")
    assert "0035_model_studio" in {record.version for record in records}
    sql = _MIGRATION.read_text(encoding="utf-8")
    for constraint in (
        "providers_provider_type_check",
        "providers_adapter_id_check",
        "provider_quotas_free_type_check",
    ):
        assert constraint in sql
    assert sql.count("'model_studio'") == 2
    assert "'subscription-credits'" in sql and "'pay-as-you-go'" in sql


def test_enums_match_the_migration() -> None:
    assert ProviderType.MODEL_STUDIO.value == "model_studio"
    assert ProviderAdapterId.MODEL_STUDIO.value == "model_studio"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.skipif(not _PG_URL, reason="PITWALL_TEST_DATABASE_URL not set")
async def test_model_studio_provider_and_credits_quota_insert() -> None:
    pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1, init=_register_codecs)
    assert pool is not None
    provider_id = "prov_0035_model_studio"
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
                """
                INSERT INTO pitwall.provider_quotas (provider_id, pool_key, free_type, tos_verdict)
                VALUES ($1, '', 'subscription-credits', 'caution')
                """,
                provider_id,
            )
            # A pre-set lockout (the API's evidence) must survive quota refreshes.
            await conn.execute(
                "UPDATE pitwall.provider_quotas SET evidence = $2::jsonb WHERE provider_id = $1 AND pool_key = ''",
                provider_id,
                '{"lockout": {"model_id": "qwen3.8-flash"}}',
            )
        # The pool holds a single connection, so repository calls run outside the acquire above.
        repo = QuotaRepository(pool)
        window_start = dt.datetime(2026, 9, 12, tzinfo=dt.UTC)
        reset_at = dt.datetime(2026, 10, 12, tzinfo=dt.UTC)
        await repo.refresh_window(
            provider_id,
            "",
            free_type="subscription-credits",
            window_start=window_start,
            reset_at=reset_at,
            budget_units=Decimal(180000),
            used_units=None,
            tos_verdict="caution",
            evidence_patch={"source": "configured-tier"},
        )
        await repo.refresh_window(
            provider_id,
            "",
            free_type="subscription-credits",
            window_start=window_start,
            reset_at=reset_at,
            budget_units=Decimal(180000),
            used_units=Decimal(10000),
            tos_verdict="caution",
            evidence_patch={"source": "openapi-stats"},
        )
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT used_units, budget_units, evidence FROM pitwall.provider_quotas WHERE provider_id = $1 AND pool_key = ''",
                provider_id,
            )
        assert row is not None
        assert Decimal(str(row["used_units"])) == Decimal(10000)
        assert Decimal(str(row["budget_units"])) == Decimal(180000)
        assert row["evidence"]["lockout"]["model_id"] == "qwen3.8-flash"
        assert row["evidence"]["source"] == "openapi-stats"
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM pitwall.providers WHERE id = $1", provider_id)
        await pool.close()
