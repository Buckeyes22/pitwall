from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall.core.enums import LeaseRenewalPolicy
from pitwall.core.models import Lease
from pitwall.db.repository import LeaseRepository, _lease_from_row

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _row(**updates: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "lease-activity",
        "provider_id": "provider-activity",
        "runpod_pod_id": "pod-activity",
        "state": "creating",
        "created_at": _NOW,
        "expires_at": _NOW + timedelta(hours=1),
        "renewal_policy": "activity",
        "auto_teardown_on_expiry": True,
        "endpoints": None,
        "readiness": None,
        "cost_accrued_usd": None,
        "last_health_at": None,
        "terminated_at": None,
        "terminated_reason": None,
        "last_traffic_at": _NOW + timedelta(minutes=1),
        "ready_at": _NOW + timedelta(seconds=30),
        "idle_timeout_min": 20,
        "max_usd_per_hour": Decimal("2.5000"),
    }
    row.update(updates)
    return row


def _pool(*, fetchrow: object = None, fetch: object = None) -> MagicMock:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetch = AsyncMock(return_value=[] if fetch is None else fetch)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    acquire = MagicMock()
    acquire.__aenter__ = AsyncMock(return_value=conn)
    acquire.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = acquire
    pool.conn = conn
    return pool


def test_lease_row_maps_activity_policy_and_nullable_automation_fields() -> None:
    lease = _lease_from_row(_row())

    assert lease.renewal_policy is LeaseRenewalPolicy.ACTIVITY
    assert lease.last_traffic_at == _NOW + timedelta(minutes=1)
    assert lease.ready_at == _NOW + timedelta(seconds=30)
    assert lease.idle_timeout_min == 20
    assert lease.max_usd_per_hour == Decimal("2.5000")


def test_lease_model_rejects_idle_timeout_below_five() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 5"):
        Lease.model_validate(_row(idle_timeout_min=4))


@pytest.mark.anyio
async def test_mark_ready_updates_ready_at_and_returns_lease() -> None:
    row = _row(ready_at=_NOW)
    pool = _pool(fetchrow=row)

    result = await LeaseRepository(pool).mark_ready("lease-activity", ready_at=_NOW)

    assert result.ready_at == _NOW
    pool.conn.fetchrow.assert_awaited_once()
    sql, ready_at, lease_id = pool.conn.fetchrow.await_args.args
    assert "SET ready_at = $1" in sql
    assert (ready_at, lease_id) == (_NOW, "lease-activity")


@pytest.mark.anyio
async def test_record_traffic_is_monotonic_write_through() -> None:
    pool = _pool()
    seen_at = _NOW + timedelta(minutes=2)

    await LeaseRepository(pool).record_traffic("lease-activity", seen_at=seen_at)

    sql, actual_seen_at, lease_id = pool.conn.execute.await_args.args
    assert "GREATEST(COALESCE(last_traffic_at, $1), $1)" in sql
    assert (actual_seen_at, lease_id) == (seen_at, "lease-activity")


@pytest.mark.anyio
async def test_claim_expiry_warning_is_atomic_and_scoped_to_expiry() -> None:
    pool = _pool(fetchrow={"id": "lease-activity"})
    expires_at = _NOW + timedelta(hours=1)

    claimed = await LeaseRepository(pool).claim_expiry_warning(
        "lease-activity", expires_at=expires_at, threshold_minutes=15
    )

    assert claimed is True
    sql, lease_id, actual_expiry, threshold = pool.conn.fetchrow.await_args.args
    assert "warning_expires_at IS DISTINCT FROM $2" in sql
    assert "NOT ($3 = ANY(warning_thresholds))" in sql
    assert (lease_id, actual_expiry, threshold) == ("lease-activity", expires_at, 15)


@pytest.mark.anyio
async def test_list_active_for_activity_control_returns_models() -> None:
    pool = _pool(fetch=[_row(state="creating")])

    leases = await LeaseRepository(pool).list_active_for_activity_control()

    assert [lease.id for lease in leases] == ["lease-activity"]
    sql, states = pool.conn.fetch.await_args.args
    assert "state = ANY" in sql
    assert "last_traffic_at" in sql
    assert "active" in states
