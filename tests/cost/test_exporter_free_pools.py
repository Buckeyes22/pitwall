"""Free-pool metrics on the cost exporter (Task 15 of free-tier-gateway-integration plan).

Asserts the four pool gauges and their `# HELP` lines render after `_refresh`, and that the
lockout gauge and the 429 Counter follow the lockout state the API persisted into
``provider_quotas.evidence`` (the exporter runs in its own process; R8).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from prometheus_client import generate_latest

from pitwall.cost import exporter

_NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
_RESET_AT = _NOW + dt.timedelta(minutes=30)


@pytest.fixture(autouse=True)
def _isolated_lockout_table() -> None:
    """Reset the free-pool counter, gauge, and failure memory around every test."""
    exporter._seen_failures.clear()
    exporter.free_pool_429_total.clear()
    exporter.free_pool_locked.clear()
    yield
    exporter._seen_failures.clear()
    exporter.free_pool_429_total.clear()
    exporter.free_pool_locked.clear()


def _lockout_row(
    provider_id: str,
    model_id: str,
    *,
    failures: int,
    reason: str,
    locked_until: dt.datetime | None,
) -> dict[str, Any]:
    return {
        "provider_id": provider_id,
        "pool_key": f"{provider_id}-pool",
        "budget_units": None,
        "used_units": "0",
        "reset_at": None,
        "evidence": {
            "lockout": {
                "model_id": model_id,
                "failures": failures,
                "locked_until": locked_until.isoformat() if locked_until else None,
                "reason": reason,
                "permanent": False,
            }
        },
    }


def _build_app(quota_rows: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    """Build a SimpleNamespace matching the exporter's `app.state` shape."""
    pool = MagicMock()
    conn = AsyncMock()
    conn.fetchval.side_effect = [0, 0, 0, 0]
    conn.fetch.side_effect = [
        [],
        [],
        quota_rows or [],
    ]
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
    return SimpleNamespace(state=SimpleNamespace(pool=pool, budget=Decimal("1.0")))


@pytest.mark.anyio
async def test_metrics_render_free_pool_series_and_help_lines() -> None:
    quota_rows = [
        {
            "provider_id": "prov_alpha",
            "pool_key": "alpha-pool",
            "budget_units": "1000",
            "used_units": "250",
            "reset_at": _RESET_AT,
            "evidence": {},
        },
        _lockout_row(
            "prov_beta",
            "beta/b1",
            failures=1,
            reason="rate_limit_exceeded",
            locked_until=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=2),
        ),
    ]
    app = _build_app(quota_rows)

    await exporter._refresh(app)
    text = generate_latest().decode()

    assert 'pitwall_free_pool_headroom{pool="alpha-pool",provider="prov_alpha"} 0.75' in text
    assert 'pitwall_free_pool_reset_seconds{pool="alpha-pool",provider="prov_alpha"}' in text
    assert 'pitwall_free_pool_locked{model="beta/b1",provider="prov_beta"} 1.0' in text
    assert 'pitwall_free_tokens_used_month{pool="alpha-pool"} 250.0' in text

    assert "# HELP pitwall_free_pool_headroom" in text
    assert "# HELP pitwall_free_pool_reset_seconds" in text
    assert "# HELP pitwall_free_pool_locked" in text
    assert "# HELP pitwall_free_tokens_used_month" in text


@pytest.mark.anyio
async def test_lockout_429_counter_follows_persisted_failures() -> None:
    first = _lockout_row(
        "prov_beta", "beta/b1", failures=1, reason="rate_limit_exceeded", locked_until=_RESET_AT
    )
    await exporter._refresh(_build_app([first]))
    second = _lockout_row(
        "prov_beta", "beta/b1", failures=2, reason="quota_exhausted", locked_until=_RESET_AT
    )
    app = _build_app([second])

    await exporter._refresh(app)
    text = generate_latest().decode()

    assert "# HELP pitwall_free_pool_429_total" in text
    assert "# TYPE pitwall_free_pool_429_total counter" in text
    assert (
        'pitwall_free_pool_429_total{model="beta/b1",provider="prov_beta",reason="rate_limit_exceeded"} 1.0'
        in text
    )
    assert (
        'pitwall_free_pool_429_total{model="beta/b1",provider="prov_beta",reason="quota_exhausted"} 1.0'
        in text
    )
