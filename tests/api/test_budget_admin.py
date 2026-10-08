"""Admin REST for runtime budget limits."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import pytest

from pitwall.cost.budget_limits import BudgetLimits, BudgetLimitsError
from tests.api._contract_helpers import build_app

pytestmark = pytest.mark.anyio
_SECRET = "test-admin-secret"
_HEADERS = {"X-Pitwall-Secret": _SECRET}


async def _client(mod: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://test")


async def test_get_requires_the_admin_secret(clear_app_module) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    mod = build_app(secret=_SECRET)
    async with await _client(mod) as client:
        assert (await client.get("/v1/admin/budget")).status_code == 401


async def test_get_and_put_delegate_to_the_service(
    clear_app_module, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    mod = build_app(secret=_SECRET)
    from pitwall.api.routes import budget as route

    seen: dict[str, Any] = {}

    async def fake_status(pool: Any) -> dict[str, Any]:
        return {"monthly_budget_usd": "50", "source": "runtime"}

    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        seen.update(kwargs)
        return BudgetLimits(Decimal("50"), Decimal("10"), "runtime")

    monkeypatch.setattr(route, "budget_status", fake_status)
    monkeypatch.setattr(route, "set_limits", fake_set)
    async with await _client(mod) as client:
        got = await client.get("/v1/admin/budget", headers=_HEADERS)
        put = await client.put(
            "/v1/admin/budget",
            headers=_HEADERS,
            json={"monthly_budget_usd": "50", "reason": "batch"},
        )
    assert got.status_code == 200 and got.json()["monthly_budget_usd"] == "50"
    assert put.status_code == 200 and put.json()["limits"]["monthly_budget_usd"] == "50"
    assert seen == {
        "monthly_budget_usd": "50",
        "per_request_max_usd": None,
        "reason": "batch",
        "actor": "api:admin",
    }


async def test_invalid_change_is_422(clear_app_module, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    mod = build_app(secret=_SECRET)
    from pitwall.api.routes import budget as route

    async def fake_set(pool: Any, **kwargs: Any) -> BudgetLimits:
        raise BudgetLimitsError("monthly_budget_usd must be a positive decimal")

    monkeypatch.setattr(route, "set_limits", fake_set)
    async with await _client(mod) as client:
        put = await client.put(
            "/v1/admin/budget", headers=_HEADERS, json={"monthly_budget_usd": "0", "reason": "x"}
        )
    assert put.status_code == 422
    assert put.json() == {
        "error": "invalid_budget_limits",
        "detail": "monthly_budget_usd must be a positive decimal",
    }


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"monthly_budget_usd": "1e100", "reason": "x"}, 422),
        ({"per_request_max_usd": "0.0000000001", "reason": "x"}, 422),
        ({"monthly_budget_usd": "50", "reason": "x\u0000"}, 422),
    ],
    ids=["overflows-numeric-14-6", "rounds-to-zero", "nul-in-reason"],
)
async def test_unstorable_change_is_422_before_any_write(  # type: ignore[no-untyped-def]  # reason: pytest fixture parameter is left unannotated
    clear_app_module, body: dict[str, str], status: int
) -> None:
    # Regression (admin fuzz): these reached Postgres and failed there as a 500.
    class _NoPool:
        def acquire(self) -> Any:
            raise AssertionError("no database access for an unstorable change")

    mod = build_app(secret=_SECRET, pool=_NoPool())  # type: ignore[arg-type]  # reason: a pool that refuses use
    async with await _client(mod) as client:
        put = await client.put("/v1/admin/budget", headers=_HEADERS, json=body)
    assert put.status_code == status
    assert "\u0000" not in put.text and "1e100" not in put.text
