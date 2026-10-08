"""Tests for the webhook receiver and cost exporter FastAPI apps.

Add webhook and exporter apps.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from prometheus_client import generate_latest


class _HealthyConnection:
    async def fetchval(self, query: str) -> int:
        assert query == "SELECT 1"
        return 1


class _HealthyAcquire:
    async def __aenter__(self) -> _HealthyConnection:
        return _HealthyConnection()

    async def __aexit__(self, *args: object) -> None:
        return None


class _HealthyPool:
    def acquire(self) -> _HealthyAcquire:
        return _HealthyAcquire()


class _BrokenAcquire:
    async def __aenter__(self) -> None:
        raise RuntimeError("postgresql://user:do-not-leak@db/private")

    async def __aexit__(self, *args: object) -> None:
        return None


class _BrokenPool:
    def acquire(self) -> _BrokenAcquire:
        return _BrokenAcquire()


class _HealthyRedis:
    async def ping(self) -> bool:
        return True


class _BrokenRedis:
    async def ping(self) -> bool:
        raise RuntimeError("redis://:do-not-leak@cache/0")


_WEBHOOK_MONKEYPATCH_ENV = {
    "RUNPOD_API_KEY": None,
    "DATABASE_URL": None,
    "REDIS_URL": None,
}

_COST_EXPORTER_MONKEYPATCH_ENV = {
    "RUNPOD_API_KEY": None,
    "DATABASE_URL": "postgresql://pitwall:pitwall@localhost/pitwall",
    "REDIS_URL": None,
}


@pytest.fixture
def webhook_app():
    monkeypatch = pytest.MonkeyPatch()
    for key, val in _WEBHOOK_MONKEYPATCH_ENV.items():
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)
    from pitwall.webhook_receiver import app

    monkeypatch.undo()
    return app


@pytest.fixture
def cost_exporter_app():
    monkeypatch = pytest.MonkeyPatch()
    for key, val in _COST_EXPORTER_MONKEYPATCH_ENV.items():
        if val is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, val)
    from pitwall.cost.exporter import app

    monkeypatch.undo()
    return app


@pytest.mark.anyio
async def test_webhook_receiver_healthz(webhook_app) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=webhook_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "webhook-receiver"


@pytest.mark.anyio
async def test_webhook_receiver_health(webhook_app) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=webhook_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


@pytest.mark.anyio
async def test_webhook_receiver_readyz_checks_postgres_and_configured_redis(
    webhook_app,
) -> None:
    webhook_app.state.pool = _HealthyPool()
    webhook_app.state.redis_required = True
    webhook_app.state.redis = _HealthyRedis()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=webhook_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {
        "ok": True,
        "postgres": {"ok": True},
        "redis": {"ok": True},
    }


@pytest.mark.anyio
async def test_webhook_receiver_readyz_fails_closed_without_leaking_details(
    webhook_app,
) -> None:
    webhook_app.state.pool = _BrokenPool()
    webhook_app.state.redis_required = True
    webhook_app.state.redis = _BrokenRedis()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=webhook_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {
        "ok": False,
        "postgres": {"ok": False, "error": "unavailable"},
        "redis": {"ok": False, "error": "unavailable"},
    }
    assert "do-not-leak" not in resp.text


@pytest.mark.anyio
async def test_cost_exporter_healthz(cost_exporter_app) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cost_exporter_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["service"] == "cost-exporter"


@pytest.mark.anyio
async def test_cost_exporter_health(cost_exporter_app) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cost_exporter_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True


@pytest.mark.anyio
async def test_cost_exporter_readyz_checks_postgres(cost_exporter_app) -> None:
    cost_exporter_app.state.pool = _HealthyPool()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cost_exporter_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "postgres": {"ok": True}}


@pytest.mark.anyio
async def test_cost_exporter_readyz_fails_closed_without_leaking_details(
    cost_exporter_app,
) -> None:
    cost_exporter_app.state.pool = _BrokenPool()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cost_exporter_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/readyz")
    assert resp.status_code == 503
    assert resp.json() == {
        "ok": False,
        "postgres": {"ok": False, "error": "unavailable"},
    }
    assert "do-not-leak" not in resp.text


@pytest.mark.anyio
async def test_cost_exporter_metrics(cost_exporter_app) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=cost_exporter_app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers.get("content-type", "")
    text = resp.text
    assert "pitwall_cloud_spend_month_usd" in text
    assert "pitwall_cloud_budget_pct" in text
    assert "pitwall_cloud_spend_month_usd 0" in text


def test_webhook_receiver_module_imports() -> None:
    from pitwall.webhook_receiver import app

    assert app is not None
    assert app.title == "Pitwall Webhook Receiver"


def test_cost_exporter_module_imports() -> None:
    from pitwall.cost.exporter import app

    assert app is not None
    assert app.title == "Pitwall Cost Exporter"


@pytest.mark.anyio
async def test_cost_exporter_reports_zero_estimate_as_zero(cost_exporter_app) -> None:
    from pitwall.cost import exporter

    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval.side_effect = [0, 0, 0, 0]
    conn.fetch.side_effect = [
        [],
        [{"provider": "self-hosted-endpoint", "spend": Decimal("0")}],
        [],
    ]  # third: provider_quotas
    conn.fetchrow.side_effect = [
        {"s": Decimal("0")},  # month-to-date spend, through the shared function
        {"retries_due": 0, "terminal_failures_24h": 0},
        None,
        None,  # no runtime budget_limits row: the environment budget applies
    ]
    acquire = MagicMock()
    acquire.__aenter__.return_value = conn
    acquire.__aexit__.return_value = None
    pool.acquire.return_value = acquire
    cost_exporter_app.state.pool = pool
    cost_exporter_app.state.budget = Decimal("10.0")

    await exporter._refresh(cost_exporter_app)
    text = generate_latest().decode()

    assert "pitwall_cloud_spend_month_usd 0.0" in text
    assert 'pitwall_provider_spend_month_usd{provider="self-hosted-endpoint"} 0.0' in text


async def test_cost_exporter_reads_lockouts_from_persisted_quota_evidence(
    cost_exporter_app,
) -> None:
    """The lockout table is per process; the exporter must read what the API persisted (R8)."""
    from pitwall.cost import exporter

    locked_until = (dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)).isoformat()
    quota_row = {
        "provider_id": "gw-a",
        "pool_key": "pool-a",
        "budget_units": "10",
        "used_units": "1",
        "reset_at": None,
        "evidence": {
            "lockout": {
                "model_id": "m/1",
                "failures": 3,
                "locked_until": locked_until,
                "reason": "quota_exhausted",
                "permanent": False,
            }
        },
    }
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval.side_effect = [0, 0, 0, 0] * 2
    conn.fetch.side_effect = [[], [], [quota_row], [], [], [quota_row]]
    # month-to-date spend, webhook failures, retention run, then the runtime budget_limits row.
    conn.fetchrow.side_effect = [
        {"s": Decimal("0")},
        {"retries_due": 0, "terminal_failures_24h": 0},
        None,
        None,
    ] * 2
    acquire = MagicMock()
    acquire.__aenter__.return_value = conn
    acquire.__aexit__.return_value = None
    pool.acquire.return_value = acquire
    cost_exporter_app.state.pool = pool
    cost_exporter_app.state.budget = Decimal("10.0")

    exporter._seen_failures.clear()
    await exporter._refresh(cost_exporter_app)
    text = generate_latest().decode()
    assert 'pitwall_free_pool_locked{model="m/1",provider="gw-a"} 1.0' in text
    assert (
        'pitwall_free_pool_429_total{model="m/1",provider="gw-a",reason="quota_exhausted"} 3.0'
        in text
    )

    await exporter._refresh(cost_exporter_app)
    text = generate_latest().decode()
    assert (
        'pitwall_free_pool_429_total{model="m/1",provider="gw-a",reason="quota_exhausted"} 3.0'
        in text
    )
