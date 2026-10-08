"""Tests for lease_expiry_reconcile — 60-second lease expiry reconciliation."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pitwall.api.admin.kill_switch import KillSwitchEngaged
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderType,
)
from pitwall.core.models import Capability, Lease, Provider
from pitwall.cost import BudgetRejected
from pitwall.models.prices import GpuPriceSnapshot
from pitwall.reconciler import _lease_expiry_reconcile
from pitwall.runpod_client.graphql import RunpodGpuType

pytestmark = pytest.mark.anyio


def _provider_repo(provider: Provider | None) -> AsyncMock:
    """A provider repository whose batched read mirrors its single-provider read."""
    repo = AsyncMock()
    repo.get.return_value = provider
    repo.get_many.side_effect = lambda ids: {} if provider is None else dict.fromkeys(ids, provider)
    return repo


def _with_pipeline(redis: MagicMock) -> None:
    """Give a Redis mock the pipelined read the reconciler uses, backed by ``redis.get``."""

    class _Pipe:
        def __init__(self) -> None:
            self.keys: list[str] = []

        async def __aenter__(self) -> _Pipe:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        def get(self, key: str) -> _Pipe:
            self.keys.append(key)
            return self

        async def execute(self) -> list[object]:
            return [await redis.get(key) for key in self.keys]

    redis.pipeline = MagicMock(side_effect=lambda **_kwargs: _Pipe())


def _plain_redis() -> MagicMock:
    redis = MagicMock()
    _with_pipeline(redis)
    return redis


def _make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    return conn


def _make_mock_pool() -> MagicMock:
    pool = MagicMock()
    conn = _make_mock_conn()
    pool.conn = conn
    acq = MagicMock()
    acq.__aenter__ = AsyncMock(return_value=conn)
    acq.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acq)
    return pool


def _patch_run_teardown(monkeypatch: pytest.MonkeyPatch, replacement: object) -> None:
    import pitwall.api.leases.teardown as teardown_module

    monkeypatch.setattr(teardown_module, "run_teardown", replacement)


async def test_lease_expiry_skips_when_no_pool() -> None:
    """No action is taken if db_pool is not in ctx."""
    ctx: dict = {}
    await _lease_expiry_reconcile(ctx)
    await _lease_expiry_reconcile(ctx)
    assert True


async def test_invalid_warning_minutes_stop_the_job_instead_of_defaulting() -> None:
    """A bad PITWALL_LEASE_ADVANCE_WARNING_MIN never silently becomes {15, 5}."""
    pool = _make_mock_pool()
    ctx: dict = {"db_pool": pool, "redis": None}
    with (
        patch.dict("os.environ", {"PITWALL_LEASE_ADVANCE_WARNING_MIN": "soon"}),
        pytest.raises(ValueError, match="PITWALL_LEASE_ADVANCE_WARNING_MIN"),
    ):
        await _lease_expiry_reconcile(ctx)


async def test_lease_expiry_fetches_active_leases() -> None:
    """The reconciler queries for active leases approaching expiry."""
    pool = _make_mock_pool()
    ctx: dict = {"db_pool": pool, "redis": None}
    await _lease_expiry_reconcile(ctx)
    # stuck-teardown retry, raw-pod expiry, and the expiry window queries
    assert pool.acquire.return_value.__aenter__.return_value.fetch.await_count == 3


async def test_reconciler_writes_newest_redis_traffic_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.UTC)
    lease = _automation_lease(
        id="lease-write-through",
        expires_at=now + dt.timedelta(hours=6),
        last_traffic_at=now - dt.timedelta(minutes=5),
        ready_at=now - dt.timedelta(minutes=10),
    )
    repo = AsyncMock()
    repo.list_active_for_activity_control.return_value = [lease]
    monkeypatch.setattr("pitwall.reconciler.LeaseRepository", lambda _pool: repo)
    provider_repo = _provider_repo(_automation_provider())
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    monkeypatch.setattr(
        "pitwall.reconciler.read_lease_traffic",
        AsyncMock(return_value=MagicMock(available=True, seen_at=now)),
    )
    pool = _make_mock_pool()

    await _lease_expiry_reconcile({"db_pool": pool, "redis": object(), "now": now})

    repo.record_traffic.assert_awaited_once_with("lease-write-through", seen_at=now)


async def test_long_ttl_idle_lease_stops_on_next_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease(
        expires_at=_FROZEN + dt.timedelta(hours=6),
        last_traffic_at=None,
        ready_at=_FROZEN - dt.timedelta(minutes=21),
    )
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    teardown = AsyncMock()
    _patch_run_teardown(monkeypatch, teardown)
    pool = _make_mock_pool()
    redis = MagicMock()
    _with_pipeline(redis)
    redis.get = AsyncMock(return_value=None)

    await _lease_expiry_reconcile({"db_pool": pool, "redis": redis, "now": _FROZEN})

    teardown.assert_awaited_once()
    assert teardown.await_args.kwargs["reason"] == "idle"


async def test_max_lifetime_stops_with_expiry_hours_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease(
        created_at=_FROZEN - dt.timedelta(minutes=1440),
        expires_at=_FROZEN + dt.timedelta(hours=6),
    )
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    teardown = AsyncMock()
    _patch_run_teardown(monkeypatch, teardown)
    pool = _make_mock_pool()

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": _FROZEN})

    teardown.assert_awaited_once()
    assert teardown.await_args.kwargs["reason"] == "max_lifetime"


async def test_redis_failure_treats_old_ready_lease_as_busy(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lease = _automation_lease(last_traffic_at=None, ready_at=_FROZEN - dt.timedelta(hours=1))
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    redis = MagicMock()
    _with_pipeline(redis)
    redis.get = AsyncMock(side_effect=ConnectionError("redis unavailable"))
    teardown = AsyncMock()
    _patch_run_teardown(monkeypatch, teardown)

    await _lease_expiry_reconcile({"db_pool": _make_mock_pool(), "redis": redis, "now": _FROZEN})

    teardown.assert_not_awaited()
    assert sum("traffic data unavailable" in record.message for record in caplog.records) == 1


async def test_reachable_redis_without_key_uses_ready_at_for_idle_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease(last_traffic_at=None, ready_at=_FROZEN - dt.timedelta(hours=1))
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    redis = MagicMock()
    _with_pipeline(redis)
    redis.get = AsyncMock(return_value=None)
    teardown = AsyncMock()
    _patch_run_teardown(monkeypatch, teardown)

    await _lease_expiry_reconcile({"db_pool": _make_mock_pool(), "redis": redis, "now": _FROZEN})

    assert teardown.await_args.kwargs["reason"] == "idle"


async def test_lease_expiry_emits_once_at_each_exact_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ticks surrounding T-15 and T-5 emit exactly one event per threshold."""
    pool = _make_mock_pool()
    expires_at = _FROZEN + dt.timedelta(minutes=16)

    mock_conn = pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "lease-1",
                "provider_id": "provider-1",
                "runpod_pod_id": "pod-1",
                "expires_at": expires_at,
                "capability_name": "llm.auto",
                "auto_teardown_on_expiry": True,
                "state": "active",
            }
        ]
    )

    redis_mock = MagicMock()

    _with_pipeline(redis_mock)
    redis_mock.publish = MagicMock(return_value=1)

    ctx: dict = {"db_pool": pool, "redis": redis_mock}
    signed = AsyncMock()
    monkeypatch.setattr("pitwall.reconciler.publish_lease_event", signed)
    repo = _patch_automation_repositories(
        monkeypatch,
        _warning_lease("lease-1", "pod-1", expires_at, _FROZEN),
    )
    repo.list_active_for_activity_control.return_value = []
    claimed: set[tuple[dt.datetime, int]] = set()

    async def claim_warning(
        lease_id: str, *, expires_at: dt.datetime, threshold_minutes: int
    ) -> bool:
        assert lease_id == "lease-1"
        marker = (expires_at, threshold_minutes)
        if marker in claimed:
            return False
        claimed.add(marker)
        return True

    repo.claim_expiry_warning.side_effect = claim_warning

    with patch.dict("os.environ", {"PITWALL_LEASE_ADVANCE_WARNING_MIN": "15,5"}):
        for minutes_left in (16, 15, 14, 6, 5, 4, 1):
            ctx["now"] = lambda minutes_left=minutes_left: (
                expires_at - dt.timedelta(minutes=minutes_left)
            )
            await _lease_expiry_reconcile(ctx)

    assert redis_mock.publish.call_count == 2
    assert signed.await_count == 2
    assert [call.args[2]["data"]["minutes_left"] for call in signed.await_args_list] == [15, 5]
    assert {call.args[2]["capability"] for call in signed.await_args_list} == {"llm.auto"}


async def test_lease_expiry_tears_down_expired_lease_at_t0(monkeypatch) -> None:
    """At T-0 the reconciler calls scoped teardown with an expired terminal state."""
    pool = _make_mock_pool()
    now = dt.datetime.now(dt.UTC)
    expires_at = now - dt.timedelta(minutes=1)
    teardown_calls: list[dict[str, object]] = []

    mock_conn = pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "lease-expired",
                "provider_id": "provider-1",
                "runpod_pod_id": "pod-expired",
                "expires_at": expires_at,
                "auto_teardown_on_expiry": True,
                "state": "active",
            }
        ]
    )

    redis_mock = MagicMock()

    _with_pipeline(redis_mock)

    ctx: dict = {"db_pool": pool, "redis": redis_mock}
    monkeypatch.setattr("pitwall.reconciler.publish_lease_event", AsyncMock())

    async def fake_run_teardown(
        lease_id: str,
        *,
        pool: object,
        redis_client: object | None,
        reason: str | None,
        now: dt.datetime,
        terminal_state: LeaseState | str,
    ) -> None:
        teardown_calls.append(
            {
                "lease_id": lease_id,
                "pool": pool,
                "redis_client": redis_client,
                "reason": reason,
                "now": now,
                "terminal_state": terminal_state,
            }
        )

    # Import the module object and patch it directly, rather than via the
    # "pitwall.api.leases.teardown.run_teardown" string. pytest resolves a string
    # target by getattr-walking from the top package, which — after another test
    # purges `pitwall.api.*` from sys.modules but leaves the stale parent-package
    # attributes — lands on the OLD module object, while the reconciler's
    # `from pitwall.api.leases.teardown import run_teardown` re-imports a FRESH one
    # (the import system trusts sys.modules, not parent attrs). The two diverge and
    # the patch misses. `import ... as` forces the import machinery to reconcile
    # sys.modules and the parent attr to a single object, which the reconciler then
    # resolves identically — so the patch always lands.
    import pitwall.api.leases.teardown as _teardown_mod

    monkeypatch.setattr(_teardown_mod, "run_teardown", fake_run_teardown)
    _patch_automation_repositories(
        monkeypatch,
        _warning_lease("lease-expired", "pod-expired", expires_at, now),
    )
    with patch.dict("os.environ", {"PITWALL_LEASE_ADVANCE_WARNING_MIN": "15,5"}):
        await _lease_expiry_reconcile(ctx)

    redis_mock.publish.assert_not_called()
    assert len(teardown_calls) == 1
    assert teardown_calls[0]["lease_id"] == "lease-expired"
    assert teardown_calls[0]["pool"] is pool
    assert teardown_calls[0]["redis_client"] is redis_mock
    assert teardown_calls[0]["reason"] == "ttl"
    assert teardown_calls[0]["terminal_state"] is LeaseState.EXPIRED
    assert isinstance(teardown_calls[0]["now"], dt.datetime)


async def test_lease_expiry_skips_leases_outside_warning_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No warning is fired for leases not yet in any warning window."""
    pool = _make_mock_pool()
    now = dt.datetime.now(dt.UTC)
    expires_at = now + dt.timedelta(minutes=30)

    mock_conn = pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "lease-far",
                "provider_id": "provider-1",
                "runpod_pod_id": "pod-far",
                "expires_at": expires_at,
                "auto_teardown_on_expiry": True,
                "state": "active",
            }
        ]
    )

    redis_mock = MagicMock()

    _with_pipeline(redis_mock)

    ctx: dict = {"db_pool": pool, "redis": redis_mock}
    _patch_automation_repositories(
        monkeypatch,
        _warning_lease("lease-far", "pod-far", expires_at, now),
    )

    with patch.dict("os.environ", {"PITWALL_LEASE_ADVANCE_WARNING_MIN": "15,5"}):
        await _lease_expiry_reconcile(ctx)

    redis_mock.publish.assert_not_called()


async def test_lease_expiry_uses_default_warning_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default warning minutes (15,5) are used when env var is not set."""
    pool = _make_mock_pool()
    now = dt.datetime.now(dt.UTC)
    expires_at = now + dt.timedelta(minutes=15)

    mock_conn = pool.acquire.return_value.__aenter__.return_value
    mock_conn.fetch = AsyncMock(
        return_value=[
            {
                "id": "lease-1",
                "provider_id": "provider-1",
                "runpod_pod_id": "pod-1",
                "expires_at": expires_at,
                "auto_teardown_on_expiry": True,
                "state": "active",
            }
        ]
    )

    redis_mock = MagicMock()

    _with_pipeline(redis_mock)
    redis_mock.publish = MagicMock(return_value=1)

    ctx: dict = {"db_pool": pool, "redis": redis_mock}
    monkeypatch.setattr("pitwall.reconciler.publish_lease_event", AsyncMock())
    repo = _patch_automation_repositories(
        monkeypatch,
        _warning_lease("lease-1", "pod-1", expires_at, now),
    )
    repo.claim_expiry_warning.return_value = True

    with patch.dict("os.environ", {}, clear=True):
        await _lease_expiry_reconcile(ctx)

    redis_mock.publish.assert_called_once()


_FROZEN = dt.datetime(2026, 8, 28, 12, 0, tzinfo=dt.UTC)


def _teardown_result(lease: Lease) -> object:
    """A LeaseTeardownResult-shaped object whose closed lease has accrued cost 0.42."""
    from pitwall.api.leases.teardown import LeaseTeardownResult

    closed = lease.model_copy(update={"cost_accrued_usd": Decimal("0.42")})
    return LeaseTeardownResult(lease=closed, event=None)


def _automation_lease(**changes: object) -> Lease:
    values: dict[str, object] = {
        "id": "lease-auto",
        "provider_id": "provider-auto",
        "runpod_pod_id": "pod-auto",
        "state": "creating",
        "created_at": _FROZEN - dt.timedelta(hours=1),
        "expires_at": _FROZEN + dt.timedelta(minutes=14),
        "renewal_policy": LeaseRenewalPolicy.ACTIVITY,
        "ready_at": _FROZEN - dt.timedelta(minutes=10),
        "last_traffic_at": _FROZEN - dt.timedelta(minutes=1),
        "idle_timeout_min": 20,
        "max_usd_per_hour": Decimal("1.00"),
    }
    values.update(changes)
    return Lease.model_validate(values)


def _warning_lease(
    lease_id: str,
    pod_id: str,
    expires_at: dt.datetime,
    now: dt.datetime,
) -> Lease:
    return _automation_lease(
        id=lease_id,
        provider_id="provider-1",
        runpod_pod_id=pod_id,
        created_at=now - dt.timedelta(hours=1),
        expires_at=expires_at,
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        idle_timeout_min=None,
        max_usd_per_hour=None,
    )


def _automation_provider() -> Provider:
    return Provider(
        id="provider-auto",
        capability_id="capability-auto",
        name="serve-auto",
        provider_type=ProviderType.POD_LEASE,
        config={
            "gpu_types": ["NVIDIA L4"],
            "lease_ttl_ms": 3_600_000,
            "cost": {"per_second_active": "0.001"},
        },
        priority=0,
        source=CapabilitySource.API,
        updated_at=_FROZEN,
    )


def _automation_capability() -> Capability:
    return Capability(
        id="capability-auto",
        name="llm.auto",
        version="1.0.0",
        **{"class": CapabilityClass.LLM},
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        created_at=_FROZEN,
        updated_at=_FROZEN,
    )


def _patch_automation_repositories(
    monkeypatch: pytest.MonkeyPatch,
    lease: Lease,
) -> AsyncMock:
    lease_repo = AsyncMock()
    lease_repo.get.return_value = lease
    provider_repo = _provider_repo(_automation_provider())
    capability_repo = AsyncMock()
    capability_repo.get.return_value = _automation_capability()
    monkeypatch.setattr("pitwall.reconciler.LeaseRepository", lambda _pool: lease_repo)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    monkeypatch.setattr("pitwall.reconciler.CapabilityRepository", lambda _pool: capability_repo)
    return lease_repo


async def test_activity_renewal_uses_original_ttl_same_id_and_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease()
    repo = _patch_automation_repositories(monkeypatch, lease)
    renew = AsyncMock(
        return_value=lease.model_copy(
            update={"expires_at": lease.expires_at + dt.timedelta(minutes=60)}
        )
    )
    monkeypatch.setattr("pitwall.reconciler.renew_lease", renew)
    monkeypatch.setattr(
        "pitwall.reconciler._renewal_refusal_reason",
        AsyncMock(return_value=None),
    )
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": lease.expires_at,
            "auto_teardown_on_expiry": True,
            "state": "active",
        }
    ]

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": _FROZEN})

    renew.assert_awaited_once()
    assert renew.await_args.args[1] == lease.id
    assert renew.await_args.kwargs["extends_minutes"] == 60
    assert renew.await_args.kwargs["renewed_by"] == "activity"
    assert renew.await_args.kwargs["actor"] == "reconciler:activity"
    assert repo.get.await_count == 1


@pytest.mark.parametrize(
    ("changes", "expected_reason"),
    [
        ({"last_traffic_at": _FROZEN - dt.timedelta(minutes=21)}, "idle"),
        ({"created_at": _FROZEN - dt.timedelta(minutes=1440)}, "max_lifetime"),
        (
            {
                "expires_at": _FROZEN - dt.timedelta(seconds=1),
                "renewal_policy": LeaseRenewalPolicy.MANUAL,
                "idle_timeout_min": None,
            },
            "ttl",
        ),
    ],
)
async def test_reconciler_routes_all_stops_through_teardown(
    monkeypatch: pytest.MonkeyPatch,
    changes: dict[str, object],
    expected_reason: str,
) -> None:
    lease = _automation_lease(**changes)
    _patch_automation_repositories(monkeypatch, lease)
    teardown = AsyncMock()
    import pitwall.api.leases.teardown as teardown_module

    monkeypatch.setattr(teardown_module, "run_teardown", teardown)
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": lease.expires_at,
            "auto_teardown_on_expiry": True,
            "state": "active",
        }
    ]

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": _FROZEN})

    teardown.assert_awaited_once()
    assert teardown.await_args.kwargs["reason"] == expected_reason


async def test_activity_without_timeout_and_stale_traffic_expires_as_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease(
        expires_at=_FROZEN - dt.timedelta(seconds=1),
        idle_timeout_min=None,
        last_traffic_at=_FROZEN - dt.timedelta(minutes=16),
        max_usd_per_hour=None,
    )
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    renew = AsyncMock()
    teardown = AsyncMock()
    monkeypatch.setattr("pitwall.reconciler.renew_lease", renew)
    _patch_run_teardown(monkeypatch, teardown)
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = [{**lease.model_dump(), "capability_name": "llm.auto"}]
    redis = MagicMock()
    _with_pipeline(redis)
    redis.get = AsyncMock(return_value=None)

    await _lease_expiry_reconcile({"db_pool": pool, "redis": redis, "now": _FROZEN})

    renew.assert_not_awaited()
    assert teardown.await_args.kwargs["reason"] == "ttl"


async def test_activity_without_timeout_renews_and_writes_through_recent_traffic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease(idle_timeout_min=None, last_traffic_at=None)
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    renew = AsyncMock(return_value=lease)
    monkeypatch.setattr("pitwall.reconciler.renew_lease", renew)
    monkeypatch.setattr("pitwall.reconciler._renewal_refusal_reason", AsyncMock(return_value=None))
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = [{**lease.model_dump(), "capability_name": "llm.auto"}]
    redis = MagicMock()
    _with_pipeline(redis)
    seen_at = _FROZEN - dt.timedelta(minutes=1)
    redis.get = AsyncMock(return_value=seen_at.isoformat())

    await _lease_expiry_reconcile({"db_pool": pool, "redis": redis, "now": _FROZEN})

    repo.record_traffic.assert_awaited_once_with(lease.id, seen_at=seen_at)
    renew.assert_awaited_once()


async def test_activity_tick_excludes_self_hosted_provider_leases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease(
        expires_at=_FROZEN + dt.timedelta(hours=6),
        ready_at=_FROZEN - dt.timedelta(hours=1),
    )
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    provider = Provider(
        id=lease.provider_id,
        capability_id="cap_selfhosted",
        name="selfhosted",
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        config={"self_hosted": {"readiness": {"kind": "openai-models"}}},
        priority=0,
        source=CapabilitySource.API,
        updated_at=_FROZEN,
    )
    providers = _provider_repo(provider)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda pool: providers)
    teardown = AsyncMock()
    _patch_run_teardown(monkeypatch, teardown)

    await _lease_expiry_reconcile(
        {"db_pool": _make_mock_pool(), "redis": _plain_redis(), "now": _FROZEN}
    )

    teardown.assert_not_awaited()
    repo.extend_expiry.assert_not_awaited()


@pytest.mark.parametrize("disposition", ["renew", "idle", "ttl"])
async def test_expiry_pass_skips_reloaded_self_hosted_provider_lease(
    monkeypatch: pytest.MonkeyPatch,
    disposition: str,
) -> None:
    changes: dict[str, object] = {"expires_at": _FROZEN + dt.timedelta(minutes=1)}
    if disposition == "idle":
        changes.update(
            last_traffic_at=_FROZEN - dt.timedelta(hours=1),
            idle_timeout_min=5,
        )
    elif disposition == "ttl":
        changes.update(
            expires_at=_FROZEN - dt.timedelta(seconds=1),
            renewal_policy=LeaseRenewalPolicy.MANUAL,
        )
    lease = _automation_lease(**changes)
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    provider = _automation_provider().model_copy(
        update={
            "provider_type": ProviderType.PUBLIC_ENDPOINT,
            "config": {"self_hosted": {"readiness": {"kind": "openai-models"}}},
        }
    )
    provider_repo = _provider_repo(provider)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    renew = AsyncMock()
    teardown = AsyncMock()
    monkeypatch.setattr("pitwall.reconciler.renew_lease", renew)
    _patch_run_teardown(monkeypatch, teardown)
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = [{**lease.model_dump(), "capability_name": "llm.auto"}]

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": _FROZEN})

    renew.assert_not_awaited()
    teardown.assert_not_awaited()


async def test_raw_pod_teardown_closes_linked_workload_with_accrued_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After expired-provider-less pre-active teardown, the linked workload is
    closed (``completed``) with the teardown's accrued cost as actual cost."""
    pool = _make_mock_pool()
    now = _FROZEN
    expires_at = now - dt.timedelta(minutes=1)
    lease = _automation_lease(
        id="lease-raw-close",
        state=LeaseState.CREATING,
        provider_id="runpod_direct",
        runpod_pod_id="pod-raw-close",
        workload_id="wkl_raw_close",
        expires_at=expires_at,
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        idle_timeout_min=None,
    )
    _patch_run_teardown(monkeypatch, AsyncMock(return_value=_teardown_result(lease)))
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    provider_repo = _provider_repo(None)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)

    workload_close_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    constructed_pools: list[object] = []

    def fake_workload_repo(pool: object) -> object:
        constructed_pools.append(pool)

        class _Repo:
            async def update_state(self, *args: object, **kwargs: object) -> None:
                workload_close_calls.append((args, kwargs))

        return _Repo()

    monkeypatch.setattr("pitwall.reconciler.WorkloadRepository", fake_workload_repo, raising=False)
    pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": expires_at,
            "auto_teardown_on_expiry": True,
            "state": "creating",
            "capability_name": None,
        }
    ]

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": now})

    assert len(constructed_pools) == 1
    assert constructed_pools[0] is pool
    assert len(workload_close_calls) == 1
    args, kwargs = workload_close_calls[0]
    assert args[0] == "wkl_raw_close"
    assert args[1] == "completed"
    assert kwargs["cost_actual_usd"] == Decimal("0.42")
    assert kwargs["cost_actual_provenance"] == "lease_teardown"
    assert kwargs["cost_reconciled_at"] == now


async def test_raw_pod_teardown_closes_workload_after_reload_branch_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reload branch (lease not activity-controlled) also closes the workload
    of an expired provider-less pre-active lease."""
    pool = _make_mock_pool()
    now = _FROZEN
    expires_at = now - dt.timedelta(minutes=1)
    lease = _automation_lease(
        id="lease-raw-close-reload",
        state=LeaseState.CREATING,
        provider_id="runpod_direct",
        runpod_pod_id="pod-raw-close-reload",
        workload_id="wkl_raw_close_reload",
        expires_at=expires_at,
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        idle_timeout_min=None,
    )
    _patch_run_teardown(monkeypatch, AsyncMock(return_value=_teardown_result(lease)))
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    provider_repo = _provider_repo(None)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)

    workload_close_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_workload_repo(_pool: object) -> object:
        class _Repo:
            async def update_state(self, *args: object, **kwargs: object) -> None:
                workload_close_calls.append((args, kwargs))

        return _Repo()

    monkeypatch.setattr("pitwall.reconciler.WorkloadRepository", fake_workload_repo, raising=False)
    pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": expires_at,
            "auto_teardown_on_expiry": True,
            "state": "creating",
            "capability_name": None,
        }
    ]

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": now})

    assert len(workload_close_calls) == 1
    args, kwargs = workload_close_calls[0]
    assert args[0] == "wkl_raw_close_reload"
    assert args[1] == "completed"
    assert kwargs["cost_actual_usd"] == Decimal("0.42")


async def test_workload_close_failure_never_blocks_teardown(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing workload close is logged and never breaks the expiry sweep."""
    pool = _make_mock_pool()
    now = _FROZEN
    expires_at = now - dt.timedelta(minutes=1)
    lease = _automation_lease(
        id="lease-raw-close-fail",
        state=LeaseState.CREATING,
        provider_id="runpod_direct",
        runpod_pod_id="pod-raw-close-fail",
        workload_id="wkl_raw_close_fail",
        expires_at=expires_at,
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        idle_timeout_min=None,
    )
    teardown = AsyncMock(return_value=_teardown_result(lease))
    _patch_run_teardown(monkeypatch, teardown)
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    provider_repo = _provider_repo(None)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)

    def fake_workload_repo(_pool: object) -> object:
        class _Repo:
            async def update_state(self, *args: object, **kwargs: object) -> None:
                raise ConnectionError("db down")

        return _Repo()

    monkeypatch.setattr("pitwall.reconciler.WorkloadRepository", fake_workload_repo, raising=False)
    pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": expires_at,
            "auto_teardown_on_expiry": True,
            "state": "creating",
            "capability_name": None,
        }
    ]

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": now})

    teardown.assert_awaited_once()
    assert sum("failed" in record.message for record in caplog.records) >= 1


async def test_expired_lease_tears_down_with_missing_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _automation_lease(
        expires_at=_FROZEN - dt.timedelta(seconds=1),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
    )
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    provider_repo = _provider_repo(None)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    teardown = AsyncMock()
    _patch_run_teardown(monkeypatch, teardown)
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = [{**lease.model_dump(), "capability_name": "llm.auto"}]

    await _lease_expiry_reconcile({"db_pool": pool, "redis": None, "now": _FROZEN})

    teardown.assert_awaited_once()
    provider_repo.get_many.assert_awaited_once_with({lease.provider_id})
    provider_repo.get.assert_not_awaited()


async def test_warning_uses_joined_capability_when_provider_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = _warning_lease(
        "lease-orphan", "pod-orphan", _FROZEN + dt.timedelta(minutes=15), _FROZEN
    )
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    repo.claim_expiry_warning.return_value = True
    provider_repo = _provider_repo(None)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    signed = AsyncMock()
    monkeypatch.setattr("pitwall.reconciler.publish_lease_event", signed)
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = [
        {**lease.model_dump(), "capability_name": "llm.orphan", "state": "active"}
    ]

    await _lease_expiry_reconcile({"db_pool": pool, "redis": _plain_redis(), "now": _FROZEN})

    assert signed.await_args.args[2]["capability"] == "llm.orphan"


@pytest.mark.parametrize(
    ("gate", "expected_reason"),
    [
        ("price_above_cap", "budget"),
        ("fallback_price", "budget"),
        ("budget", "budget"),
        ("kill_switch", "kill_switch"),
    ],
)
async def test_expired_activity_renewal_refusal_uses_real_gates(
    monkeypatch: pytest.MonkeyPatch,
    gate: str,
    expected_reason: str,
) -> None:
    lease = _automation_lease(expires_at=_FROZEN - dt.timedelta(seconds=1))
    if gate in {"budget", "kill_switch"}:
        lease = lease.model_copy(update={"max_usd_per_hour": None})
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    price = Decimal("1.25") if gate == "price_above_cap" else Decimal("0.50")
    snapshot = GpuPriceSnapshot(
        gpu_types=(
            RunpodGpuType(
                id="NVIDIA L4",
                memoryInGb=24,
                securePrice=price,
            ),
        ),
        checked_at=_FROZEN,
        source="fallback" if gate == "fallback_price" else "live",
    )
    monkeypatch.setattr(
        "pitwall.reconciler.load_gpu_price_snapshot",
        AsyncMock(return_value=snapshot),
    )
    kill_gate = AsyncMock()
    if gate == "kill_switch":
        kill_gate.side_effect = KillSwitchEngaged()
    monkeypatch.setattr("pitwall.reconciler.CloudKillSwitch.ensure_disengaged", kill_gate)
    budget_gate = AsyncMock()
    if gate == "budget":
        budget_gate.check_available.side_effect = BudgetRejected("monthly_budget", MagicMock())
    monkeypatch.setattr("pitwall.reconciler.BudgetGate", lambda _pool: budget_gate)
    renew = AsyncMock()
    teardown = AsyncMock()
    monkeypatch.setattr("pitwall.reconciler.renew_lease", renew)
    _patch_run_teardown(monkeypatch, teardown)
    pool = _make_mock_pool()
    pool.conn.fetch.return_value = [{**lease.model_dump(), "capability_name": "llm.auto"}]
    redis = MagicMock()
    _with_pipeline(redis)
    redis.get = AsyncMock(return_value=None)

    await _lease_expiry_reconcile({"db_pool": pool, "redis": redis, "now": _FROZEN})

    renew.assert_not_awaited()
    assert teardown.await_args.kwargs["reason"] == expected_reason


async def test_expired_creating_raw_pod_lease_reaches_t0_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired creating raw-pod lease row converges through the expiry sweeper."""
    pool = _make_mock_pool()
    now = _FROZEN
    expires_at = now - dt.timedelta(minutes=1)
    lease = _automation_lease(
        id="lease-raw-expired",
        state=LeaseState.CREATING,
        provider_id="runpod_direct",
        runpod_pod_id="pod-raw-expired",
        expires_at=expires_at,
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        idle_timeout_min=None,
    )
    teardown_calls: list[dict[str, object]] = []

    async def fake_run_teardown(
        lease_id: str,
        *,
        pool: object,
        redis_client: object | None,
        reason: str | None,
        now: dt.datetime,
        terminal_state: LeaseState | str,
    ) -> None:
        teardown_calls.append(
            {
                "lease_id": lease_id,
                "reason": reason,
                "terminal_state": terminal_state,
            }
        )

    _patch_run_teardown(monkeypatch, fake_run_teardown)
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    provider_repo = _provider_repo(None)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": expires_at,
            "auto_teardown_on_expiry": True,
            "state": "creating",
            "capability_name": None,
        }
    ]
    redis = MagicMock()
    _with_pipeline(redis)

    import pitwall.reconciler as reconciler_module

    assert "creating" in reconciler_module._LEASE_EXPIRY_LEASES_SQL, (
        "the expiry sweep must select pre-active auto-teardown lease rows"
    )

    with patch.dict("os.environ", {"PITWALL_LEASE_ADVANCE_WARNING_MIN": "15,5"}):
        await _lease_expiry_reconcile({"db_pool": pool, "redis": redis, "now": now})

    assert len(teardown_calls) == 1
    assert teardown_calls[0]["lease_id"] == lease.id
    assert teardown_calls[0]["reason"] == "ttl"
    assert teardown_calls[0]["terminal_state"] is LeaseState.EXPIRED
    redis.publish.assert_not_called()


async def test_future_expiry_creating_raw_pod_lease_is_not_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A creating raw-pod lease with a future expiry gets no teardown and no warnings."""
    pool = _make_mock_pool()
    now = _FROZEN
    expires_at = now + dt.timedelta(minutes=10)
    lease = _automation_lease(
        id="lease-raw-future",
        state=LeaseState.CREATING,
        provider_id="runpod_direct",
        runpod_pod_id="pod-raw-future",
        expires_at=expires_at,
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        idle_timeout_min=None,
    )
    teardown = AsyncMock()
    _patch_run_teardown(monkeypatch, teardown)
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = [lease]
    provider_repo = _provider_repo(None)
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    pool.conn.fetch.return_value = [
        {
            "id": lease.id,
            "provider_id": lease.provider_id,
            "runpod_pod_id": lease.runpod_pod_id,
            "expires_at": expires_at,
            "auto_teardown_on_expiry": True,
            "state": "creating",
            "capability_name": None,
        }
    ]
    redis = MagicMock()
    _with_pipeline(redis)

    with patch.dict("os.environ", {"PITWALL_LEASE_ADVANCE_WARNING_MIN": "15,5"}):
        await _lease_expiry_reconcile({"db_pool": pool, "redis": redis, "now": now})

    teardown.assert_not_awaited()
    redis.publish.assert_not_called()
    repo.claim_expiry_warning.assert_not_awaited()


async def test_leases_left_stopping_by_a_failed_teardown_are_retried(monkeypatch) -> None:
    """A lease a failed teardown left `stopping` still has a billing pod; retry every tick."""
    pool = _make_mock_pool()
    now = dt.datetime.now(dt.UTC)
    stuck = [
        {"id": "lease-stuck-live", "state": "stopping", "expires_at": now + dt.timedelta(hours=2)},
        {
            "id": "lease-stuck-expired",
            "state": "stopping",
            "expires_at": now - dt.timedelta(minutes=5),
        },
        {"id": "lease-stuck-again", "state": "stopping", "expires_at": now + dt.timedelta(hours=1)},
    ]

    async def fetch(sql: str, *args: object) -> list[dict[str, object]]:
        return stuck if "state = 'stopping'" in sql else []

    pool.conn.fetch = AsyncMock(side_effect=fetch)
    calls: list[tuple[str, str | None, object]] = []

    async def fake_run_teardown(lease_id: str, **kwargs: object) -> None:
        calls.append((lease_id, kwargs.get("reason"), kwargs.get("terminal_state")))
        if lease_id == "lease-stuck-live":
            raise RuntimeError("provider still unreachable")

    _patch_run_teardown(monkeypatch, fake_run_teardown)

    await _lease_expiry_reconcile({"db_pool": pool, "redis": None})

    assert calls == [
        ("lease-stuck-live", "operator", LeaseState.STOPPED),
        ("lease-stuck-expired", "ttl", LeaseState.EXPIRED),
        ("lease-stuck-again", "operator", LeaseState.STOPPED),
    ]


async def test_budget_breach_escalation_reads_nothing_when_disabled() -> None:
    from pitwall.config import PitwallSettings
    from pitwall.reconciler import _budget_breach_escalation

    pool = MagicMock()
    pool.acquire.side_effect = AssertionError("disabled escalation must not read the database")
    settings = PitwallSettings(pitwall_budget_breach_kill_mode="disabled")
    assert await _budget_breach_escalation({"db_pool": pool, "settings": settings}) is None


def _unexpired_raw_pod_setup(
    monkeypatch: pytest.MonkeyPatch, *, created_minutes_ago: int = 30
) -> tuple[Any, Lease, AsyncMock, list[tuple[tuple[object, ...], dict[str, object]]]]:
    """A provider-less raw-pod lease 90 minutes from expiry, with teardown and workload fakes."""
    pool = _make_mock_pool()
    lease = _automation_lease(
        id="lease-raw-absent",
        state=LeaseState.CREATING,
        provider_id="runpod_direct",
        runpod_pod_id="pod-raw-absent",
        workload_id="wkl_raw_absent",
        created_at=_FROZEN - dt.timedelta(minutes=created_minutes_ago),
        expires_at=_FROZEN + dt.timedelta(minutes=90),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        idle_timeout_min=None,
    )
    teardown = AsyncMock(return_value=_teardown_result(lease))
    _patch_run_teardown(monkeypatch, teardown)
    repo = _patch_automation_repositories(monkeypatch, lease)
    repo.list_active_for_activity_control.return_value = []
    provider_repo = AsyncMock()
    provider_repo.get.return_value = None
    monkeypatch.setattr("pitwall.reconciler.ProviderRepository", lambda _pool: provider_repo)
    closes: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _Workloads:
        async def update_state(self, *args: object, **kwargs: object) -> None:
            closes.append((args, kwargs))

    monkeypatch.setattr(
        "pitwall.reconciler.WorkloadRepository", lambda _pool: _Workloads(), raising=False
    )
    pool.conn.fetch.return_value = [
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
    return pool, lease, teardown, closes


async def test_raw_pod_lease_closes_early_when_its_pod_is_gone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raw pod terminated before its TTL (through the broker or at RunPod directly) no longer
    holds its budget reservation until expiry: the lease closes and the workload gets its
    accrued cost within one reconciler tick."""
    pool, lease, teardown, closes = _unexpired_raw_pod_setup(monkeypatch)
    lookups: list[str] = []

    async def absent(pod_id: str, **_: object) -> None:
        lookups.append(pod_id)
        return None

    monkeypatch.setattr("pitwall.runpod_client.pods.get_pod_strict", absent)
    await _lease_expiry_reconcile({"db_pool": pool, "redis": MagicMock(), "now": _FROZEN})

    assert lookups == ["pod-raw-absent"]
    teardown.assert_awaited_once()
    assert teardown.await_args.args[0] == "lease-raw-absent"
    assert teardown.await_args.kwargs["terminated_reason"] == "pod_absent"
    assert len(closes) == 1
    args, kwargs = closes[0]
    assert (args[0], args[1], kwargs["cost_actual_usd"]) == (
        "wkl_raw_absent",
        "completed",
        Decimal("0.42"),
    )


async def test_raw_pod_lease_stays_open_while_its_pod_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _lease, teardown, closes = _unexpired_raw_pod_setup(monkeypatch)

    async def running(pod_id: str, **_: object) -> dict[str, object]:
        return {"id": pod_id, "desiredStatus": "RUNNING"}

    monkeypatch.setattr("pitwall.runpod_client.pods.get_pod_strict", running)
    await _lease_expiry_reconcile({"db_pool": pool, "redis": MagicMock(), "now": _FROZEN})

    teardown.assert_not_awaited()
    assert closes == []


async def test_runpod_outage_never_reads_as_an_absent_pod(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _lease, teardown, closes = _unexpired_raw_pod_setup(monkeypatch)

    async def outage(pod_id: str, **_: object) -> None:
        raise RuntimeError("RunPod unavailable")

    monkeypatch.setattr("pitwall.runpod_client.pods.get_pod_strict", outage)
    await _lease_expiry_reconcile({"db_pool": pool, "redis": MagicMock(), "now": _FROZEN})

    teardown.assert_not_awaited()
    assert closes == []


async def test_a_just_created_raw_pod_lease_is_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    pool, _lease, teardown, _closes = _unexpired_raw_pod_setup(monkeypatch, created_minutes_ago=1)

    async def must_not_be_called(pod_id: str, **_: object) -> None:
        raise AssertionError("a lease inside the creation grace window must not be probed")

    monkeypatch.setattr("pitwall.runpod_client.pods.get_pod_strict", must_not_be_called)
    await _lease_expiry_reconcile({"db_pool": pool, "redis": MagicMock(), "now": _FROZEN})

    teardown.assert_not_awaited()
