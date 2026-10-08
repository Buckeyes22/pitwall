"""One failing teardown never stops the lease expiry sweep."""

from __future__ import annotations

import datetime as dt
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.api.leases.teardown import TeardownFailed
from pitwall.core.enums import LeaseRenewalPolicy, LeaseState
from pitwall.core.models import Lease
from pitwall.reconciler import _lease_expiry_reconcile

pytestmark = pytest.mark.anyio

_NOW = dt.datetime(2026, 9, 28, 12, 0, tzinfo=dt.UTC)
_IDS = ("lease-a", "lease-b", "lease-c")


def _expired_lease(lease_id: str) -> Lease:
    return Lease.model_validate(
        {
            "id": lease_id,
            "provider_id": "provider-1",
            "runpod_pod_id": f"pod-{lease_id}",
            "state": "creating",
            "created_at": _NOW - dt.timedelta(hours=2),
            "expires_at": _NOW - dt.timedelta(minutes=1),
            "renewal_policy": LeaseRenewalPolicy.MANUAL,
        }
    )


def _pool(*, stopping: list[dict[str, Any]] | None = None) -> MagicMock:
    rows = [
        {
            "id": lease_id,
            "provider_id": "provider-1",
            "expires_at": _NOW - dt.timedelta(minutes=1),
            "state": "active",
            "capability_name": None,
        }
        for lease_id in _IDS
    ]

    async def fetch(sql: str, *_args: object) -> list[dict[str, Any]]:
        if "state = 'stopping'" in sql:
            return stopping or []
        return rows

    conn = MagicMock()
    conn.fetch = AsyncMock(side_effect=fetch)
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire)
    return pool


def _patch_repositories(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    lease_repo = AsyncMock()
    lease_repo.list_active_for_activity_control.return_value = []
    lease_repo.get.side_effect = lambda lease_id: _expired_lease(lease_id)
    provider_repo = AsyncMock()
    provider_repo.get.return_value = None
    provider_repo.get_many.return_value = {}
    monkeypatch.setattr("pitwall.reconciler.LeaseRepository", lambda _pool: lease_repo)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    monkeypatch.setattr("pitwall.reconciler.CapabilityRepository", lambda _pool: AsyncMock())
    return lease_repo


def _patch_teardown(monkeypatch: pytest.MonkeyPatch, calls: list[dict[str, Any]]) -> None:
    import pitwall.api.leases.teardown as teardown_module

    async def fake_run_teardown(lease_id: str, **kwargs: Any) -> None:
        calls.append({"lease_id": lease_id, **kwargs})
        if lease_id == _IDS[0] and kwargs.get("wait_for_lock", True):
            raise TeardownFailed(f"provider teardown failed for lease {lease_id}")

    monkeypatch.setattr(teardown_module, "run_teardown", fake_run_teardown)


async def test_teardown_failure_does_not_stop_sweep(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _patch_repositories(monkeypatch)
    calls: list[dict[str, Any]] = []
    _patch_teardown(monkeypatch, calls)
    ctx: dict[str, Any] = {"db_pool": _pool(), "redis": None, "now": _NOW}

    with caplog.at_level("WARNING", logger="pitwall.reconciler"):
        await _lease_expiry_reconcile(ctx)

    assert [call["lease_id"] for call in calls] == list(_IDS)
    assert all(call["terminal_state"] is LeaseState.EXPIRED for call in calls)
    failure_logs = [r.getMessage() for r in caplog.records if "teardown" in r.getMessage()]
    assert any(_IDS[0] in message for message in failure_logs)
    assert ctx["lease_teardown_failures"] == 1


async def test_failed_lease_left_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    lease_repo = _patch_repositories(monkeypatch)
    close_raw = AsyncMock()
    monkeypatch.setattr("pitwall.reconciler._close_raw_pod_workload", close_raw)
    calls: list[dict[str, Any]] = []
    _patch_teardown(monkeypatch, calls)

    await _lease_expiry_reconcile({"db_pool": _pool(), "redis": None, "now": _NOW})

    # The sweep changes nothing about the failed lease: no state write, no workload close.
    lease_repo.update_state.assert_not_awaited()
    assert all(call.args[1] != _IDS[0] for call in close_raw.await_args_list)

    # The lease is still `stopping`, so the next tick's stuck-teardown pass retries it.
    stuck = [{"id": _IDS[0], "state": "stopping", "expires_at": _NOW - dt.timedelta(minutes=1)}]
    calls.clear()
    await _lease_expiry_reconcile({"db_pool": _pool(stopping=stuck), "redis": None, "now": _NOW})

    retry = calls[0]
    assert retry["lease_id"] == _IDS[0]
    assert retry["wait_for_lock"] is False
    assert retry["terminal_state"] is LeaseState.EXPIRED
