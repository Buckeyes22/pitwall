"""Personal mode enforces a monthly budget and keeps an audit log (review finding #15).

Personal serving has no Postgres, so the budget gate and config audit never applied: a user
could launch leases without any monthly total and without a record of what ran.
"""

from __future__ import annotations

import json
import stat
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pitwall.personal.service import PersonalServeService, ServeRefused, ServeSpec
from pitwall.personal.state import StateStore
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
    # 60 minutes at 0.22/h reserves 0.22.
    return ServeSpec(
        model=MODEL, gpu_class=GPU, ttl_minutes=60, max_usd_per_hour=Decimal("1"), route=route
    )


def _service(
    tmp_path: Path, clock: FakeClock, runpod: FakeRunPod, **budget: Any
) -> PersonalServeService:
    return build_service(tmp_path, runpod, FakeRoutes(), clock=clock, **budget)


def _audit(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "audit.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


async def test_unconfigured_budget_refuses_before_any_pod(tmp_path: Path) -> None:
    runpod = FakeRunPod()
    service = _service(tmp_path, FakeClock(), runpod, monthly_budget_usd=None)

    with pytest.raises(ServeRefused) as refused:
        await service.serve(_spec("a"))

    assert refused.value.code == "budget_not_configured"
    assert runpod.created == []
    assert _audit(tmp_path)[-1] | {"ts": None} == {
        "ts": None,
        "action": "refused",
        "route": "a",
        "pod_id": None,
        "max_spend_usd": "0.220000",
        "reason": "budget_not_configured",
    }


async def test_a_lease_over_the_per_request_cap_is_refused(tmp_path: Path) -> None:
    runpod = FakeRunPod()
    service = _service(
        tmp_path,
        FakeClock(),
        runpod,
        monthly_budget_usd=Decimal("100"),
        per_request_max_usd=Decimal("0.10"),
    )

    with pytest.raises(ServeRefused) as refused:
        await service.serve(_spec("a"))

    assert refused.value.code == "per_request_cap"
    assert runpod.created == []


async def test_recorded_spend_and_running_leases_count_against_the_month(tmp_path: Path) -> None:
    runpod = FakeRunPod()
    StateStore(tmp_path).record_spend("2026-09", Decimal("0.10"))
    service = _service(tmp_path, FakeClock(), runpod, monthly_budget_usd=Decimal("0.40"))

    await service.serve(_spec("a"))  # 0.10 + 0.22 <= 0.40
    with pytest.raises(ServeRefused) as refused:
        await service.serve(_spec("b"))  # 0.10 + 0.22 reserved + 0.22 > 0.40

    assert refused.value.code == "monthly_budget"
    assert len(runpod.created) == 1


async def test_stop_records_accrued_cost_once_and_frees_the_reservation(tmp_path: Path) -> None:
    clock = FakeClock()
    service = _service(tmp_path, clock, FakeRunPod(), monthly_budget_usd=Decimal("0.40"))
    store = StateStore(tmp_path)

    lease = await service.serve(_spec("a"))
    clock.advance(minutes=30)
    await service.stop("a")
    await service.stop("a")  # a repeated stop must not charge twice

    assert store.month_spend("2026-09") == Decimal("0.110000")
    await service.serve(_spec("b"))  # 0.11 + 0.22 <= 0.40 once a's reservation is released

    records = _audit(tmp_path)
    assert [record["action"] for record in records] == ["serve", "stop", "serve"]
    assert records[0]["pod_id"] == lease.pod_id
    assert records[0]["max_spend_usd"] == "0.220000"
    assert records[1]["accrued_usd"] == "0.110000"


async def test_a_vanished_pod_is_charged_up_to_its_deadline(tmp_path: Path) -> None:
    clock = FakeClock()
    runpod = FakeRunPod()
    service = _service(tmp_path, clock, runpod, monthly_budget_usd=Decimal("10"))

    lease = await service.serve(_spec("a"))
    runpod.vanish(lease.pod_id)
    clock.advance(minutes=90)
    await service.status()

    assert StateStore(tmp_path).month_spend("2026-09") == Decimal("0.220000")
    assert _audit(tmp_path)[-1]["action"] == "gone"


async def test_state_files_are_owner_only(tmp_path: Path) -> None:
    service = _service(tmp_path, FakeClock(), FakeRunPod(), monthly_budget_usd=Decimal("10"))
    await service.serve(_spec("a"))
    await service.stop("a")

    for name in ("audit.jsonl", "ledger.json"):
        assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600, name
