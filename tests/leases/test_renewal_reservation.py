"""A lease renewal reserves rate x extension under the budget lock, on every adapter (Task 7c)."""

from __future__ import annotations

import importlib
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.core.enums import ProviderAdapterId
from pitwall.cost.budget_gate import BudgetRejected, BudgetSnapshot
from pitwall.db.repository import LeaseMutationResult
from pitwall.leases import mutations
from tests.leases.test_teardown import _lease, _provider

pytestmark = pytest.mark.anyio

_SNAPSHOT = BudgetSnapshot(
    monthly_budget_usd=Decimal("2"),
    per_request_max_usd=Decimal("10"),
    mtd_spend_usd=Decimal("1.152"),
    estimate_usd=Decimal("5.76"),
    budget_remaining_usd=Decimal("0.848"),
)


class _Capabilities:
    def __init__(self, pool: object) -> None:
        pass

    async def get(self, capability_id: str) -> Any:
        from tests.providers.test_vm_lease_billing import _capability

        return _capability()


def _world(monkeypatch: pytest.MonkeyPatch, provider: Any, lease: Any = None) -> Any:
    lease = lease or _lease()
    repo = SimpleNamespace(
        get=AsyncMock(return_value=lease),
        renew=AsyncMock(return_value=LeaseMutationResult(lease)),
    )
    monkeypatch.setattr(
        mutations,
        "ProviderRepository",
        lambda pool: SimpleNamespace(get=AsyncMock(return_value=provider)),
    )
    teardown = importlib.import_module("pitwall.api.leases.teardown")
    monkeypatch.setattr(teardown, "CapabilityRepository", _Capabilities)
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "100")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "50")
    return repo


async def _renew(repo: Any, minutes: int = 30) -> None:
    await mutations.renew_lease(
        repo, "lease-target", extends_minutes=minutes, actor="rest:lease", pool=object()
    )


def _vm(adapter: ProviderAdapterId, cost: dict[str, Any]) -> Any:
    return _provider().model_copy(
        update={"adapter_id": adapter, "credential_ref": "VM_KEY", "config": {"cost": cost}}
    )


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        (_provider(), Decimal("3.600000")),  # RunPod per_second_active 0.002 x 30 min
        (_vm(ProviderAdapterId.LAMBDA_CLOUD, {"rate_per_second": "0.00016"}), Decimal("0.288000")),
        (
            _vm(
                ProviderAdapterId.VAST,
                {"kind": "per_second", "price_per_hour": "0.36", "bid_price_per_hour": "0.72"},
            ),
            Decimal("0.360000"),
        ),
    ],
    ids=["runpod", "lambda_cloud", "vast"],
)
async def test_a_renewal_reserves_the_settlement_rate_times_the_extension(
    monkeypatch: pytest.MonkeyPatch, provider: Any, expected: Decimal
) -> None:
    repo = _world(monkeypatch, provider)

    await _renew(repo)

    kwargs = repo.renew.await_args.kwargs
    assert kwargs["extension_usd"] == expected
    assert callable(kwargs["budget_check"])


async def test_a_runpod_lease_without_a_rate_reserves_its_cap_then_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    no_rate = _provider().model_copy(update={"config": {}})
    capped = _lease().model_copy(update={"max_usd_per_hour": Decimal("1.2")})
    repo = _world(monkeypatch, no_rate, capped)
    await _renew(repo)
    assert repo.renew.await_args.kwargs["extension_usd"] == Decimal("0.600000")

    repo = _world(monkeypatch, no_rate)
    await _renew(repo)
    assert repo.renew.await_args.kwargs["extension_usd"] == Decimal("0.250000")


async def test_the_budget_check_takes_the_budget_lock_then_checks_the_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.cost.budget_gate import PITWALL_BUDGET_LOCK_KEY

    repo = _world(monkeypatch, _provider())
    checked: list[tuple[Decimal, object]] = []

    class Gate:
        def __init__(self, pool: object) -> None:
            pass

        async def check_available(self, amount: Decimal, *, _conn: object) -> None:
            checked.append((amount, _conn))

    class Conn:
        executed: list[tuple[Any, ...]] = []

        async def execute(self, *args: Any) -> None:
            self.executed.append(args)

    monkeypatch.setattr(mutations, "BudgetGate", Gate)
    await _renew(repo)
    conn = Conn()
    await repo.renew.await_args.kwargs["budget_check"](conn, Decimal("3.6"))

    assert conn.executed == [("SELECT pg_advisory_xact_lock($1)", PITWALL_BUDGET_LOCK_KEY)]
    assert checked == [(Decimal("3.6"), conn)]


async def test_a_renewal_of_a_missing_lease_reserves_nothing_and_is_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _world(monkeypatch, _provider())
    repo.get.return_value = None
    repo.renew.return_value = None

    with pytest.raises(mutations.LeaseMutationNotFound):
        await _renew(repo)
    assert repo.renew.await_args.kwargs["extension_usd"] is None


async def test_a_renewal_needs_the_pool_to_reserve() -> None:
    repo = SimpleNamespace(get=AsyncMock(), renew=AsyncMock())
    with pytest.raises(ValueError, match="pool"):
        await mutations.renew_lease(repo, "lease-target", extends_minutes=30, actor="rest:lease")
    repo.renew.assert_not_awaited()


async def test_a_budget_refusal_propagates_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _world(monkeypatch, _provider())
    repo.renew.side_effect = BudgetRejected("monthly_budget", _SNAPSHOT)

    with pytest.raises(BudgetRejected):
        await _renew(repo)


def test_the_cli_reports_a_renewal_budget_refusal_as_budget_rejected() -> None:
    from pitwall.cli.leases import _lease_error_code

    assert _lease_error_code(BudgetRejected("monthly_budget", _SNAPSHOT)) == "budget_rejected"


def test_mcp_reports_a_renewal_budget_refusal_with_its_remedy() -> None:
    from mcp.server.mcpserver.exceptions import ToolError

    from pitwall.mcp.error_adapter import adapt_error
    from pitwall.mcp.safe_boundary import BUDGET_REMEDY, _stable_error_payload

    wrapped = ToolError("refused")
    wrapped.__cause__ = adapt_error(BudgetRejected("monthly_budget", _SNAPSHOT))
    payload = _stable_error_payload(wrapped)
    assert payload["error"] == "budget_rejected" and payload["remedy"] == BUDGET_REMEDY


async def test_rest_reports_a_renewal_budget_refusal_as_budget_rejected(
    clear_app_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.api._contract_helpers import client_for
    from tests.api.test_leases_contract import _lease_repo_app

    mod, _repo = _lease_repo_app(clear_app_module, get=_lease())
    routes = importlib.import_module("pitwall.api.routes.leases")
    monkeypatch.setattr(
        routes, "ProviderRepository", lambda pool: SimpleNamespace(get=AsyncMock(return_value=None))
    )
    monkeypatch.setattr(
        routes,
        "renew_lease_service",
        AsyncMock(side_effect=BudgetRejected("monthly_budget", _SNAPSHOT)),
    )
    async with client_for(mod) as client:
        response = await client.post("/v1/leases/lease-target/renew", json={"extends_minutes": 600})

    assert response.json()["error"] == "budget_rejected"
    assert response.status_code == BudgetRejected.status_code
