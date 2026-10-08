"""The orphaned-workload reaper resolves an unknown-outcome raw-pod create by its marker.

A pod create that timed out before RunPod returned an id keeps its journal row ``started``
and leaves its workload open with no lease. Before the reaper charges such a workload, it
lists pods (a fake ``get_pods``) and adopts only the pod whose env carries the
``PITWALL_CREATE_ATTEMPT`` marker the create sent: that pod is leased, no pod with the
marker or the name closes the workload at $0, and anything else holds it. A same-name pod
without the marker is never adopted or terminated. The journal row, the workload, and the
lease are real PostgreSQL rows.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

import pitwall.reconciler as recon
from pitwall.db.repository import LeaseRepository
from pitwall.runpod_control_plane import (
    CREATE_ATTEMPT_ENV,
    PodCreateRequest,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    create_attempt_marker,
    journal_lock_name,
)
from tests.integration.conftest import requires_pg
from tests.runpod_control_plane.test_service import RecordingBackend

pytestmark = [pytest.mark.asyncio, pytest.mark.integration, requires_pg]

_NAME = "unknown-outcome-pod"


async def _unknown_outcome_create(
    pg_pool: Any,
    key: str,
    *,
    attempt_age: str = "10 minutes",
    workload_age: str = "10 minutes",
) -> tuple[str, dt.datetime, dict[str, str]]:
    """Admit a raw-pod workload and leave its create ``started`` after a timeout.

    Returns the workload id, the attempt's start, and the env the create sent to RunPod.
    """
    workload_id = f"wkl_{key}"
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state,"
            " idempotency_key, submitted_at, cost_estimate_usd, cost_ceiling_usd)"
            " VALUES ($1, 'runpod_direct', 'runpod_direct', 'inference', 'queued', $2,"
            f" now() - interval '{workload_age}', 0.50, 0.50)",
            workload_id,
            key,
        )

    backend = RecordingBackend()
    sent: list[dict[str, str]] = []

    async def times_out(request: PodCreateRequest) -> dict[str, Any]:
        sent.append(dict(request.env))
        raise TimeoutError("create timed out")

    backend.create_pod = times_out  # type: ignore[method-assign]  # reason: per-test failure
    service = RunPodControlPlaneService(
        backend=backend, audit_pool=pg_pool, environ={"RUNPOD_API_KEY": "reap-key"}, timeout_s=1
    )
    request = PodCreateRequest(
        intent="apply",
        idempotency_key=key,
        name=_NAME,
        image="example/image:1",
        gpu_type_ids=["NVIDIA L4"],
        ttl_minutes=60,
        max_cost_per_hour=Decimal("0.50"),
    )
    with pytest.raises(RunPodControlPlaneError):
        await service.create_pod(request)

    async with pg_pool.acquire() as conn:
        started_at = await conn.fetchval(
            "UPDATE pitwall.config_audit"
            f" SET created_at = now() - interval '{attempt_age}'"
            " WHERE new_value ->> 'idempotency_key' = $1"
            "   AND new_value ->> 'state' = 'started'"
            " RETURNING created_at",
            key,
        )
    assert isinstance(started_at, dt.datetime)
    (sent_env,) = sent
    return workload_id, started_at, sent_env


def _fake_pods(monkeypatch: pytest.MonkeyPatch, result: Any) -> AsyncMock:
    lookup = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock()
    if not isinstance(result, Exception):
        lookup.return_value = result
    monkeypatch.setattr("pitwall.runpod_client.pods.get_pods", lookup)
    return lookup


def _never_terminate(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    terminate = AsyncMock(side_effect=AssertionError("an unproven pod must never be terminated"))
    monkeypatch.setattr("pitwall.runpod_client.pods.terminate_pod", terminate)
    monkeypatch.setattr("pitwall.runpod_client.pods.terminate_pod_sync", terminate)
    return terminate


def _pod(pod_id: str, env: dict[str, str] | None) -> dict[str, Any]:
    return {"id": pod_id, "name": _NAME, "desiredStatus": "RUNNING", "env": env}


async def _workload(pg_pool: Any, workload_id: str) -> Any:
    async with pg_pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT state, cost_actual_usd, cost_actual_provenance, idempotency_key"
            " FROM pitwall.workloads WHERE id = $1",
            workload_id,
        )


async def _leases(pg_pool: Any, workload_id: str) -> list[Any]:
    async with pg_pool.acquire() as conn:
        return list(
            await conn.fetch(
                "SELECT id, runpod_pod_id, created_at, expires_at, max_usd_per_hour"
                " FROM pitwall.leases WHERE workload_id = $1",
                workload_id,
            )
        )


async def test_the_pod_carrying_the_attempt_marker_is_leased_and_the_sweep_settles_it(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pitwall.reconciler import _lease_expiry_reconcile
    from tests.reconciler.test_lease_expiry_reconcile import (
        _make_mock_pool,
        _patch_automation_repositories,
        _patch_run_teardown,
        _plain_redis,
        _teardown_result,
    )

    workload_id, started_at, sent_env = await _unknown_outcome_create(pg_pool, "reap-found-key")
    assert sent_env == {CREATE_ATTEMPT_ENV: create_attempt_marker("reap-found-key")}
    # RunPod returns the env the create sent; an unrelated same-name pod sits beside it.
    _fake_pods(monkeypatch, [_pod("pod_unrelated", {}), _pod("pod_found", sent_env)])

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    (lease_row,) = await _leases(pg_pool, workload_id)
    assert lease_row["runpod_pod_id"] == "pod_found"
    assert lease_row["created_at"] == started_at  # the TTL counts from the create attempt
    assert lease_row["expires_at"] == started_at + dt.timedelta(minutes=60)
    assert lease_row["max_usd_per_hour"] == Decimal("0.5")
    assert (await _workload(pg_pool, workload_id))["state"] == "queued"
    assert await recon.reap_orphaned_workloads(pg_pool) == 0
    assert len(await _leases(pg_pool, workload_id)) == 1  # leased once, not on every pass

    # The existing lease sweep settles it at accrued cost (fake teardown and provider).
    lease = await LeaseRepository(pg_pool).get(lease_row["id"])
    assert lease is not None
    sweep_pool = _make_mock_pool()
    teardown = AsyncMock(return_value=_teardown_result(lease))
    _patch_run_teardown(monkeypatch, teardown)
    lease_repo = _patch_automation_repositories(monkeypatch, lease)
    lease_repo.list_active_for_activity_control.return_value = []
    provider_repo = AsyncMock()
    provider_repo.get.return_value = None
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    settled: list[tuple[object, ...]] = []

    class _Workloads:
        async def update_state(self, wid: str, state: str, **kwargs: Any) -> None:
            settled.append((wid, state, kwargs["cost_actual_usd"]))

    monkeypatch.setattr("pitwall.reconciler.WorkloadRepository", lambda _pool: _Workloads())
    sweep_pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": lease.expires_at,
            "auto_teardown_on_expiry": True,
            "state": "creating",
            "capability_name": None,
        }
    ]
    monkeypatch.setattr("pitwall.runpod_client.pods.get_pod_strict", AsyncMock(return_value=None))
    await _lease_expiry_reconcile(
        {
            "db_pool": sweep_pool,
            "redis": _plain_redis(),
            "now": started_at + dt.timedelta(minutes=20),
        }
    )
    assert teardown.await_args.kwargs["terminated_reason"] == "pod_absent"
    assert settled == [(workload_id, "completed", Decimal("0.42"))]


async def test_a_terminated_pod_carrying_the_attempt_marker_is_leased_not_closed_at_zero(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, started_at, sent_env = await _unknown_outcome_create(
        pg_pool, "reap-terminated-key"
    )
    _fake_pods(monkeypatch, [{**_pod("pod_gone", sent_env), "desiredStatus": "TERMINATED"}])
    _never_terminate(monkeypatch)

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    (lease_row,) = await _leases(pg_pool, workload_id)
    assert lease_row["runpod_pod_id"] == "pod_gone"
    assert lease_row["created_at"] == started_at  # the sweep charges from the attempt start
    row = await _workload(pg_pool, workload_id)
    assert row["state"] == "queued"
    assert row["cost_actual_provenance"] != "raw_pod_create_absent"


async def test_an_earlier_attempts_terminated_pod_is_not_matched_on_a_same_key_retry(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = "reap-retry-key"
    service = RunPodControlPlaneService(
        backend=RecordingBackend(),
        audit_pool=pg_pool,
        environ={"RUNPOD_API_KEY": "reap-key"},
        timeout_s=1,
    )
    # An earlier attempt created pod_new; its lease insert failed, the rollback terminated
    # the pod and freed the key, so the caller retried with the same key.
    first = await service.create_pod(
        PodCreateRequest(
            intent="apply",
            idempotency_key=key,
            name=_NAME,
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
            max_cost_per_hour=Decimal("0.50"),
        )
    )
    assert first.resource_id == "pod_new"
    assert await service.release_idempotency_key(key, reason="rollback terminated the pod")
    workload_id, _started, sent_env = await _unknown_outcome_create(pg_pool, key)
    _fake_pods(monkeypatch, [{**_pod("pod_new", sent_env), "desiredStatus": "TERMINATED"}])

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    assert await _leases(pg_pool, workload_id) == []
    row = await _workload(pg_pool, workload_id)
    assert row["state"] == "failed"
    assert row["cost_actual_provenance"] == "raw_pod_create_absent"


async def test_a_pod_absent_after_the_settling_margin_closes_the_workload_at_zero(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, _started, _env = await _unknown_outcome_create(pg_pool, "reap-absent-key")
    _fake_pods(monkeypatch, [])

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    row = await _workload(pg_pool, workload_id)
    assert row["state"] == "failed"
    assert row["cost_actual_usd"] == Decimal("0")
    assert row["cost_actual_provenance"] == "raw_pod_create_absent"
    assert row["idempotency_key"] is None
    assert await _leases(pg_pool, workload_id) == []


async def test_a_pod_absent_inside_the_settling_margin_is_held(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, _started, _env = await _unknown_outcome_create(
        pg_pool, "reap-young-key", attempt_age="2 minutes"
    )
    lookup = _fake_pods(monkeypatch, [])

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    lookup.assert_not_awaited()
    row = await _workload(pg_pool, workload_id)
    assert (row["state"], row["idempotency_key"]) == ("queued", "reap-young-key")


async def test_a_lookup_error_holds_the_workload(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, _started, _env = await _unknown_outcome_create(pg_pool, "reap-outage-key")
    lookup = _fake_pods(monkeypatch, RuntimeError("RunPod unavailable"))

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    lookup.assert_awaited_once()
    row = await _workload(pg_pool, workload_id)
    assert (row["state"], row["idempotency_key"]) == ("queued", "reap-outage-key")
    assert await _leases(pg_pool, workload_id) == []


@pytest.mark.parametrize("env", ["none", "unmarked", "other_attempt"])
async def test_a_same_name_pod_without_the_marker_is_held_and_never_terminated(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch, env: str
) -> None:
    workload_id, _started, _sent = await _unknown_outcome_create(pg_pool, f"reap-collide-{env}")
    pod_env = {
        "none": None,
        "unmarked": {"MODE": "fast"},
        "other_attempt": {CREATE_ATTEMPT_ENV: create_attempt_marker("someone-elses-key")},
    }[env]
    lookup = _fake_pods(monkeypatch, [_pod("pod_unrelated", pod_env)])
    terminate = _never_terminate(monkeypatch)

    assert await recon.reap_orphaned_workloads(pg_pool) == 0
    assert await recon.reap_orphaned_workloads(pg_pool) == 0  # still held on the next pass

    assert lookup.await_count == 2  # looked up and declined, not skipped
    terminate.assert_not_called()
    assert await _leases(pg_pool, workload_id) == []  # no lease, so no TTL teardown either
    row = await _workload(pg_pool, workload_id)
    assert (row["state"], row["idempotency_key"]) == ("queued", f"reap-collide-{env}")


async def test_a_create_still_running_is_not_resolved(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, _started, _env = await _unknown_outcome_create(pg_pool, "reap-inflight-key")
    lookup = _fake_pods(monkeypatch, [])

    async with pg_pool.acquire() as holder:
        await holder.execute(
            "SELECT pg_advisory_lock(hashtextextended($1, 0))",
            journal_lock_name("reap-inflight-key"),
        )
        try:
            assert await recon.reap_orphaned_workloads(pg_pool) == 0
        finally:
            await holder.execute(
                "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                journal_lock_name("reap-inflight-key"),
            )

    lookup.assert_not_awaited()
    assert (await _workload(pg_pool, workload_id))["state"] == "queued"


async def test_an_unresolvable_create_past_the_orphan_threshold_is_charged_its_ceiling(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, _started, _env = await _unknown_outcome_create(
        pg_pool, "reap-old-key", attempt_age="2 hours", workload_age="2 hours"
    )
    lookup = _fake_pods(monkeypatch, RuntimeError("RunPod unavailable"))

    assert await recon.reap_orphaned_workloads(pg_pool) == 1

    lookup.assert_awaited_once()  # resolution was tried first; the charge follows

    row = await _workload(pg_pool, workload_id)
    assert row["state"] == "timed_out"
    assert row["cost_actual_usd"] == Decimal("0.500000")
    assert row["cost_actual_provenance"] == "reaped_unfinished"


async def _completed_create_without_lease(
    pg_pool: Any, key: str, *, completed_age: str = "9 minutes"
) -> tuple[str, dt.datetime, dict[str, str]]:
    """A create that RunPod completed, then the process stopped before its lease insert.

    The journal holds ``started`` then ``completed`` (naming ``pod_new``); the workload is
    open and has no lease, exactly as a crash after the ``completed`` write leaves it.
    """
    workload_id = f"wkl_{key}"
    async with pg_pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pitwall.workloads (id, capability_id, provider_id, type, state,"
            " idempotency_key, submitted_at, cost_estimate_usd, cost_ceiling_usd)"
            " VALUES ($1, 'runpod_direct', 'runpod_direct', 'inference', 'queued', $2,"
            " now() - interval '10 minutes', 0.50, 0.50)",
            workload_id,
            key,
        )
    backend = RecordingBackend()
    sent: list[dict[str, str]] = []
    original = backend.create_pod

    async def create(request: PodCreateRequest) -> dict[str, Any]:
        sent.append(dict(request.env))
        return await original(request)

    backend.create_pod = create  # type: ignore[method-assign]  # reason: capture the env sent
    service = RunPodControlPlaneService(
        backend=backend, audit_pool=pg_pool, environ={"RUNPOD_API_KEY": "reap-key"}, timeout_s=1
    )
    result = await service.create_pod(
        PodCreateRequest(
            intent="apply",
            idempotency_key=key,
            name=_NAME,
            image="example/image:1",
            gpu_type_ids=["NVIDIA L4"],
            ttl_minutes=60,
            max_cost_per_hour=Decimal("0.50"),
        )
    )
    assert result.resource_id == "pod_new"
    # The process stops here: no lease insert. Age the journal rows.
    async with pg_pool.acquire() as conn:
        started_at = await conn.fetchval(
            "UPDATE pitwall.config_audit SET created_at = now() - interval '10 minutes'"
            " WHERE new_value ->> 'idempotency_key' = $1 AND new_value ->> 'state' = 'started'"
            " RETURNING created_at",
            key,
        )
        await conn.execute(
            "UPDATE pitwall.config_audit"
            f" SET created_at = now() - interval '{completed_age}'"
            " WHERE new_value ->> 'idempotency_key' = $1 AND new_value ->> 'state' = 'completed'",
            key,
        )
    assert isinstance(started_at, dt.datetime)
    (sent_env,) = sent
    return workload_id, started_at, sent_env


@pytest.mark.parametrize("listed", ["marked", "unlisted"])
async def test_a_crash_after_completed_leaves_a_pod_the_reaper_leases_by_its_recorded_id(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch, listed: str
) -> None:
    workload_id, started_at, sent_env = await _completed_create_without_lease(
        pg_pool, f"reap-completed-{listed}"
    )
    pods = [_pod("pod_new", sent_env)] if listed == "marked" else []
    lookup = _fake_pods(monkeypatch, pods)
    terminate = _never_terminate(monkeypatch)

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    lookup.assert_awaited_once()
    terminate.assert_not_called()
    (lease_row,) = await _leases(pg_pool, workload_id)
    assert lease_row["runpod_pod_id"] == "pod_new"
    assert lease_row["created_at"] == started_at  # the TTL counts from the create attempt
    assert lease_row["expires_at"] == started_at + dt.timedelta(minutes=60)
    assert (await _workload(pg_pool, workload_id))["state"] == "queued"
    assert await recon.reap_orphaned_workloads(pg_pool) == 0
    assert len(await _leases(pg_pool, workload_id)) == 1


async def test_a_completed_pod_carrying_another_attempts_marker_is_held(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, _started, _sent = await _completed_create_without_lease(
        pg_pool, "reap-completed-other"
    )
    lookup = _fake_pods(
        monkeypatch, [_pod("pod_new", {CREATE_ATTEMPT_ENV: create_attempt_marker("other-key")})]
    )
    terminate = _never_terminate(monkeypatch)

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    lookup.assert_awaited_once()  # considered and declined, not skipped
    terminate.assert_not_called()
    assert await _leases(pg_pool, workload_id) == []
    assert (await _workload(pg_pool, workload_id))["state"] == "queued"


async def test_a_just_completed_create_is_left_to_its_own_lease_insert(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, _started, _sent = await _completed_create_without_lease(
        pg_pool, "reap-completed-young", completed_age="1 minute"
    )
    lookup = _fake_pods(monkeypatch, [])

    assert await recon.reap_orphaned_workloads(pg_pool) == 0

    lookup.assert_not_awaited()
    assert await _leases(pg_pool, workload_id) == []


class _CompletesBeforeTheLock:
    """A pool whose first try-lock is preceded by the create's fresh ``completed`` row.

    It reproduces a long create that finishes between the reaper's first read (which saw
    its old ``started`` row as due) and the reaper's ``pg_try_advisory_lock``.
    """

    def __init__(self, pool: Any, key: str) -> None:
        self._pool = pool
        self._key = key
        self.completed_inserted = False

    def acquire(self) -> Any:
        outer = self

        class _Acquire:
            async def __aenter__(self) -> Any:
                self._ctx = outer._pool.acquire()
                conn = await self._ctx.__aenter__()
                return _Conn(conn, outer)

            async def __aexit__(self, *exc: object) -> Any:
                return await self._ctx.__aexit__(*exc)

        return _Acquire()


class _Conn:
    def __init__(self, conn: Any, owner: _CompletesBeforeTheLock) -> None:
        self._conn = conn
        self._owner = owner

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)

    async def fetchval(self, sql: str, *args: Any) -> Any:
        if "pg_try_advisory_lock" in sql and not self._owner.completed_inserted:
            self._owner.completed_inserted = True
            await self._conn.execute(
                "INSERT INTO pitwall.config_audit"
                " (actor, action, entity_type, entity_id, new_value, change_reason)"
                " SELECT actor, action, entity_type, 'pod_late',"
                "   jsonb_set(jsonb_set(new_value, '{state}', '\"completed\"'),"
                "             '{result}', '{\"resource_id\": \"pod_late\"}'),"
                "   'runpod control-plane pod.create completed'"
                " FROM pitwall.config_audit"
                " WHERE new_value ->> 'idempotency_key' = $1"
                "   AND new_value ->> 'state' = 'started'",
                self._owner._key,
            )
        return await self._conn.fetchval(sql, *args)


async def test_a_create_that_completes_between_the_read_and_the_lock_is_left_alone(
    pg_pool: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    workload_id, _started, _env = await _unknown_outcome_create(pg_pool, "reap-late-complete")
    lookup = _fake_pods(monkeypatch, [_pod("pod_late", _env)])
    racing_pool = _CompletesBeforeTheLock(pg_pool, "reap-late-complete")

    await recon.resolve_ambiguous_raw_pod_creates(racing_pool)  # type: ignore[arg-type]  # reason: proxy pool

    assert racing_pool.completed_inserted
    lookup.assert_not_awaited()  # the 0 s-old completed row failed the re-read's margin
    assert await _leases(pg_pool, workload_id) == []
    assert (await _workload(pg_pool, workload_id))["state"] == "queued"
