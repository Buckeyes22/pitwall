"""``LeaseRepository.latest_for_external_resource`` against a real schema."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from pitwall.core.enums import LeaseRenewalPolicy, LeaseState
from pitwall.core.models import Lease
from pitwall.db.repository import LeaseRepository
from tests.integration.conftest import requires_pg

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]


def _lease(lease_id: str, provider_id: str, pod_id: str, created: dt.datetime) -> Lease:
    return Lease(
        id=lease_id,
        provider_id=provider_id,
        runpod_pod_id=pod_id,
        state=LeaseState.CREATING,
        created_at=created,
        expires_at=created + dt.timedelta(hours=1),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
    )


async def test_latest_for_external_resource_returns_the_newest_matching_lease(
    pg_pool: Any,
) -> None:
    repo = LeaseRepository(pg_pool)
    now = dt.datetime.now(dt.UTC)
    await repo.create(_lease("lease_ext_old", "runpod_direct", "pod_ext_one", now))
    await repo.create(
        _lease("lease_ext_new", "runpod_direct", "pod_ext_one", now + dt.timedelta(seconds=5))
    )
    await repo.create(_lease("lease_ext_other_pod", "runpod_direct", "pod_ext_two", now))
    await repo.create(_lease("lease_ext_other_provider", "prov_other", "pod_ext_three", now))

    newest = await repo.latest_for_external_resource("runpod_direct", "pod_ext_one")
    other_provider = await repo.latest_for_external_resource("runpod_direct", "pod_ext_three")
    absent = await repo.latest_for_external_resource("runpod_direct", "pod_ext_missing")

    assert newest is not None and newest.id == "lease_ext_new"
    assert newest.external_resource_id == "pod_ext_one"
    assert other_provider is None
    assert absent is None
