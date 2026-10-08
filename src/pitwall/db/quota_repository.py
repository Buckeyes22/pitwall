"""Persistence for provider quota evidence and the proxy model-id map (migration 0033)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import asyncpg

from pitwall.routing.quota import QuotaRecord


@dataclass(frozen=True, slots=True)
class ModelIdMapping:
    model_id: str
    capability: str
    provider: str


class QuotaRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_all(self) -> tuple[QuotaRecord, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM pitwall.provider_quotas ORDER BY provider_id, pool_key"
            )
        return tuple(_record(row) for row in rows)

    async def upsert(self, record: QuotaRecord) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pitwall.provider_quotas
                    (provider_id, pool_key, free_type, window_start, reset_at, budget_units, used_units,
                     tos_verdict, evidence, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, now())
                ON CONFLICT (provider_id, pool_key) DO UPDATE SET
                    free_type = EXCLUDED.free_type, window_start = EXCLUDED.window_start,
                    reset_at = EXCLUDED.reset_at, budget_units = EXCLUDED.budget_units,
                    used_units = EXCLUDED.used_units, tos_verdict = EXCLUDED.tos_verdict,
                    evidence = EXCLUDED.evidence, updated_at = now()
                """,
                record.provider_id,
                record.pool_key,
                record.free_type,
                record.window_start,
                record.reset_at,
                str(record.budget_units) if record.budget_units is not None else None,
                str(record.used_units),
                record.tos_verdict,
                dict(record.evidence),
            )

    async def add_usage(self, provider_id: str, pool_key: str, units: Decimal) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE pitwall.provider_quotas SET used_units = (used_units::numeric + $3)::text, updated_at = now()"
                " WHERE provider_id = $1 AND pool_key = $2",
                provider_id,
                pool_key,
                units,
            )

    async def set_lockout(self, provider_id: str, model_id: str, state: dict[str, Any]) -> None:
        """Store the API process's lockout state under ``evidence.lockout`` for every pool row.

        ``state["seq"]`` is the transition's sequence number, assigned when the transition
        happened. A row is updated only when its stored ``seq`` is older, so a delayed write
        never replaces a newer state.
        """
        raw_seq = state.get("seq")
        if isinstance(raw_seq, bool) or not isinstance(raw_seq, int):
            raise ValueError("set_lockout requires an integer state['seq']")
        document = dict(state)
        document["model_id"] = model_id
        document["updated_at"] = dt.datetime.now(dt.UTC).isoformat()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE pitwall.provider_quotas"
                " SET evidence = COALESCE(evidence, '{}'::jsonb) || jsonb_build_object('lockout', $2::jsonb),"
                " updated_at = now() WHERE provider_id = $1"
                " AND COALESCE((evidence->'lockout'->>'seq')::bigint, -1) < $3",
                provider_id,
                document,
                raw_seq,
            )

    async def roll_window(
        self,
        provider_id: str,
        pool_key: str,
        *,
        window_start: dt.datetime | None,
        reset_at: dt.datetime | None,
    ) -> None:
        """Start a new window in one statement so concurrent ``add_usage`` calls are not lost."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE pitwall.provider_quotas SET window_start = $3, reset_at = $4, used_units = '0',"
                " updated_at = now() WHERE provider_id = $1 AND pool_key = $2 AND reset_at <= now()",
                provider_id,
                pool_key,
                window_start,
                reset_at,
            )

    async def refresh_window(
        self,
        provider_id: str,
        pool_key: str,
        *,
        free_type: str,
        window_start: dt.datetime | None,
        reset_at: dt.datetime | None,
        budget_units: Decimal | None,
        used_units: Decimal | None,
        tos_verdict: str,
        evidence_patch: Mapping[str, Any],
    ) -> None:
        """Upsert a provider-observed window; ``None`` used_units keeps the stored count."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pitwall.provider_quotas
                    (provider_id, pool_key, free_type, window_start, reset_at, budget_units, used_units, tos_verdict, evidence, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, COALESCE($7, '0'), $8, $9::jsonb, now())
                ON CONFLICT (provider_id, pool_key) DO UPDATE SET
                    free_type = EXCLUDED.free_type,
                    used_units = CASE
                        WHEN $7 IS NOT NULL THEN $7
                        WHEN pitwall.provider_quotas.window_start IS DISTINCT FROM EXCLUDED.window_start THEN '0'
                        ELSE pitwall.provider_quotas.used_units END,
                    window_start = EXCLUDED.window_start,
                    reset_at = EXCLUDED.reset_at,
                    budget_units = EXCLUDED.budget_units,
                    tos_verdict = EXCLUDED.tos_verdict,
                    evidence = pitwall.provider_quotas.evidence || EXCLUDED.evidence,
                    updated_at = now()
                """,
                provider_id,
                pool_key,
                free_type,
                window_start,
                reset_at,
                str(budget_units) if budget_units is not None else None,
                str(used_units) if used_units is not None else None,
                tos_verdict,
                dict(evidence_patch),
            )

    async def record_sample(
        self,
        provider_id: str,
        sampled_at: dt.datetime,
        used_units: Decimal,
        reset_at: dt.datetime | None,
    ) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO pitwall.provider_quota_samples (provider_id, sampled_at, used_units, reset_at)"
                " VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                provider_id,
                sampled_at,
                str(used_units),
                reset_at,
            )

    async def list_model_ids(self) -> tuple[ModelIdMapping, ...]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT model_id, capability, provider FROM pitwall.model_id_map ORDER BY model_id"
            )
        return tuple(
            ModelIdMapping(row["model_id"], row["capability"], row["provider"]) for row in rows
        )

    async def upsert_model_id(self, model_id: str, capability: str, provider: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO pitwall.model_id_map (model_id, capability, provider) VALUES ($1, $2, $3)"
                " ON CONFLICT (model_id) DO UPDATE SET capability = EXCLUDED.capability, provider = EXCLUDED.provider",
                model_id,
                capability,
                provider,
            )


def _record(row: Any) -> QuotaRecord:
    return QuotaRecord(
        provider_id=row["provider_id"],
        pool_key=row["pool_key"],
        free_type=row["free_type"],
        window_start=row["window_start"],
        reset_at=row["reset_at"],
        budget_units=Decimal(row["budget_units"]) if row["budget_units"] is not None else None,
        used_units=Decimal(row["used_units"]),
        tos_verdict=row["tos_verdict"],
        evidence=dict(row["evidence"] or {}),
        updated_at=row["updated_at"],
    )


__all__ = ["ModelIdMapping", "QuotaRepository"]
