"""The quota poll writes Model Studio windows from the catalog, stats, and billing reads."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.providers.model_studio.openapi import SubscriptionStats
from pitwall.reconciler import _quota_poll

pytestmark = pytest.mark.anyio

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)


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


def _row(**settings: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "plan": "token-plan-personal",
        "tier": "pro",
        "model": "qwen3.8-flash",
        "renews_on": "2026-09-12",
    }
    base.update(settings)
    filtered = {key: value for key, value in base.items() if value is not None}
    return {"id": "ms-flash", "config": {"model_studio": filtered}}


async def _poll(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[dict[str, Any]],
    *,
    stats: Any = None,
    spend: Any = None,
    env: dict[str, str] | None = None,
) -> AsyncMock:
    repo = AsyncMock()
    repo.list_all.return_value = ()
    monkeypatch.setattr("pitwall.reconciler.QuotaRepository", lambda _pool: repo)

    async def rows_of(_pool: Any, provider_types: Any) -> list[dict[str, Any]]:
        return rows if "model_studio" in provider_types else []

    monkeypatch.setattr("pitwall.reconciler.fetch_providers_of_types", rows_of)
    monkeypatch.setattr(
        "pitwall.providers.model_studio.openapi.get_subscription_stats",
        AsyncMock(return_value=stats),
    )
    monkeypatch.setattr(
        "pitwall.providers.model_studio.openapi.get_billing_month_to_date",
        AsyncMock(return_value=spend),
    )
    for key in ("MODEL_STUDIO_TOKEN_PLAN_AUTOMATION",):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    await _quota_poll({"db_pool": _make_mock_pool(), "now": NOW})
    return repo


async def test_token_plan_window_from_renewal_without_stats_and_gate_closed(monkeypatch) -> None:
    repo = await _poll(monkeypatch, [_row()])
    kwargs = repo.refresh_window.await_args.kwargs
    assert kwargs["free_type"] == "subscription-credits"
    assert (kwargs["window_start"], kwargs["reset_at"]) == (
        dt.datetime(2026, 9, 12, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 12, tzinfo=dt.UTC),
    )
    assert kwargs["budget_units"] == Decimal(180000)
    assert kwargs["used_units"] is None
    assert kwargs["tos_verdict"] == "avoid"
    assert kwargs["evidence_patch"]["source"] == "configured-tier"


async def test_token_plan_stats_and_acceptance(monkeypatch) -> None:
    stats = SubscriptionStats(
        window_start=NOW - dt.timedelta(days=4),
        reset_at=NOW + dt.timedelta(days=26),
        total_credits=Decimal(180000),
        remaining_credits=Decimal(170000),
    )
    repo = await _poll(
        monkeypatch, [_row()], stats=stats, env={"MODEL_STUDIO_TOKEN_PLAN_AUTOMATION": "accept"}
    )
    kwargs = repo.refresh_window.await_args.kwargs
    assert (kwargs["used_units"], kwargs["tos_verdict"]) == (Decimal(10000), "caution")
    assert kwargs["evidence_patch"]["source"] == "openapi-stats"


async def test_pay_as_you_go_records_month_to_date_spend(monkeypatch) -> None:
    repo = await _poll(
        monkeypatch,
        [_row(plan="pay-as-you-go", tier=None, region="ap-southeast-1", renews_on=None)],
        spend=Decimal("12.34"),
    )
    kwargs = repo.refresh_window.await_args.kwargs
    assert kwargs["free_type"] == "pay-as-you-go"
    assert kwargs["budget_units"] is None and kwargs["used_units"] == Decimal("12.34")
    assert kwargs["tos_verdict"] == "ok"
    assert (kwargs["window_start"], kwargs["reset_at"]) == (
        dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
    )


async def test_one_bad_provider_does_not_stop_the_tick(monkeypatch) -> None:
    repo = await _poll(monkeypatch, [{"id": "broken", "config": {}}, _row()])
    assert repo.refresh_window.await_count == 1
