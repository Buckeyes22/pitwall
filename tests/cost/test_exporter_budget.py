"""The cost exporter needs a configured monthly budget and keeps it as a Decimal."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI

from pitwall.cost import exporter
from pitwall.cost.budget_gate import BudgetNotConfigured


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PITWALL_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://pitwall@127.0.0.1/none")


@pytest.mark.anyio
async def test_refuses_without_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    opened = AsyncMock()
    monkeypatch.setattr(exporter, "get_pool", opened)

    with pytest.raises(BudgetNotConfigured, match="PITWALL_MONTHLY_BUDGET_USD"):
        async with exporter.lifespan(FastAPI()):
            pass

    opened.assert_not_awaited()


@pytest.mark.anyio
async def test_refuses_blank_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "   ")
    monkeypatch.setattr(exporter, "get_pool", AsyncMock())

    with pytest.raises(BudgetNotConfigured):
        async with exporter.lifespan(FastAPI()):
            pass


@pytest.mark.anyio
async def test_budget_is_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "123.45")
    pool = SimpleNamespace(close=AsyncMock())
    get_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(exporter, "get_pool", get_pool)
    monkeypatch.setattr(exporter, "_poll_loop", AsyncMock())
    monkeypatch.setattr(exporter, "close_pool", AsyncMock())  # never touch a shared real pool
    app = FastAPI()

    async with exporter.lifespan(app):
        assert app.state.budget == Decimal("123.45")
        assert isinstance(app.state.budget, Decimal)

    assert not hasattr(exporter, "BUDGET_USD")


@pytest.mark.anyio
async def test_pool_comes_from_the_shared_pool_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "10")
    pool = SimpleNamespace(close=AsyncMock())
    get_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(exporter, "get_pool", get_pool)
    monkeypatch.setattr(exporter, "_poll_loop", AsyncMock())
    closed = AsyncMock()
    monkeypatch.setattr(exporter, "close_pool", closed)

    async with exporter.lifespan(FastAPI()):
        pass

    get_pool.assert_awaited_once()
    assert get_pool.await_args.args[0] == "postgresql://pitwall@127.0.0.1/none"
    closed.assert_awaited_once()
