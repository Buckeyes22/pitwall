"""A same-key replay that finds its original pod gone settles that pod's lease at once.

The original attempt's workload was kept open (its rollback could not terminate the pod).
A same-key replay then finds no lease and the pod gone. The tool leases the pod by its id,
counted from the attempt's journal ``started`` row, and settles that lease immediately
through ``settle_absent_raw_pod_lease`` in ``pitwall.api.leases.teardown``. It does not
wait for the lease sweep, which probes only leases within an hour of expiry: a 480-minute
TTL would otherwise hold the reservation for hours. Journal, workload, and lease are real PostgreSQL rows.
"""

from __future__ import annotations

import datetime as dt
import importlib
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.shared.exceptions import MCPError

from pitwall.mcp.tools import runpod_resources
from pitwall.runpod_control_plane import PodCreateRequest, RunPodControlPlaneService
from tests.integration.conftest import requires_pg
from tests.runpod_control_plane.test_service import RecordingBackend

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

_KEY = "replay-gone-pod-480"


def _request(cap: str | None) -> PodCreateRequest:
    return PodCreateRequest(
        intent="apply",
        idempotency_key=_KEY,
        name="replay-gone-pod",
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=480,
        max_cost_per_hour=Decimal(cap) if cap is not None else None,
    )


@pytest.mark.parametrize(
    ("cap", "cost_per_hr", "rate"),
    [
        pytest.param("0.60", "0.44", "0.60", id="capped-charges-the-cap"),
        pytest.param(None, "0.44", "0.44", id="uncapped-charges-costPerHr"),
        pytest.param(None, None, "0.50", id="uncapped-no-costPerHr-charges-the-reservation-rate"),
        pytest.param(None, "0.00004", "0.50", id="uncapped-sub-cent-costPerHr-falls-back"),
        pytest.param(None, "1000000000", "0.50", id="uncapped-absurd-costPerHr-falls-back"),
    ],
)
async def test_a_gone_pod_on_replay_is_settled_at_once_from_the_attempt_start(
    pg_pool: Any,
    monkeypatch: pytest.MonkeyPatch,
    cap: str | None,
    cost_per_hr: str | None,
    rate: str,
) -> None:
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state,"
            " idempotency_key, submitted_at, cost_estimate_usd, cost_ceiling_usd)"
            " VALUES ('wkl_kept_open', 'runpod_direct', 'runpod_direct', 'inference',"
            " 'queued', $1, now() - interval '30 minutes', 4.80, 4.80)",
            _KEY,
        )
    backend = RecordingBackend()

    async def create(request: PodCreateRequest) -> dict[str, Any]:
        backend.calls.append("pods.create")
        pod = backend._pod("pod_new", request.name)
        if cost_per_hr is None:
            pod.pop("costPerHr")
        else:
            pod["costPerHr"] = cost_per_hr
        return pod

    backend.create_pod = create  # type: ignore[method-assign]  # reason: per-case pod price
    service = RunPodControlPlaneService(
        backend=backend, audit_pool=pg_pool, environ={"RUNPOD_API_KEY": "replay-key"}, timeout_s=1
    )
    assert (await service.create_pod(_request(cap))).resource_id == "pod_new"
    # The create began 30 minutes ago and RunPod answered 2 minutes later (a long create).
    async with pg_pool.acquire() as conn:
        started_at = await conn.fetchval(
            "UPDATE pitwall.config_audit SET created_at = now() - interval '30 minutes'"
            " WHERE new_value ->> 'idempotency_key' = $1 AND new_value ->> 'state' = 'started'"
            " RETURNING created_at",
            _KEY,
        )
        await conn.execute(
            "UPDATE pitwall.config_audit SET created_at = now() - interval '28 minutes'"
            " WHERE new_value ->> 'idempotency_key' = $1 AND new_value ->> 'state' = 'completed'",
            _KEY,
        )

    # The pod has since gone. The replay reuses the kept-open workload (no budget check).
    backend.pod_present = False

    async def fake_service(*, mutation: bool = False) -> Any:
        return service

    async def fake_pool(*args: Any, **kwargs: Any) -> Any:
        return pg_pool

    async def kept_open_admission(pool: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(workload_id="wkl_kept_open", is_new=False)

    terminate = AsyncMock(return_value=None)  # the teardown's idempotent terminate of a gone pod
    monkeypatch.setattr(runpod_resources, "_service", fake_service)
    monkeypatch.setattr(runpod_resources, "get_pool", fake_pool)
    monkeypatch.setattr(runpod_resources, "admit_raw_pod_lease", kept_open_admission)
    # The module object the tool's lazy import resolves (other suites purge pitwall.api*).
    teardown_module = importlib.import_module("pitwall.api.leases.teardown")
    monkeypatch.setattr(teardown_module, "terminate_pod", terminate)

    before = dt.datetime.now(dt.UTC)
    with pytest.raises(MCPError) as excinfo:
        await runpod_resources.pitwall_runpod_create_pod(_request(cap))
    after = dt.datetime.now(dt.UTC)

    assert (excinfo.value.error.data or {})["error"] == "resource_not_found"
    assert backend.calls.count("pods.create") == 1
    async with pg_pool.acquire() as conn:
        lease = await conn.fetchrow(
            "SELECT state, created_at, expires_at, terminated_at, terminated_reason,"
            " cost_accrued_usd, max_usd_per_hour FROM pitwall.leases"
            " WHERE workload_id = 'wkl_kept_open'"
        )
        workload = await conn.fetchrow(
            "SELECT state, cost_actual_usd, cost_actual_provenance"
            " FROM pitwall.workloads WHERE id = 'wkl_kept_open'"
        )
    # Leased from the attempt's start (the started row, not the completed row) ...
    # (age on the database clock, applied to the broker clock: equal within a second)
    assert abs((lease["created_at"] - started_at).total_seconds()) < 1
    assert lease["expires_at"] == lease["created_at"] + dt.timedelta(minutes=480)
    # The lease's cap is only the caller's: null when uncapped, even though RunPod quoted a price.
    assert lease["max_usd_per_hour"] == (Decimal(cap) if cap is not None else None)
    # ... and settled during the call, not hours later by the sweep.
    assert lease["state"] == "stopped"
    assert lease["terminated_reason"] == "pod_absent"
    assert before <= lease["terminated_at"] <= after
    window_s = (lease["terminated_at"] - lease["created_at"]).total_seconds()
    expected = (Decimal(rate) / Decimal(3600) * Decimal(str(window_s))).quantize(
        Decimal("0.000001")
    )
    assert abs(lease["cost_accrued_usd"] - expected) <= Decimal("0.000002")
    half_hour = Decimal(rate) / 2  # about 30 minutes at the rate, never $0
    assert half_hour - Decimal("0.01") < lease["cost_accrued_usd"] < half_hour + Decimal("0.01")
    # The workload is closed at that cost, so its 4.80 reservation no longer counts.
    assert workload["state"] == "completed"
    assert workload["cost_actual_usd"] == lease["cost_accrued_usd"]
    assert workload["cost_actual_provenance"] == "lease_teardown"
    terminate.assert_awaited_once_with("pod_new")
