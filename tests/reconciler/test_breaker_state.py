"""The budget circuit breaker's state lives in the worker startup context."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.reconciler import _budget_breach_escalation, _on_startup

pytestmark = pytest.mark.anyio


def _pool() -> MagicMock:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=False)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        pitwall_budget_breach_kill_mode="shadow",
        pitwall_monthly_budget_usd=Decimal("100"),
        pitwall_per_request_max_usd=Decimal("10"),
        pitwall_budget_breach_kill_headroom_floor_usd=Decimal("0"),
    )


async def test_breaker_cooldown_survives_between_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    import pitwall.db

    monkeypatch.setattr(pitwall.db, "get_pool", AsyncMock(return_value=_pool()))
    startup_ctx: dict[str, Any] = {}
    await _on_startup(startup_ctx)

    spends = iter([Decimal("100"), Decimal("0")])

    class _Gate:
        def __init__(self, *_args: object, **_kwargs: object) -> None: ...

        async def effective_limits(self) -> SimpleNamespace:
            return SimpleNamespace(monthly_budget_usd=Decimal("100"))

        async def current_mtd_spend(self) -> Decimal:
            return next(spends)

    monkeypatch.setattr("pitwall.reconciler.BudgetGate", _Gate)
    kill_switch = SimpleNamespace(activate=AsyncMock())

    def job_ctx() -> dict[str, Any]:
        # arq hands every job a shallow copy of the startup context.
        return {**startup_ctx, "settings": _settings(), "kill_switch": kill_switch}

    await _budget_breach_escalation(job_ctx())
    breaker = startup_ctx["budget_breaker"]
    assert breaker.state == "open"

    # Spend recovered, but the cooldown has not elapsed: a fresh breaker would read
    # "closed"; the persisted one stays open.
    await _budget_breach_escalation(job_ctx())
    assert startup_ctx["budget_breaker"] is breaker
    assert breaker.state == "open"
