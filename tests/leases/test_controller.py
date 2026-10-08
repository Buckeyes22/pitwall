from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pitwall.core.enums import LeaseRenewalPolicy, LeaseState
from pitwall.core.models import Lease
from pitwall.leases.controller import decide_renewal, is_idle

_NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def _lease(**changes: object) -> Lease:
    values: dict[str, object] = {
        "id": "lease-controller",
        "provider_id": "provider-controller",
        "runpod_pod_id": "pod-controller",
        "state": LeaseState.CREATING,
        "created_at": _NOW - timedelta(hours=1),
        "expires_at": _NOW + timedelta(minutes=14),
        "renewal_policy": LeaseRenewalPolicy.ACTIVITY,
        "ready_at": _NOW - timedelta(minutes=10),
        "last_traffic_at": _NOW - timedelta(minutes=2),
        "idle_timeout_min": 20,
    }
    values.update(changes)
    return Lease.model_validate(values)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"last_traffic_at": _NOW - timedelta(minutes=19)}, False),
        ({"last_traffic_at": _NOW - timedelta(minutes=20)}, True),
        (
            {
                "last_traffic_at": None,
                "ready_at": _NOW - timedelta(minutes=20),
            },
            True,
        ),
        ({"last_traffic_at": None, "ready_at": None}, False),
        ({"idle_timeout_min": None}, False),
    ],
)
def test_is_idle_uses_traffic_then_ready_and_missing_is_busy(
    changes: dict[str, object],
    expected: bool,
) -> None:
    assert is_idle(lease=_lease(**changes), now=_NOW) is expected


def test_decide_renewal_renews_activity_at_t15_when_busy() -> None:
    assert (
        decide_renewal(
            lease=_lease(),
            now=_NOW,
            max_lifetime_min=1440,
        )
        == "renew"
    )


def test_decide_renewal_skips_before_t15_or_for_manual_policy() -> None:
    assert (
        decide_renewal(
            lease=_lease(expires_at=_NOW + timedelta(minutes=16)),
            now=_NOW,
            max_lifetime_min=1440,
        )
        == "skip"
    )
    assert (
        decide_renewal(
            lease=_lease(renewal_policy=LeaseRenewalPolicy.MANUAL),
            now=_NOW,
            max_lifetime_min=1440,
        )
        == "skip"
    )


def test_decide_renewal_prioritizes_max_lifetime_then_idle() -> None:
    maxed = _lease(created_at=_NOW - timedelta(minutes=1440))
    assert (
        decide_renewal(
            lease=maxed,
            now=_NOW,
            max_lifetime_min=1440,
        )
        == "expire_max_lifetime"
    )
    idle = _lease(last_traffic_at=_NOW - timedelta(minutes=21))
    assert (
        decide_renewal(
            lease=idle,
            now=_NOW,
            max_lifetime_min=1440,
        )
        == "expire_idle"
    )


def test_decide_renewal_treats_missing_traffic_and_ready_as_busy() -> None:
    lease = _lease(last_traffic_at=None, ready_at=None)

    assert (
        decide_renewal(
            lease=lease,
            now=_NOW,
            max_lifetime_min=1440,
        )
        == "renew"
    )


def test_activity_without_idle_timeout_requires_traffic_within_fifteen_minutes() -> None:
    stale = _lease(
        idle_timeout_min=None,
        last_traffic_at=_NOW - timedelta(minutes=16),
    )
    recent = stale.model_copy(
        update={"last_traffic_at": _NOW - timedelta(minutes=15)},
    )

    assert decide_renewal(lease=stale, now=_NOW, max_lifetime_min=1440) == "skip"
    assert decide_renewal(lease=recent, now=_NOW, max_lifetime_min=1440) == "renew"


def test_unavailable_traffic_skips_idle_decision_without_ready_fallback() -> None:
    lease = _lease(
        last_traffic_at=None,
        ready_at=_NOW - timedelta(minutes=30),
    )

    assert (
        decide_renewal(
            lease=lease,
            now=_NOW,
            max_lifetime_min=1440,
            traffic_available=False,
        )
        == "renew"
    )
