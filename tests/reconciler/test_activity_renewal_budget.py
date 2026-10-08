"""An activity auto-renewal the budget refuses stops renewing and lets TTL teardown proceed."""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from pitwall.cost import BudgetRejected
from pitwall.cost.budget_gate import BudgetSnapshot
from pitwall.reconciler import _lease_expiry_reconcile
from tests.reconciler.test_lease_expiry_reconcile import (
    _FROZEN,
    _automation_lease,
    _make_mock_pool,
    _patch_automation_repositories,
    _patch_run_teardown,
    _plain_redis,
)

pytestmark = pytest.mark.anyio

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


def _row(lease: object) -> list[dict[str, object]]:
    return [
        {
            "id": lease.id,  # type: ignore[attr-defined]  # reason: Lease fixture
            "provider_id": lease.provider_id,  # type: ignore[attr-defined]  # reason: Lease fixture
            "runpod_pod_id": lease.runpod_pod_id,  # type: ignore[attr-defined]  # reason: Lease fixture
            "expires_at": lease.expires_at,  # type: ignore[attr-defined]  # reason: Lease fixture
            "auto_teardown_on_expiry": True,
            "state": "active",
        }
    ]


async def _sweep(monkeypatch: pytest.MonkeyPatch, lease: object) -> tuple[AsyncMock, AsyncMock]:
    _patch_automation_repositories(monkeypatch, lease)  # type: ignore[arg-type]  # reason: Lease fixture
    renew = AsyncMock(side_effect=_REFUSED)
    teardown = AsyncMock()
    monkeypatch.setattr("pitwall.reconciler.renew_lease", renew)
    monkeypatch.setattr("pitwall.reconciler._renewal_refusal_reason", AsyncMock(return_value=None))
    _patch_run_teardown(monkeypatch, teardown)
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = _row(lease)
    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": _FROZEN})
    return renew, teardown


async def test_a_refused_auto_renewal_before_expiry_leaves_the_lease_to_its_ttl(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="pitwall.reconciler")

    renew, teardown = await _sweep(monkeypatch, _automation_lease())

    renew.assert_awaited_once()
    teardown.assert_not_awaited()
    assert any("refused by the budget" in record.getMessage() for record in caplog.records)


async def test_a_refused_auto_renewal_at_expiry_tears_the_lease_down_for_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease(expires_at=_FROZEN - dt.timedelta(seconds=1))

    renew, teardown = await _sweep(monkeypatch, lease)

    renew.assert_awaited_once()
    teardown.assert_awaited_once()
    assert teardown.await_args.kwargs["reason"] == "budget"
