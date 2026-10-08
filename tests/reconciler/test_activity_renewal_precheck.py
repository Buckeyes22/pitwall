"""The activity-renewal budget pre-check prices any provider's pricing and never aborts a sweep.

``_renewal_refusal_reason`` runs for real here: it prices the renewal at the lease's
settlement rate (``teardown.settlement_rate_per_second``) x the original TTL, for tagged
RunPod pricing and Lambda Cloud alike. Only the budget's answer, the repositories, and the
provider calls are fakes. A later lease past its TTL is torn down in the same sweep.
"""

from __future__ import annotations

import datetime as dt
import importlib
import logging
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.core.enums import LeaseRenewalPolicy, ProviderAdapterId
from pitwall.core.models import Lease, Provider
from pitwall.cost import BudgetRejected
from pitwall.cost.budget_gate import BudgetNotConfigured, BudgetSnapshot
from pitwall.reconciler import _lease_expiry_reconcile
from tests.reconciler.test_activity_renewal_budget import _row
from tests.reconciler.test_lease_expiry_reconcile import (
    _FROZEN,
    _automation_capability,
    _automation_lease,
    _automation_provider,
    _make_mock_pool,
    _patch_run_teardown,
    _plain_redis,
)

pytestmark = pytest.mark.anyio

#: provider config cost, adapter, and the renewal estimate: rate x the 60-min original TTL
_PROVIDERS = {
    "runpod-tagged-per-second": (
        {"kind": "per_second", "rate_per_second": "0.001"},
        ProviderAdapterId.RUNPOD,
        Decimal("3.6"),
    ),
    "lambda-per-vm-second": (
        {"kind": "per_vm_second", "rate_per_second": "0.00016"},
        ProviderAdapterId.LAMBDA_CLOUD,
        Decimal("0.576"),
    ),
}

_REFUSED = BudgetRejected(
    "monthly_budget",
    BudgetSnapshot(
        monthly_budget_usd=Decimal("2"),
        per_request_max_usd=Decimal("10"),
        mtd_spend_usd=Decimal("1.9"),
        estimate_usd=Decimal("3.6"),
        budget_remaining_usd=Decimal("0.1"),
    ),
)


def _provider(cost: dict[str, str], adapter: ProviderAdapterId) -> Provider:
    base = _automation_provider()
    return base.model_copy(update={"config": {**base.config, "cost": cost}, "adapter_id": adapter})


class _Capabilities:
    def __init__(self, pool: object) -> None:
        pass

    async def get(self, capability_id: str) -> Any:
        return _automation_capability()


async def _sweep(
    monkeypatch: pytest.MonkeyPatch,
    provider: Provider,
    budget: AsyncMock,
    *,
    broken_first: bool = False,
) -> tuple[AsyncMock, AsyncMock]:
    """Sweep an activity lease due for renewal, then a manual lease past its TTL."""
    renewing = _automation_lease(id="lease-renew", max_usd_per_hour=None)
    expired = _automation_lease(
        id="lease-expired",
        runpod_pod_id="pod-expired",
        max_usd_per_hour=None,
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        expires_at=_FROZEN - dt.timedelta(minutes=1),
    )
    leases: dict[str, Lease] = {lease.id: lease for lease in (renewing, expired)}

    async def get_lease(lease_id: str) -> Lease:
        if broken_first and lease_id == renewing.id:
            raise RuntimeError("lease row unreadable")
        return leases[lease_id]

    lease_repo = AsyncMock()
    lease_repo.get.side_effect = get_lease
    provider_repo = AsyncMock()
    provider_repo.get.return_value = provider
    provider_repo.get_many.side_effect = lambda ids: dict.fromkeys(ids, provider)
    capability_repo = AsyncMock()
    capability_repo.get.return_value = _automation_capability()
    monkeypatch.setenv("PITWALL_MONTHLY_BUDGET_USD", "100")
    monkeypatch.setenv("PITWALL_PER_REQUEST_MAX_USD", "50")
    monkeypatch.setattr("pitwall.reconciler.LeaseRepository", lambda _p: lease_repo)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _p: provider_repo)
    monkeypatch.setattr("pitwall.reconciler.CapabilityRepository", lambda _p: capability_repo)
    # The module the reconciler's lazy import resolves (other suites purge pitwall.api*).
    lease_teardown = importlib.import_module("pitwall.api.leases.teardown")
    monkeypatch.setattr(lease_teardown, "CapabilityRepository", _Capabilities)
    monkeypatch.setattr("pitwall.reconciler.CloudKillSwitch.ensure_disengaged", AsyncMock())
    monkeypatch.setattr("pitwall.reconciler.BudgetGate.check_available", budget)
    renew = AsyncMock(
        return_value=renewing.model_copy(
            update={"expires_at": renewing.expires_at + dt.timedelta(hours=1)}
        )
    )
    monkeypatch.setattr("pitwall.reconciler.renew_lease", renew)
    teardown = AsyncMock()
    _patch_run_teardown(monkeypatch, teardown)
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = _row(renewing) + _row(expired)

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": _FROZEN})

    return renew, teardown


def _torn_down(teardown: AsyncMock) -> list[str]:
    return [call.args[0] for call in teardown.await_args_list]


@pytest.mark.parametrize(("cost", "adapter", "estimate"), _PROVIDERS.values(), ids=_PROVIDERS)
async def test_a_renewal_within_budget_renews_and_the_expired_lease_is_torn_down(
    monkeypatch: pytest.MonkeyPatch,
    cost: dict[str, str],
    adapter: ProviderAdapterId,
    estimate: Decimal,
) -> None:
    budget = AsyncMock()

    renew, teardown = await _sweep(monkeypatch, _provider(cost, adapter), budget)

    budget.assert_awaited_once()
    assert budget.await_args.args[-1] == estimate
    renew.assert_awaited_once()
    assert _torn_down(teardown) == ["lease-expired"]


@pytest.mark.parametrize(
    "refusal",
    [_REFUSED, BudgetNotConfigured("PITWALL_MONTHLY_BUDGET_USD is not set")],
    ids=["budget_rejected", "budget_not_configured"],
)
@pytest.mark.parametrize(("cost", "adapter", "estimate"), _PROVIDERS.values(), ids=_PROVIDERS)
async def test_a_renewal_the_budget_refuses_is_skipped_and_the_expired_lease_is_torn_down(
    monkeypatch: pytest.MonkeyPatch,
    cost: dict[str, str],
    adapter: ProviderAdapterId,
    estimate: Decimal,
    refusal: Exception,
) -> None:
    renew, teardown = await _sweep(
        monkeypatch, _provider(cost, adapter), AsyncMock(side_effect=refusal)
    )

    renew.assert_not_awaited()
    assert _torn_down(teardown) == ["lease-expired"]


async def test_a_provider_without_a_lease_ttl_refuses_the_renewal_without_aborting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _provider(
        {"kind": "per_second", "rate_per_second": "0.001"}, ProviderAdapterId.RUNPOD
    )
    provider = provider.model_copy(
        update={"config": {k: v for k, v in provider.config.items() if k != "lease_ttl_ms"}}
    )

    renew, teardown = await _sweep(monkeypatch, provider, AsyncMock())

    renew.assert_not_awaited()
    assert _torn_down(teardown) == ["lease-expired"]


async def test_one_lease_failing_unexpectedly_does_not_stall_the_rest_of_the_sweep(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="pitwall.reconciler")
    cost, adapter, _estimate = _PROVIDERS["runpod-tagged-per-second"]

    renew, teardown = await _sweep(
        monkeypatch, _provider(cost, adapter), AsyncMock(), broken_first=True
    )

    renew.assert_not_awaited()
    assert _torn_down(teardown) == ["lease-expired"]
    assert any(
        "lease-renew" in record.getMessage() and "RuntimeError" in record.getMessage()
        for record in caplog.records
    )
