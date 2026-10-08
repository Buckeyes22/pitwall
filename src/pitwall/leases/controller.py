from __future__ import annotations

import datetime as dt
from typing import Literal

from pitwall.core.enums import LeaseRenewalPolicy
from pitwall.core.models import Lease

RenewalDecision = Literal[
    "renew",
    "expire_max_lifetime",
    "expire_idle",
    "skip",
]


def is_idle(*, lease: Lease, now: dt.datetime, traffic_available: bool = True) -> bool:
    if not traffic_available:
        return False
    if lease.idle_timeout_min is None:
        return False
    activity_at = lease.last_traffic_at or lease.ready_at
    if activity_at is None:
        return False
    idle_for = now.astimezone(dt.UTC) - activity_at.astimezone(dt.UTC)
    return idle_for >= dt.timedelta(minutes=lease.idle_timeout_min)


def decide_renewal(
    *,
    lease: Lease,
    now: dt.datetime,
    max_lifetime_min: int,
    traffic_available: bool = True,
) -> RenewalDecision:
    current = now.astimezone(dt.UTC)
    lifetime_end = lease.created_at + dt.timedelta(minutes=max_lifetime_min)
    if current >= lifetime_end:
        return "expire_max_lifetime"
    if is_idle(lease=lease, now=current, traffic_available=traffic_available):
        return "expire_idle"
    if lease.renewal_policy != LeaseRenewalPolicy.ACTIVITY:
        return "skip"
    if lease.expires_at - current > dt.timedelta(minutes=15):
        return "skip"
    if lease.idle_timeout_min is None:
        if lease.last_traffic_at is None:
            return "skip"
        if current - lease.last_traffic_at > dt.timedelta(minutes=15):
            return "skip"
    return "renew"


__all__ = ["RenewalDecision", "decide_renewal", "is_idle"]
