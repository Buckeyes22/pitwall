"""POST /v1/leases/{id}/renew answers a typed budget refusal, never a bare 500 (J35).

A renewal reserves rate x extension against the budget (Task 7c), so it builds the budget gate.
With ``PITWALL_MONTHLY_BUDGET_USD`` unset the gate raises ``BudgetNotConfigured``; the REST route
let it escape as an unhandled 500, where the CLI and MCP report ``budget_not_configured``.
"""

from __future__ import annotations

import importlib
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.db.repository import LeaseMutationResult
from tests.api._contract_helpers import build_app, client_for, override
from tests.leases.test_teardown import _lease, _provider

pytestmark = pytest.mark.anyio


def _renew_app(clear_app_module: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    lease = _lease()
    repo = AsyncMock()
    repo.get.return_value = lease
    repo.applied = []

    async def renew(lease_id: str, **kwargs: Any) -> LeaseMutationResult:
        # The repository's non-replay path: the budget check runs, then the renewal applies.
        conn = AsyncMock()  # no spend this month and no stored budget override
        conn.fetchval.return_value = Decimal("0")
        conn.fetchrow.return_value = None
        conn.fetch.return_value = []
        if kwargs["budget_check"] is not None:
            await kwargs["budget_check"](conn, kwargs["extension_usd"])
        repo.applied.append(lease_id)
        return LeaseMutationResult(lease)

    repo.renew.side_effect = renew
    mod = build_app(pool=MagicMock())
    routes = importlib.import_module("pitwall.api.routes.leases")
    mutations = importlib.import_module("pitwall.leases.mutations")
    teardown = importlib.import_module("pitwall.api.leases.teardown")
    providers = SimpleNamespace(get=AsyncMock(return_value=_provider()))
    capabilities = SimpleNamespace(get=AsyncMock(return_value=None))
    for module in (routes, mutations):
        monkeypatch.setattr(module, "ProviderRepository", lambda _pool: providers)
    monkeypatch.setattr(routes, "CapabilityRepository", lambda _pool: capabilities)
    monkeypatch.setattr(teardown, "CapabilityRepository", lambda _pool: capabilities)
    override(mod, routes._lease_repo, repo)
    return mod, repo


async def test_renew_without_a_configured_budget_is_a_typed_refusal(
    clear_app_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod, repo = _renew_app(clear_app_module, monkeypatch)
    monkeypatch.delenv("PITWALL_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.delenv("PITWALL_PER_REQUEST_MAX_USD", raising=False)

    async with client_for(mod) as client:
        resp = await client.post("/v1/leases/lease-target/renew", json={"extends_minutes": 10})

    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["error"] == "budget_not_configured"
    assert "PITWALL_MONTHLY_BUDGET_USD" in body["remedy"]
    assert repo.applied == []


async def test_renew_with_a_configured_budget_reserves_and_succeeds(
    clear_app_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod, repo = _renew_app(clear_app_module, monkeypatch)
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "100")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "50")

    async with client_for(mod) as client:
        resp = await client.post("/v1/leases/lease-target/renew", json={"extends_minutes": 10})

    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "lease-target"
    kwargs = repo.renew.await_args.kwargs
    # per_second_active 0.002 x 600 s
    assert str(kwargs["extension_usd"]) == "1.200000"
    assert kwargs["budget_check"] is not None


_INVALID_BUDGETS = [
    ("PITWALL_MONTHLY_BUDGET_USD", "lots"),
    ("PITWALL_MONTHLY_BUDGET_USD", "0"),
    ("PITWALL_MONTHLY_BUDGET_USD", "-5"),
    ("PITWALL_MONTHLY_BUDGET_USD", "NaN"),
    ("PITWALL_PER_REQUEST_MAX_USD", "Infinity"),
]


def _set_budget(monkeypatch: pytest.MonkeyPatch, name: str, value: str) -> None:
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "100")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "50")
    monkeypatch.setenv(name, value)


def _assert_typed_refusal(resp: Any, name: str, value: str | None) -> None:
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["error"] == "budget_not_configured"
    assert name in body["remedy"]
    if value is not None:
        # The refusal names the setting, never echoes its value.
        assert value not in resp.text


@pytest.mark.parametrize(("name", "value"), _INVALID_BUDGETS)
async def test_renew_with_an_invalid_budget_is_the_same_typed_refusal(
    clear_app_module: Any, monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    mod, repo = _renew_app(clear_app_module, monkeypatch)
    _set_budget(monkeypatch, name, value)

    async with client_for(mod) as client:
        resp = await client.post("/v1/leases/lease-target/renew", json={"extends_minutes": 10})

    _assert_typed_refusal(resp, name, value)
    assert repo.applied == []


def _launch_app(clear_app_module: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """POST /v1/leases whose launch runs the real budget admission (no pod, no database)."""
    from tests.providers.test_vm_lease_billing import _capability

    routes = importlib.import_module("pitwall.api.routes.leases")
    launch = importlib.import_module("pitwall.api.leases.launch")
    capability, provider = _capability(), _provider()
    plan = SimpleNamespace(plan_id="plan_j35", selected_provider_id=provider.id)
    routing = SimpleNamespace(
        preview=AsyncMock(return_value=plan),
        plan_execution=AsyncMock(return_value=(plan, {})),
    )

    async def admit_then_launch(**kwargs: Any) -> dict[str, Any]:
        await launch.admit_lease_launch(kwargs["pool"], kwargs["capability"], kwargs["provider"])
        raise AssertionError("a launch without a valid budget must not be admitted")

    monkeypatch.setattr(launch, "run_launch", admit_then_launch)
    mod = build_app(pool=MagicMock())
    override(
        mod,
        routes._capability_repo,
        SimpleNamespace(
            get_by_name=AsyncMock(return_value=capability), get=AsyncMock(return_value=capability)
        ),
    )
    override(mod, routes._provider_repo, SimpleNamespace(get=AsyncMock(return_value=provider)))
    override(mod, routes._lease_repo, AsyncMock())
    override(mod, routes._workload_repo, AsyncMock())
    override(mod, routes._routing_service, routing)
    return mod


@pytest.mark.parametrize(
    ("name", "value"), [("PITWALL_MONTHLY_BUDGET_USD", None), *_INVALID_BUDGETS]
)
async def test_launch_without_a_valid_budget_is_the_same_typed_refusal(
    clear_app_module: Any, monkeypatch: pytest.MonkeyPatch, name: str, value: str | None
) -> None:
    mod = _launch_app(clear_app_module, monkeypatch)
    if value is None:
        monkeypatch.delenv("PITWALL_MONTHLY_BUDGET_USD", raising=False)
        monkeypatch.delenv("PITWALL_PER_REQUEST_MAX_USD", raising=False)
    else:
        _set_budget(monkeypatch, name, value)

    async with client_for(mod) as client:
        resp = await client.post("/v1/leases", json={"capability_id": "gpu.lease"})

    _assert_typed_refusal(resp, name, value)


@pytest.mark.parametrize(("name", "value"), _INVALID_BUDGETS)
def test_the_gate_reports_an_invalid_budget_as_not_configured(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """The CLI and MCP key off the gate's error code, so they report the same refusal."""
    from pitwall.cost.budget_gate import BudgetGate, BudgetNotConfigured

    _set_budget(monkeypatch, name, value)
    with pytest.raises(BudgetNotConfigured) as raised:
        BudgetGate(MagicMock())
    assert name in str(raised.value)
    assert value not in str(raised.value)
    assert raised.value.to_response_body()["error"] == "budget_not_configured"
