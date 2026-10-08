"""Gap-closing tests for reconciler/__init__.py.

Direct tests for the pure helpers and the mock-pool DB helpers that the
existing reconciler async-job tests under-exercise. Imports from
``pitwall.reconciler`` (the package __init__). No real DB/Redis/sleep.
"""

from __future__ import annotations

import datetime as dt
import logging
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import pitwall.reconciler as recon
from pitwall.core.enums import WorkloadState
from tests.conftest import make_asyncpg_pool

TZ_NOW = dt.datetime(2026, 5, 28, 12, 0, 0, tzinfo=dt.UTC)


@pytest.mark.parametrize(
    ("dsn", "expected"),
    [
        ("redis://localhost:6379/0", True),
        ("redis://127.0.0.1:6380/0", True),
        ("", False),
        ("not-a-url", False),
        ("http://localhost", False),
    ],
    ids=["loopback", "ip", "empty", "garbage", "wrong-scheme"],
)
def test_validate_redis_dsn(dsn: str, expected: bool) -> None:
    assert recon.validate_redis_dsn(dsn) is expected


def test_check_redis_config_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)
    rc = recon.check_redis_config()
    assert rc == 1
    assert "REDIS_URL is not set" in capsys.readouterr().err


def test_check_redis_config_invalid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REDIS_URL", "not-a-url")
    rc = recon.check_redis_config()
    assert rc == 1
    assert "not a valid redis" in capsys.readouterr().err


def test_check_redis_config_masks_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://user:hunter2@redis.internal:6379/0")
    rc = recon.check_redis_config()
    out = capsys.readouterr().out
    assert rc == 0
    assert "hunter2" not in out
    assert "redis.internal:6379" in out


def test_check_redis_config_valid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    rc = recon.check_redis_config()
    assert rc == 0
    assert "REDIS_URL is valid" in capsys.readouterr().out


async def test_worker_startup_attaches_db_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = object()
    get_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)
    ctx: dict[str, object] = {}

    await recon.WorkerSettings.on_startup(ctx)

    get_pool.assert_awaited_once_with()
    assert ctx["db_pool"] is pool


async def test_worker_startup_raises_when_db_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_pool = AsyncMock(side_effect=RuntimeError("database unavailable"))
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await recon.WorkerSettings.on_startup({})


async def test_worker_shutdown_closes_db_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    close_pool = AsyncMock()
    monkeypatch.setattr("pitwall.db.close_pool", close_pool)
    ctx: dict[str, object] = {"db_pool": object()}

    await recon.WorkerSettings.on_shutdown(ctx)

    close_pool.assert_awaited_once_with()
    assert "db_pool" not in ctx


def test_worker_settings_register_webhook_job_with_arq() -> None:
    """arq only reads Worker.__init__ parameter names from the settings class;
    the enqueued webhook job must be exposed as ``functions`` or it is
    silently dropped and every enqueue fails with 'function not found'."""
    from arq.worker import get_kwargs

    kwargs = get_kwargs(recon.WorkerSettings)
    assert recon._process_webhook_terminal_status in kwargs["functions"]
    assert kwargs["cron_jobs"]


@pytest.mark.parametrize(
    ("status", "terminal", "state"),
    [
        ("COMPLETED", True, WorkloadState.COMPLETED),
        ("FAILED", True, WorkloadState.FAILED),
        ("CANCELLED", True, WorkloadState.CANCELLED),
        ("TIMED_OUT", True, WorkloadState.TIMED_OUT),
        ("IN_PROGRESS", False, None),
        ("IN_QUEUE", False, None),
        ("UNKNOWN", False, None),
    ],
    ids=["completed", "failed", "cancelled", "timed_out", "in_progress", "in_queue", "unknown"],
)
def test_map_runpod_status(status: str, terminal: bool, state: WorkloadState | None) -> None:
    result = recon.map_runpod_status(status)
    assert result.terminal is terminal
    assert result.state == state
    if terminal:
        assert result.completed_at is not None


def test_map_runpod_status_computes_cost() -> None:
    result = recon.map_runpod_status(
        "COMPLETED",
        cost_per_hr=Decimal("3.60"),
        worker_time_ms=1_000,
        completed_at=TZ_NOW,
    )
    assert result.actual_cost == Decimal("0.001000")
    assert result.completed_at == TZ_NOW


@pytest.mark.parametrize(
    ("cost_per_hr", "worker_time_ms", "expected"),
    [
        (None, 1000, None),
        (Decimal("3.60"), None, None),
        (Decimal("3.60"), 0, None),
        (Decimal("3.60"), 3_600_000, Decimal("3.600000")),
    ],
    ids=["no-cost", "no-time", "zero-time", "one-hour"],
)
def test_compute_actual_cost(
    cost_per_hr: Decimal | None, worker_time_ms: int | None, expected: Decimal | None
) -> None:
    assert recon._compute_actual_cost(cost_per_hr, worker_time_ms) == expected


def test_build_workload_completed_event_minimal() -> None:
    workload = {
        "id": "wkl_1",
        "capability_id": "cap_1",
        "provider_id": "prov_1",
        "state": WorkloadState.COMPLETED,
        "completed_at": TZ_NOW,
        "execution_ms": 1234,
        "output_bytes": 56,
        "cost_actual_usd": Decimal("0.42"),
    }
    event = recon.build_workload_completed_event(workload)
    assert event["event"] == "workload.completed"
    assert event["workload_id"] == "wkl_1"
    assert event["state"] == "completed"
    assert event["completed_at"] == TZ_NOW.isoformat()
    assert event["cost_actual_usd"] == "0.42"
    assert "error" not in event


def test_build_workload_completed_event_with_optionals() -> None:
    workload = {
        "id": "wkl_2",
        "state": WorkloadState.FAILED,
        "completed_at": None,
        "cost_actual_usd": None,
        "error": "boom",
        "result": {"x": 1},
        "fallback_chain": ["prov_a", "prov_b"],
    }
    event = recon.build_workload_completed_event(workload)
    assert event["completed_at"] is None
    assert event["cost_actual_usd"] is None
    assert event["error"] == "boom"
    assert event["result"] == {"x": 1}
    assert event["fallback_chain"] == ["prov_a", "prov_b"]


@pytest.mark.anyio
async def test_fetch_active_workloads_returns_dicts() -> None:
    pool = make_asyncpg_pool(fetch=[{"id": "wkl_1", "runpod_job_id": "job_1"}])
    rows = await recon.fetch_active_workloads(pool)
    assert rows == [{"id": "wkl_1", "runpod_job_id": "job_1"}]


@pytest.mark.anyio
async def test_apply_terminal_state_updated_true() -> None:
    pool = make_asyncpg_pool(fetch=[{"id": "wkl_1"}])
    updated = await recon.apply_terminal_state(
        pool,
        workload_id="wkl_1",
        state=WorkloadState.COMPLETED,
        actual_cost=Decimal("0.10"),
        completed_at=TZ_NOW,
    )
    assert updated is True
    pool.conn.fetch.assert_awaited_once()


@pytest.mark.anyio
async def test_apply_terminal_state_already_terminal_false() -> None:
    pool = make_asyncpg_pool(fetch=[])
    updated = await recon.apply_terminal_state(
        pool,
        workload_id="wkl_1",
        state=WorkloadState.COMPLETED,
        actual_cost=None,
        completed_at=TZ_NOW,
    )
    assert updated is False


@pytest.mark.anyio
async def test_fetch_workload_by_id_none_when_missing() -> None:
    pool = make_asyncpg_pool(fetchrow=None)
    assert await recon.fetch_workload_by_id(pool, "wkl_missing") is None


@pytest.mark.anyio
async def test_apply_terminal_status_and_publish_no_workload_returns_false() -> None:
    pool = make_asyncpg_pool(fetchrow=None)
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=0)
    result = await recon.apply_terminal_status_and_publish(pool, redis, "job_missing", "COMPLETED")
    assert result is False
    redis.publish.assert_not_called()


@pytest.mark.anyio
async def test_apply_terminal_status_and_publish_non_terminal_returns_false() -> None:
    pool = make_asyncpg_pool(fetchrow={"id": "wkl_1"})
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=0)
    result = await recon.apply_terminal_status_and_publish(pool, redis, "job_1", "IN_PROGRESS")
    assert result is False


@pytest.mark.anyio
async def test_cost_reconcile_noop_without_pool() -> None:
    await recon._cost_reconcile({"redis": None})


@pytest.mark.anyio
async def test_cost_reconcile_empty_active_is_noop() -> None:
    pool = make_asyncpg_pool(fetch=[])
    await recon._cost_reconcile({"db_pool": pool, "redis": None})
    pool.conn.fetch.assert_awaited()


@pytest.mark.anyio
async def test_publish_workload_completed_redis_none_returns_zero() -> None:
    result = await recon.publish_workload_completed(None, {"event": "test"})
    assert result == 0


@pytest.mark.anyio
async def test_publish_workload_completed_success() -> None:
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=1)
    event = {"event": "workload.completed", "workload_id": "wkl_1"}
    result = await recon.publish_workload_completed(redis, event)
    assert result == 1


@pytest.mark.anyio
async def test_publish_workload_completed_exception_returns_zero() -> None:
    redis = MagicMock()
    redis.publish = MagicMock(side_effect=RuntimeError("boom"))
    event = {"event": "workload.completed", "workload_id": "wkl_1"}
    result = await recon.publish_workload_completed(redis, event)
    assert result == 0


@pytest.mark.anyio
async def test_apply_terminal_status_and_publish_terminal_publishes() -> None:
    wl_row = {
        "id": "wkl_1",
        "capability_id": "c",
        "provider_id": "p",
        "state": "queued",
        "runpod_job_id": "job_1",
        "completed_at": TZ_NOW,
        "execution_ms": 100,
        "output_bytes": 10,
        "cost_actual_usd": Decimal("0.01"),
        "error": None,
        "result": None,
        "fallback_chain": None,
    }
    pool = make_asyncpg_pool(fetch=[wl_row])
    pool.conn.fetchrow = AsyncMock(side_effect=[wl_row, wl_row])
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=1)
    result = await recon.apply_terminal_status_and_publish(pool, redis, "job_1", "COMPLETED")
    assert result is True


@pytest.mark.anyio
async def test_aggregate_daily_cost_calls_execute() -> None:
    pool = make_asyncpg_pool(execute="INSERT 0 1")
    await recon.aggregate_daily_cost(pool)
    pool.conn.execute.assert_awaited()


@pytest.mark.anyio
async def test_fetch_providers_for_health_probe() -> None:
    pool = make_asyncpg_pool(
        fetch=[
            {
                "id": "p1",
                "name": "prov",
                "provider_type": "serverless_lb",
                "runpod_endpoint_id": "ep1",
                "health_status": "healthy",
                "consecutive_failures": 0,
                "cooldown_trips": 0,
                "cooldown_until": None,
            }
        ]
    )
    rows = await recon.fetch_providers_for_health_probe(pool)
    assert len(rows) == 1
    assert rows[0]["id"] == "p1"
    pool.conn.fetch.assert_awaited_once_with(
        recon._HEALTH_PROBE_PROVIDERS_SQL,
        ["serverless_lb", "public_endpoint"],
    )


@pytest.mark.anyio
async def test_fetch_lb_providers_for_hibernate_sweep() -> None:
    pool = make_asyncpg_pool(
        fetch=[
            {
                "id": "p1",
                "name": "prov",
                "provider_type": "serverless_lb",
                "runpod_endpoint_id": "ep1",
                "config": {},
            }
        ]
    )
    rows = await recon.fetch_lb_providers_for_hibernate_sweep(pool)
    assert len(rows) == 1


@pytest.mark.anyio
async def test_update_provider_health() -> None:
    pool = make_asyncpg_pool(execute="UPDATE 1")
    await recon.update_provider_health(
        pool,
        provider_id="prov_1",
        health_status="healthy",
        consecutive_failures=0,
        cooldown_trips=0,
        cooldown_until=None,
    )
    pool.conn.execute.assert_awaited_once_with(
        recon._UPDATE_PROVIDER_HEALTH_SQL,
        "prov_1",
        "healthy",
        0,
        0,
        None,
        None,
        None,
    )


@pytest.mark.anyio
async def test_update_provider_health_reports_a_stale_compare_and_set() -> None:
    pool = make_asyncpg_pool(execute="UPDATE 0")
    written = await recon.update_provider_health(
        pool,
        provider_id="prov_1",
        health_status="healthy",
        consecutive_failures=0,
        cooldown_trips=0,
        cooldown_until=None,
        expected_updated_at=TZ_NOW,
    )
    assert written is False
    assert pool.conn.execute.await_args.args[-1] == TZ_NOW


@pytest.mark.anyio
async def test_cost_reconcile_with_terminal_workload() -> None:
    pool = make_asyncpg_pool(fetch=[{"id": "wkl_1", "runpod_job_id": "job_1"}])
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=0)
    await recon._cost_reconcile({"db_pool": pool, "redis": redis})
    pool.conn.fetch.assert_awaited()


@pytest.mark.anyio
async def test_process_webhook_terminal_status() -> None:
    pool = make_asyncpg_pool(fetchrow=None)
    result = await recon._process_webhook_terminal_status(
        {"db_pool": pool, "redis": None}, "job_1", "COMPLETED"
    )
    assert result is None


@pytest.mark.anyio
async def test_process_webhook_terminal_status_no_pool() -> None:
    result = await recon._process_webhook_terminal_status({"redis": None}, "job_1", "COMPLETED")
    assert result is None


@pytest.mark.anyio
async def test_dispatch_workload_completion_webhooks_records_terminal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64
    import json
    from types import SimpleNamespace

    import pitwall.db.repository as repository
    import pitwall.webhook_dispatcher as webhook_dispatcher

    monkeypatch.setenv(
        "PITWALL_WEBHOOK_ENCRYPTION_KEYS",
        json.dumps({"v1": base64.urlsafe_b64encode(bytes(range(32))).decode()}),
    )
    monkeypatch.setenv("PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY", "v1")
    subscription = SimpleNamespace(
        id="7",
        webhook_url="https://hooks.example.test/events",
        hmac_secret="signing-secret",
    )
    monkeypatch.setattr(
        repository.WebhookSubscriptionRepository,
        "list_for_dispatch",
        AsyncMock(return_value=[subscription]),
    )
    insert_failure = AsyncMock()
    monkeypatch.setattr(repository.WebhookDeliveryFailureRepository, "insert", insert_failure)
    monkeypatch.setattr(
        webhook_dispatcher,
        "dispatch_completion",
        AsyncMock(
            return_value={
                "7": {
                    "success": False,
                    "attempt": 4,
                    "status_code": 503,
                    "error_message": "Retryable HTTP status: 503",
                    "delivery_id": "delivery-7",
                    "state": "terminal_failure",
                }
            }
        ),
    )

    results = await recon.dispatch_workload_completion_webhooks(
        make_asyncpg_pool(),
        {"id": "wkl_7", "capability_id": "cap_7", "state": "completed"},
        {"event": "workload.completed", "workload_id": "wkl_7"},
    )

    assert results["7"]["state"] == "terminal_failure"
    insert_failure.assert_awaited_once()
    stored_payload = insert_failure.await_args.args[3]
    assert stored_payload == {
        "event": "workload.completed",
        "workload_id": "wkl_7",
        "delivery_id": "delivery-7",
        "state": "completed",
        "consumer": "cap_7",
        "data": {"event": "workload.completed", "workload_id": "wkl_7"},
    }
    assert insert_failure.await_args.kwargs["next_retry_at"] is None


@pytest.mark.anyio
async def test_poll_and_reconcile_no_api_key_returns_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "")
    pool = make_asyncpg_pool(fetch=[])
    await recon._poll_and_reconcile({"db_pool": pool, "redis": None})
    pool.conn.fetch.assert_not_called()


@pytest.mark.anyio
async def test_poll_and_reconcile_missing_endpoint_id_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    workload_row = {
        "id": "wkl_1",
        "runpod_job_id": "job_1",
        "provider_id": "prov_1",
        "runpod_endpoint_id": None,
        "provider_type": "serverless_queue",
    }
    pool = make_asyncpg_pool(fetch=[workload_row])
    redis = MagicMock()
    await recon._poll_and_reconcile({"db_pool": pool, "redis": redis})
    pool.conn.fetch.assert_awaited()


@pytest.mark.anyio
async def test_idempotency_gc_no_pool() -> None:
    await recon._idempotency_gc({"redis": None})


@pytest.mark.anyio
async def test_idempotency_gc_with_pool() -> None:
    pool = make_asyncpg_pool(execute="DELETE 0")
    await recon._idempotency_gc({"db_pool": pool, "redis": None})
    pool.conn.execute.assert_awaited()


@pytest.mark.anyio
async def test_health_probe_no_api_key_returns_early(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "")
    pool = make_asyncpg_pool(fetch=[])
    await recon._health_probe({"db_pool": pool, "redis": None})
    pool.conn.fetch.assert_awaited_once_with(
        recon._HEALTH_PROBE_PROVIDERS_SQL,
        ["serverless_lb", "public_endpoint"],
    )
    pool.conn.execute.assert_not_called()


@pytest.mark.anyio
async def test_health_probe_no_pool_returns_early() -> None:
    await recon._health_probe({"redis": None})


@pytest.mark.anyio
async def test_health_probe_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    prov_row = {
        "id": "prov_1",
        "name": "lb1",
        "provider_type": "serverless_lb",
        "runpod_endpoint_id": "ep_1",
        "health_status": "healthy",
        "consecutive_failures": 0,
        "cooldown_trips": 0,
        "cooldown_until": None,
        "updated_at": TZ_NOW,
    }
    pool = make_asyncpg_pool(fetch=[prov_row])
    pool.conn.execute = AsyncMock(return_value="UPDATE 1")
    redis = MagicMock()

    class MockProbeResult:
        healthy = True

    class MockLBClient:
        def __init__(self, api_key):
            pass

        async def probe(self, endpoint_id):
            return MockProbeResult()

    with (
        patch("pitwall.runpod_client.lb.LBClient", MockLBClient),
        patch("pitwall.reconciler.is_in_cooldown", return_value=False),
        patch("pitwall.reconciler.apply_probe_result") as mock_apply,
    ):
        mock_apply.return_value = type(
            "obj",
            (object,),
            {
                "health_status": "healthy",
                "consecutive_failures": 0,
                "cooldown_trips": 0,
                "cooldown_until": None,
            },
        )()
        await recon._health_probe({"db_pool": pool, "redis": redis})
        pool.conn.execute.assert_awaited()


@pytest.mark.anyio
async def test_health_probe_in_cooldown_skips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    prov_row = {
        "id": "prov_1",
        "name": "lb1",
        "provider_type": "serverless_lb",
        "runpod_endpoint_id": "ep_1",
        "health_status": "healthy",
        "consecutive_failures": 0,
        "cooldown_trips": 0,
        "cooldown_until": None,
    }
    pool = make_asyncpg_pool(fetch=[prov_row])
    redis = MagicMock()

    with patch("pitwall.reconciler.is_in_cooldown", return_value=True):
        await recon._health_probe({"db_pool": pool, "redis": redis})
        pool.conn.execute.assert_not_called()


@pytest.mark.anyio
async def test_poll_and_reconcile_with_queue_provider_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    workload_row = {
        "id": "wkl_1",
        "runpod_job_id": "job_1",
        "provider_id": "prov_1",
        "runpod_endpoint_id": "ep_1",
        "provider_type": "serverless_queue",
    }
    pool = make_asyncpg_pool(fetch=[workload_row])
    redis = MagicMock()
    pool.conn.fetch = AsyncMock(return_value=[])

    class MockQueueJob:
        status = "COMPLETED"

    class MockQueueClient:
        def __init__(self, api_key):
            pass

        async def status(self, endpoint_id, job_id):
            return MockQueueJob()

    with patch("pitwall.runpod_client.queue.QueueClient", MockQueueClient):
        await recon._poll_and_reconcile({"db_pool": pool, "redis": redis})
        pool.conn.fetch.assert_awaited()


@pytest.mark.anyio
async def test_lease_expiry_reconcile_no_pool_returns_early() -> None:
    await recon._lease_expiry_reconcile({"redis": None})


@pytest.mark.anyio
async def test_lease_expiry_reconcile_with_leases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    lease_row = {
        "id": "lease_1",
        "provider_id": "prov_1",
        "runpod_pod_id": "pod_1",
        "expires_at": dt.datetime.now(dt.UTC) + dt.timedelta(minutes=10),
        "auto_teardown_on_expiry": True,
        "state": "active",
    }
    pool = make_asyncpg_pool(
        fetch_side_effect=lambda sql, *_args: [] if "state = ANY" in sql else [lease_row]
    )
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=1)
    await recon._lease_expiry_reconcile({"db_pool": pool, "redis": redis})
    pool.conn.fetch.assert_awaited()


@pytest.mark.anyio
async def test_lease_expiry_reconcile_expired_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    lease_row = {
        "id": "lease_1",
        "provider_id": "prov_1",
        "runpod_pod_id": "pod_1",
        "expires_at": dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
        "auto_teardown_on_expiry": True,
        "state": "active",
    }
    pool = make_asyncpg_pool(
        fetch_side_effect=lambda sql, *_args: [lease_row] if "interval '60 minutes'" in sql else []
    )
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=1)
    with patch("pitwall.api.leases.teardown.run_teardown", new_callable=AsyncMock) as mock_teardown:
        await recon._lease_expiry_reconcile({"db_pool": pool, "redis": redis})
        mock_teardown.assert_awaited_once()
    pool.conn.fetch.assert_awaited()


@pytest.mark.anyio
async def test_lb_endpoint_hibernate_sweep_no_providers_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    pool = make_asyncpg_pool(fetch=[])
    redis = MagicMock()
    await recon._lb_endpoint_hibernate_sweep({"db_pool": pool, "redis": redis})
    pool.conn.fetch.assert_awaited()


@pytest.mark.anyio
async def test_lb_endpoint_hibernate_sweep_no_api_key_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "")
    pool = make_asyncpg_pool(
        fetch=[
            {
                "id": "p1",
                "name": "prov",
                "provider_type": "serverless_lb",
                "runpod_endpoint_id": "ep1",
                "config": {},
            }
        ]
    )
    redis = MagicMock()
    await recon._lb_endpoint_hibernate_sweep({"db_pool": pool, "redis": redis})
    pool.conn.fetch.assert_not_called()


@pytest.mark.anyio
async def test_lb_endpoint_hibernate_sweep_fetch_raises_returns_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    pool = make_asyncpg_pool(fetch=[])
    pool.conn.fetch = AsyncMock(side_effect=RuntimeError("boom"))
    redis = MagicMock()
    await recon._lb_endpoint_hibernate_sweep({"db_pool": pool, "redis": redis})
    pool.conn.fetch.assert_awaited()


@pytest.mark.anyio
async def test_publish_lease_warning_redis_none() -> None:
    result = await recon._publish_lease_warning(
        None,
        lease_id="l1",
        provider_id="p1",
        runpod_pod_id="pod_1",
        minutes_until_expiry=10,
        warning_threshold=15,
    )
    assert result is None


@pytest.mark.anyio
async def test_publish_lease_warning_success() -> None:
    redis = MagicMock()
    redis.publish = AsyncMock(return_value=1)
    await recon._publish_lease_warning(
        redis,
        lease_id="l1",
        provider_id="p1",
        runpod_pod_id="pod_1",
        minutes_until_expiry=10,
        warning_threshold=15,
    )
    redis.publish.assert_awaited()


@pytest.mark.anyio
async def test_publish_lease_warning_exception_suppressed() -> None:
    redis = MagicMock()
    redis.publish = MagicMock(side_effect=RuntimeError("boom"))
    result = await recon._publish_lease_warning(
        redis,
        lease_id="l1",
        provider_id="p1",
        runpod_pod_id="pod_1",
        minutes_until_expiry=10,
        warning_threshold=15,
    )
    assert result is None


@pytest.mark.anyio
async def test_backup_drill_no_pool() -> None:
    await recon._backup_drill({"redis": None})


@pytest.mark.anyio
async def test_archive_old_workloads_no_pool() -> None:
    await recon._archive_old_workloads({"redis": None})


@pytest.mark.anyio
async def test_archive_old_workloads_no_archive_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PITWALL_ARCHIVE_DIR", raising=False)
    pool = make_asyncpg_pool(fetch=[])
    await recon._archive_old_workloads({"db_pool": pool, "redis": None})
    pool.conn.fetch.assert_not_called()


@pytest.mark.anyio
async def test_rollup_job_no_pool() -> None:
    await recon._rollup_job({"redis": None})


@pytest.mark.anyio
async def test_rollup_job_no_redis() -> None:
    pool = make_asyncpg_pool(fetch=[])
    await recon._rollup_job({"db_pool": pool, "redis": None})
    pool.conn.fetch.assert_not_called()


@pytest.mark.anyio
async def test_rollup_job_checks_forecast_alert_after_successful_rollup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = make_asyncpg_pool()
    redis = object()
    forecast = object()
    budget_alert = AsyncMock()
    read_forecast = AsyncMock(return_value=forecast)
    forecast_alert = AsyncMock()

    async def run_rollup_with_hook(
        received_pool: object,
        *,
        after_rollup: object,
    ) -> None:
        assert received_pool is pool
        assert callable(after_rollup)
        await after_rollup()

    monkeypatch.setattr(recon, "run_rollup", run_rollup_with_hook)
    monkeypatch.setattr("pitwall.cost.alerts.check_and_send_budget_alert", budget_alert)
    monkeypatch.setattr("pitwall.finops.burn_rate.read_configured_burn_rate", read_forecast)
    monkeypatch.setattr(
        "pitwall.finops.burn_rate_alerts.check_and_send_forecast_alert", forecast_alert
    )

    await recon._rollup_job({"db_pool": pool, "redis": redis})

    budget_alert.assert_awaited_once_with(pool, redis)
    read_forecast.assert_awaited_once()
    read_call = read_forecast.await_args
    assert read_call is not None
    assert read_call.args == (pool,)
    assert read_call.kwargs["now"].tzinfo is dt.UTC
    forecast_alert.assert_awaited_once_with(forecast, redis)


@pytest.mark.anyio
async def test_rollup_job_logs_alert_failures_redacted_and_keeps_going(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failing alert is logged (secrets redacted) instead of silently swallowed (finding #6)."""
    pool = make_asyncpg_pool()
    forecast_alert = AsyncMock()

    async def run_rollup_with_hook(received_pool: object, *, after_rollup: Any) -> None:
        await after_rollup()

    monkeypatch.setattr(recon, "run_rollup", run_rollup_with_hook)
    monkeypatch.setattr(
        "pitwall.cost.alerts.check_and_send_budget_alert",
        AsyncMock(side_effect=RuntimeError("smtp rejected api_key=sk-live-abcdef0123456789")),
    )
    monkeypatch.setattr(
        "pitwall.finops.burn_rate.read_configured_burn_rate", AsyncMock(return_value=object())
    )
    monkeypatch.setattr(
        "pitwall.finops.burn_rate_alerts.check_and_send_forecast_alert", forecast_alert
    )

    with caplog.at_level(logging.WARNING, logger=recon.log.name):
        await recon._rollup_job({"db_pool": pool, "redis": object()})

    forecast_alert.assert_awaited_once()
    messages = [r.getMessage() for r in caplog.records if "budget alert" in r.getMessage()]
    assert messages, caplog.text
    assert "sk-live-abcdef0123456789" not in caplog.text


def test_build_workload_completed_event_state_is_enum_value() -> None:
    workload = {
        "id": "wkl_1",
        "capability_id": "cap_1",
        "provider_id": "prov_1",
        "state": WorkloadState.COMPLETED,
        "completed_at": TZ_NOW,
        "execution_ms": 100,
        "output_bytes": 10,
        "cost_actual_usd": Decimal("0.01"),
    }
    event = recon.build_workload_completed_event(workload)
    assert event["state"] == "completed"


def test_orphaned_workload_reaper_runs_every_five_minutes() -> None:
    jobs = {job.name: job for job in recon.WorkerSettings.cron_jobs}
    reaper = next(job for name, job in jobs.items() if "reap_orphaned_workloads" in name)
    assert reaper.minute == set(range(1, 60, 5))


@pytest.mark.anyio
async def test_orphaned_workload_reaper_without_pool_is_a_no_op() -> None:
    await recon._reap_orphaned_workloads({})


_ATTEMPT_START = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.UTC)
_MARKER = "a" * 32


def _ambiguous_create() -> Any:
    from pitwall.reconciler import _AmbiguousPodCreate

    return _AmbiguousPodCreate(
        workload_id="wkl_amb",
        idempotency_key="amb-key-0001",
        attempt_marker=_MARKER,
        pod_name="amb-pod",
        ttl_minutes=60,
        max_cost_per_hour=None,
        attempt_started_at=_ATTEMPT_START,
    )


def _named_pod(pod_id: str, env: object = None, **fields: object) -> dict[str, object]:
    return {"id": pod_id, "name": "amb-pod", "desiredStatus": "RUNNING", "env": env, **fields}


_OURS = {"PITWALL_CREATE_ATTEMPT": _MARKER, "MODE": "fast"}


@pytest.mark.parametrize(
    ("pods", "expected"),
    [
        ([], ("absent", "")),
        # A marked pod existed and billed, even if it is already terminated: lease it so the
        # sweep settles it at accrued cost, never close the workload at $0.
        ([_named_pod("pod_x", _OURS, desiredStatus="TERMINATED")], ("adopt", "pod_x")),
        ([_named_pod("pod_x", {"MODE": "fast"}, desiredStatus="TERMINATED")], ("absent", "")),
        ([{"id": "pod_other", "name": "other", "desiredStatus": "RUNNING"}], ("absent", "")),
        ([_named_pod("pod_ours", _OURS)], ("adopt", "pod_ours")),
        ([_named_pod("pod_ours", [f"PITWALL_CREATE_ATTEMPT={_MARKER}"])], ("adopt", "pod_ours")),
        (
            [_named_pod("pod_ours", [{"key": "PITWALL_CREATE_ATTEMPT", "value": _MARKER}])],
            ("adopt", "pod_ours"),
        ),
        # The marker, not the name, is the proof: a renamed marked pod is still ours.
        ([_named_pod("pod_ours", _OURS, name="renamed")], ("adopt", "pod_ours")),
        (
            [_named_pod("pod_unmarked", {"MODE": "fast"})],
            ("hold", "pod(s) pod_unmarked carry the name but not the attempt marker"),
        ),
        (
            [_named_pod("pod_other", {"PITWALL_CREATE_ATTEMPT": "b" * 32})],
            ("hold", "pod(s) pod_other carry the name but not the attempt marker"),
        ),
        (
            [_named_pod("pod_no_env")],
            ("hold", "pod(s) pod_no_env carry the name but not the attempt marker"),
        ),
        (
            [_named_pod("pod_a", _OURS), _named_pod("pod_b", _OURS)],
            ("hold", "several pods carry the attempt marker"),
        ),
        (
            [_named_pod("pod_a", _OURS), _named_pod("pod_b", _OURS, desiredStatus="TERMINATED")],
            ("hold", "several pods carry the attempt marker"),
        ),
    ],
)
def test_an_unknown_outcome_pod_is_adopted_only_with_its_attempt_marker(
    pods: list[dict[str, object]], expected: tuple[str, str]
) -> None:
    from pitwall.reconciler import _ambiguous_pod_decision

    assert _ambiguous_pod_decision(pods, _ambiguous_create()) == expected


@pytest.mark.parametrize(
    ("pods", "expected"),
    [
        # The marker is per key: a pod an earlier attempt recorded, then a rollback terminated,
        # is that attempt's, so a retry whose own pod never appeared is absent, not adopted.
        ([_named_pod("pod_old", _OURS, desiredStatus="TERMINATED")], ("absent", "")),
        (
            [
                _named_pod("pod_old", _OURS, desiredStatus="TERMINATED"),
                _named_pod("pod_new", _OURS),
            ],
            ("adopt", "pod_new"),
        ),
        (
            [
                _named_pod("pod_old", _OURS, desiredStatus="TERMINATED"),
                _named_pod("pod_new", _OURS, desiredStatus="TERMINATED"),
            ],
            ("adopt", "pod_new"),
        ),
    ],
)
def test_an_earlier_attempts_terminated_pod_is_not_this_attempts(
    pods: list[dict[str, object]], expected: tuple[str, str]
) -> None:
    import dataclasses

    from pitwall.reconciler import _ambiguous_pod_decision

    retry = dataclasses.replace(_ambiguous_create(), earlier_pod_ids=frozenset({"pod_old"}))
    assert _ambiguous_pod_decision(pods, retry) == expected


def test_a_journal_row_carries_the_pod_ids_earlier_attempts_completed() -> None:
    from pitwall.reconciler import _ambiguous_pod_create

    row = {
        "id": "wkl_amb",
        "new_value": {"idempotency_key": "amb-key-0001", "recovery": _RECOVERY},
        "attempt_started_at": _ATTEMPT_START,
        "completed_pod_ids": ["pod_old", None, ""],
    }
    create = _ambiguous_pod_create(row)
    assert create is not None
    assert create.earlier_pod_ids == frozenset({"pod_old"})


_RECOVERY = {"attempt_marker": _MARKER, "name": "amb-pod", "ttl_minutes": 60}


@pytest.mark.parametrize(
    "recovery",
    [
        None,
        {**_RECOVERY, "name": ""},
        {**_RECOVERY, "attempt_marker": None},
        {**_RECOVERY, "ttl_minutes": 0},
        {**_RECOVERY, "ttl_minutes": True},
        {**_RECOVERY, "max_cost_per_hour": "not-a-number"},
        {**_RECOVERY, "max_cost_per_hour": 1.5},
    ],
)
def test_an_unusable_journal_recovery_record_is_not_resolved(recovery: object) -> None:
    from pitwall.reconciler import _ambiguous_pod_create

    row = {
        "id": "wkl_amb",
        "new_value": {"idempotency_key": "amb-key-0001", "recovery": recovery},
        "attempt_started_at": _ATTEMPT_START,
    }
    assert _ambiguous_pod_create(row) is None


def test_a_journal_recovery_record_carries_the_marker_and_lease_terms() -> None:
    from pitwall.reconciler import _ambiguous_pod_create

    row = {
        "id": "wkl_amb",
        "new_value": {
            "idempotency_key": "amb-key-0001",
            "recovery": {**_RECOVERY, "max_cost_per_hour": "0.49"},
        },
        "attempt_started_at": _ATTEMPT_START,
    }
    create = _ambiguous_pod_create(row)
    assert create is not None
    assert (create.attempt_marker, create.pod_name, create.ttl_minutes) == (_MARKER, "amb-pod", 60)
    assert create.max_cost_per_hour == Decimal("0.49")


@pytest.mark.parametrize(
    ("pods", "expected"),
    [
        ([_named_pod("pod_done", _OURS)], ("adopt", "pod_done")),
        ([_named_pod("pod_done", {"MODE": "fast"})], ("adopt", "pod_done")),
        ([], ("adopt", "pod_done")),  # the lease sweep confirms absence by id
        ([_named_pod("pod_done", _OURS, desiredStatus="TERMINATED")], ("adopt", "pod_done")),
        (
            [_named_pod("pod_done", {"PITWALL_CREATE_ATTEMPT": "b" * 32})],
            ("hold", "pod pod_done carries another attempt's marker"),
        ),
    ],
)
def test_a_completed_create_is_adopted_by_its_recorded_pod_id(
    pods: list[dict[str, object]], expected: tuple[str, str]
) -> None:
    import dataclasses

    from pitwall.reconciler import _ambiguous_pod_decision

    completed = dataclasses.replace(_ambiguous_create(), pod_id="pod_done")
    assert _ambiguous_pod_decision(pods, completed) == expected


def test_a_completed_record_without_a_pod_id_is_not_resolved() -> None:
    from pitwall.reconciler import _ambiguous_pod_create

    row = {
        "id": "wkl_amb",
        "new_value": {
            "idempotency_key": "amb-key-0001",
            "state": "completed",
            "result": {"resource_id": None},
            "recovery": _RECOVERY,
        },
        "attempt_started_at": _ATTEMPT_START,
    }
    assert _ambiguous_pod_create(row) is None
    row["new_value"]["result"] = {"resource_id": "pod_done"}  # type: ignore[index]  # reason: test row
    create = _ambiguous_pod_create(row)
    assert create is not None and create.pod_id == "pod_done"


@pytest.mark.parametrize(
    ("cap", "expected"),
    [
        (None, None),  # uncapped: the cap stays null; settlement reads RunPod's price
        (Decimal("0.6"), Decimal("0.6000")),
    ],
)
async def test_an_adopted_pod_keeps_only_the_callers_cap_on_its_lease(
    monkeypatch: pytest.MonkeyPatch, cap: Decimal | None, expected: Decimal | None
) -> None:
    import dataclasses

    from pitwall.api.leases.launch import raw_pod_lease
    from pitwall.reconciler import _settle_ambiguous_pod_create

    created: list[Any] = []

    class _Leases:
        def __init__(self, pool: object) -> None:
            pass

        async def create(self, lease: Any) -> Any:
            created.append(lease)
            return lease

    monkeypatch.setattr("pitwall.reconciler.LeaseRepository", _Leases)
    completed = dataclasses.replace(_ambiguous_create(), pod_id="pod_done", max_cost_per_hour=cap)
    pods = [_named_pod("pod_done", _OURS, costPerHr="0.44")]

    await _settle_ambiguous_pod_create(MagicMock(), completed, pods, _ATTEMPT_START, raw_pod_lease)

    (lease,) = created
    assert lease.max_usd_per_hour == expected  # RunPod's 0.44 is never shown as a cap


def test_a_raw_pod_lease_without_any_rate_settles_at_the_reservation_rate() -> None:
    from pitwall.api.leases.launch import estimate_raw_pod_lease_cost, raw_pod_lease
    from pitwall.api.leases.teardown import close_lease_cost

    lease = raw_pod_lease(
        pod_id="pod_uncapped",
        workload_id="wkl_uncapped",
        ttl_minutes=480,
        max_cost_per_hour=None,
        created_at=_ATTEMPT_START,
    )
    cost = close_lease_cost(
        lease, provider=None, terminated_at=_ATTEMPT_START + dt.timedelta(minutes=30)
    )
    # Admission reserved estimate_raw_pod_lease_cost(480, None) for this pod: the same rate.
    assert estimate_raw_pod_lease_cost(60, None) == Decimal("0.500000")
    assert cost == Decimal("0.250000")


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        ("0.44", Decimal("0.4400")),
        ("0.00004", None),  # rounds to $0 at 4 dp
        ("1e9", None),  # absurd
        ("-1", None),
        ("NaN", None),
        ("not-a-price", None),
        (None, None),
    ],
)
def test_only_a_usable_observed_price_is_used(price: object, expected: Decimal | None) -> None:
    from pitwall.api.leases.teardown import representable_usd_per_hour

    assert representable_usd_per_hour(price) == expected


@pytest.mark.parametrize(
    ("observed", "expected_cost"),
    [
        (Decimal("0.44"), Decimal("0.220000")),
        (Decimal("0.00004"), Decimal("0.250000")),  # unusable: the reservation rate
        (Decimal("1E+9"), Decimal("0.250000")),
        (None, Decimal("0.250000")),
    ],
)
def test_an_uncapped_raw_pod_lease_settles_at_a_usable_observed_price(
    observed: Decimal | None, expected_cost: Decimal
) -> None:
    from pitwall.api.leases.launch import raw_pod_lease
    from pitwall.api.leases.teardown import close_lease_cost

    lease = raw_pod_lease(
        pod_id="pod_uncapped",
        workload_id="wkl_uncapped",
        ttl_minutes=480,
        max_cost_per_hour=None,
        created_at=_ATTEMPT_START,
    )
    assert lease.max_usd_per_hour is None
    cost = close_lease_cost(
        lease,
        provider=None,
        terminated_at=_ATTEMPT_START + dt.timedelta(minutes=30),
        raw_pod_usd_per_hour=observed,
    )
    assert cost == expected_cost
