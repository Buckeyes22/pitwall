"""Tests for _quota_poll — 5-minute free-pool quota seeding, rolling, and sampling."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.reconciler import _quota_poll
from pitwall.routing.quota import QuotaRecord

pytestmark = pytest.mark.anyio

NOW = dt.datetime(2026, 9, 15, 12, 0, tzinfo=dt.UTC)


def _make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    return conn


def _make_mock_pool() -> MagicMock:
    pool = MagicMock()
    conn = _make_mock_conn()
    pool.conn = conn
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acq)
    return pool


def _gateway_row(**catalog_changes: Any) -> dict[str, Any]:
    catalog: dict[str, Any] = {
        "pool_key": "deepseek",
        "free_type": "recurring-monthly",
        "tos": "unknown",
    }
    catalog.update(catalog_changes)
    return {
        "id": "gw-deepseek",
        "config": {
            "gateway": {
                "model_id": "deepseek/deepseek-chat",
                "catalog": catalog,
            }
        },
    }


def _record(**changes: Any) -> QuotaRecord:
    values: dict[str, Any] = {
        "provider_id": "gw-deepseek",
        "pool_key": "deepseek",
        "free_type": "recurring-monthly",
        "window_start": NOW - dt.timedelta(days=14),
        "reset_at": NOW + dt.timedelta(days=16),
        "budget_units": Decimal(5_000_000),
        "used_units": Decimal(0),
        "tos_verdict": "unknown",
        "evidence": {},
    }
    values.update(changes)
    return QuotaRecord(**values)


def _gateway_rows(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> None:
    async def rows_of(_pool: Any, provider_types: Any) -> list[dict[str, Any]]:
        return rows if "openai_gateway" in provider_types else []

    monkeypatch.setattr("pitwall.reconciler.fetch_providers_of_types", rows_of)


async def test_quota_poll_seeds_monthly_window_from_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = AsyncMock()
    repo.list_all.return_value = ()
    monkeypatch.setattr("pitwall.reconciler.QuotaRepository", lambda _pool: repo)
    _gateway_rows(monkeypatch, [_gateway_row(monthly_tokens=5_000_000)])
    await _quota_poll({"db_pool": _make_mock_pool(), "now": NOW})
    record = repo.upsert.await_args.args[0]
    assert (record.budget_units, record.used_units, record.reset_at) == (
        Decimal(5_000_000),
        Decimal(0),
        dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )


async def test_quota_poll_never_rewrites_used_units_of_a_live_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A usage increment recorded during the tick must survive it (R11)."""
    existing = _record(used_units=Decimal(100), evidence={"lockout": {"failures": 1}})
    repo = AsyncMock()
    repo.list_all.return_value = (existing,)
    monkeypatch.setattr("pitwall.reconciler.QuotaRepository", lambda _pool: repo)
    _gateway_rows(monkeypatch, [_gateway_row()])
    await _quota_poll({"db_pool": _make_mock_pool(), "now": NOW})
    repo.upsert.assert_not_awaited()
    repo.roll_window.assert_not_awaited()
    repo.record_sample.assert_awaited_once()


async def test_quota_poll_rolls_a_window_atomically_and_leaves_lockout_evidence_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _record(
        used_units=Decimal(4_000_000),
        reset_at=NOW - dt.timedelta(minutes=1),
        window_start=NOW - dt.timedelta(days=31),
        evidence={"lockout": {"failures": 1, "reason": "quota_exhausted"}},
    )
    repo = AsyncMock()
    repo.list_all.return_value = (existing,)
    monkeypatch.setattr("pitwall.reconciler.QuotaRepository", lambda _pool: repo)
    _gateway_rows(monkeypatch, [_gateway_row()])
    await _quota_poll({"db_pool": _make_mock_pool(), "now": NOW})
    repo.upsert.assert_not_awaited()
    repo.roll_window.assert_awaited_once_with(
        "gw-deepseek",
        "deepseek",
        window_start=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        reset_at=dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )
    sampled = repo.record_sample.await_args.args
    assert sampled[2] == Decimal(0)
