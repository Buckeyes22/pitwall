"""Serving refuses a lease the month's committed ceilings cannot cover."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from pitwall.personal.service import ServeRefused, ServeSpec
from pitwall.personal.state import PersonalLease, StateStore
from tests.fakes.personal import (
    GPU,
    MODEL,
    FakeClock,
    FakeRoutes,
    FakeRunPod,
    build_service,
    fake_plan,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def _catalogue_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pitwall.personal.service.plan_catalogue_model", fake_plan)


def _spec(route: str) -> ServeSpec:
    # 60 minutes at the fake market's 0.22/h commits 0.22.
    return ServeSpec(
        model=MODEL, gpu_class=GPU, ttl_minutes=60, max_usd_per_hour=Decimal("1"), route=route
    )


def _running_lease(clock: FakeClock, route: str) -> PersonalLease:
    now = clock.now()
    return PersonalLease(
        route=route,
        pod_id=f"pod-{route}",
        model=MODEL,
        served_model_id="m",
        engine="llama.cpp",
        variant=None,
        image="img",
        gpu_class=GPU,
        gpu_count=1,
        cloud="community",
        price_per_hour_usd="0.220000",
        endpoint_url="https://pod.example/v1",
        key_env="PITWALL_ENDPOINT_KEY",
        launched_at=now,
        deadline_at=now + dt.timedelta(minutes=60),
        state="ready",
    )


async def test_serve_refused_over_monthly_budget(tmp_path: Path) -> None:
    clock = FakeClock()
    runpod = FakeRunPod()
    StateStore(tmp_path).upsert(_running_lease(clock, "first"))  # commits 0.22 of this month
    service = build_service(
        tmp_path, runpod, FakeRoutes(), clock=clock, monthly_budget_usd=Decimal("0.40")
    )

    with pytest.raises(ServeRefused) as refused:
        await service.serve(_spec("second"))  # 0.22 committed + 0.22 new > 0.40

    assert refused.value.code == "monthly_budget"
    assert runpod.created == []


async def test_serve_allowed_up_to_the_monthly_budget(tmp_path: Path) -> None:
    clock = FakeClock()
    StateStore(tmp_path).upsert(_running_lease(clock, "first"))
    service = build_service(
        tmp_path, FakeRunPod(), FakeRoutes(), clock=clock, monthly_budget_usd=Decimal("0.44")
    )

    lease = await service.serve(_spec("second"))  # 0.22 + 0.22 == 0.44

    assert lease.route == "second"


async def test_last_months_leases_do_not_count(tmp_path: Path) -> None:
    clock = FakeClock()
    store = StateStore(tmp_path)
    old = _running_lease(clock, "old")
    store.upsert(
        old.model_copy(
            update={
                "launched_at": old.launched_at - dt.timedelta(days=60),
                "deadline_at": old.deadline_at - dt.timedelta(days=60),
            }
        )
    )
    service = build_service(
        tmp_path, FakeRunPod(), FakeRoutes(), clock=clock, monthly_budget_usd=Decimal("0.30")
    )

    lease = await service.serve(_spec("new"))

    assert lease.route == "new"
