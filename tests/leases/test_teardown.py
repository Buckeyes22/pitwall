from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall.api.exceptions import PreSpendPayloadRejected
from pitwall.api.leases import teardown
from pitwall.api.routes import leases as lease_routes
from pitwall.api.schemas.leases import LeaseStop
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability, Lease, LeaseEndpoints, LeaseReadiness, Provider
from pitwall.providers.interface import (
    CredentialReference,
    CredentialResolutionError,
    TeardownRequest,
)
from pitwall.security.pre_spend import PreSpendInspectionService
from tests.fakes.teardown import UnlockedTeardown

_CREATED_AT = dt.datetime(2026, 5, 28, 12, 0, tzinfo=dt.UTC)
_TERMINATED_AT = dt.datetime(2026, 5, 28, 12, 10, tzinfo=dt.UTC)


def _readiness() -> LeaseReadiness:
    return LeaseReadiness(
        runtime_seen_at=dt.datetime(2026, 5, 28, 12, 0, 18, tzinfo=dt.UTC),
        port_mappings_seen_at=dt.datetime(2026, 5, 28, 12, 0, 19, tzinfo=dt.UTC),
        probe_passed_at=dt.datetime(2026, 5, 28, 12, 0, 34, tzinfo=dt.UTC),
        probe_method="ssh_localhost",
    )


def _endpoints() -> LeaseEndpoints:
    return LeaseEndpoints(
        http={"8000": "https://pod-target-8000.proxy.runpod.net"},
        tcp={"22": {"host": "pod-target.proxy.runpod.net", "port": 19022}},
    )


def _lease(
    state: LeaseState = LeaseState.ACTIVE,
    *,
    cost_accrued_usd: Decimal | None = None,
    terminated_at: dt.datetime | None = None,
    terminated_reason: str | None = None,
) -> Lease:
    return Lease(
        id="lease-target",
        provider_id="provider-target",
        runpod_pod_id="pod-target",
        state=state,
        created_at=_CREATED_AT,
        expires_at=_CREATED_AT + dt.timedelta(hours=2),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        endpoints=_endpoints(),
        readiness=_readiness(),
        cost_accrued_usd=cost_accrued_usd,
        terminated_at=terminated_at,
        terminated_reason=terminated_reason,
    )


def _provider() -> Provider:
    return Provider(
        id="provider-target",
        capability_id="cap-target",
        name="target-provider",
        provider_type=ProviderType.POD_LEASE,
        config={"cost": {"per_second_active": "0.002"}},
        priority=1,
        source=CapabilitySource.API,
        updated_at=_CREATED_AT,
    )


@pytest.mark.anyio
async def test_teardown_guardrail_blocks_secret_reason_before_repository_or_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-1234567890abcdef1234567890abcdef"
    guardrail = PreSpendInspectionService()
    repository_constructed = False

    def forbidden_repository(_pool: object) -> object:
        nonlocal repository_constructed
        repository_constructed = True
        raise AssertionError("rejected teardown must not reach persistence")

    monkeypatch.setattr(teardown, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(teardown, "LeaseRepository", forbidden_repository)
    terminate = AsyncMock()
    monkeypatch.setattr(teardown, "terminate_pod", terminate)

    with pytest.raises(PreSpendPayloadRejected) as exc_info:
        await teardown.run_teardown(
            "lease-target",
            pool=object(),
            terminated_reason=f"operator note {secret}",
        )

    serialized = repr(exc_info.value.to_response_body())
    assert secret not in serialized
    assert exc_info.value.error_code == "pre_spend_payload_rejected"
    assert not repository_constructed
    terminate.assert_not_awaited()
    assert guardrail.status().counters.block == 1


def test_teardown_guardrail_redacts_pii_without_retaining_original(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guardrail = PreSpendInspectionService()
    monkeypatch.setattr(teardown, "get_pre_spend_inspection_service", lambda: guardrail)

    lease_id, reason, terminated_reason = teardown._guard_teardown_inputs(
        lease_id="lease-target",
        reason="operator",
        terminated_reason="requested by jane.roe@example.com",
    )

    assert lease_id == "lease-target"
    assert reason == "operator"
    assert terminated_reason == "requested by [REDACTED:email]"
    status = guardrail.status()
    assert status.counters.redact == 1
    assert "jane.roe@example.com" not in repr(status.to_dict())


@pytest.mark.anyio
async def test_teardown_guardrail_never_reflects_a_rejected_lease_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-1234567890abcdef1234567890abcdef"
    guardrail = PreSpendInspectionService()
    repository = AsyncMock(side_effect=AssertionError("repository must not be constructed"))
    monkeypatch.setattr(teardown, "get_pre_spend_inspection_service", lambda: guardrail)
    monkeypatch.setattr(teardown, "LeaseRepository", repository)

    with pytest.raises(PreSpendPayloadRejected) as exc_info:
        await teardown.run_teardown(secret, pool=object())

    assert secret not in repr(exc_info.value.to_response_body())
    repository.assert_not_called()


@pytest.mark.anyio
async def test_idle_teardown_writes_automated_lease_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audits: list[dict[str, Any]] = []
    current = _lease().model_copy(
        update={"external_resource_id": "resource-target", "runpod_pod_id": None}
    )

    class LeaseRepo(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease:
            assert lease_id == current.id
            return current

        async def update_state(self, lease_id: str, state: str) -> Lease:
            assert lease_id == current.id
            return current.model_copy(update={"state": state})

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease:
            assert lease_id == current.id
            return current.model_copy(update=changes)

        async def capability_name(self, lease_id: str) -> str:
            assert lease_id == current.id
            return "llm.target"

    class ProviderRepo:
        async def get(self, provider_id: str) -> Provider:
            assert provider_id == current.provider_id
            return _provider()

    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LeaseRepo())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: ProviderRepo())
    monkeypatch.setattr(teardown, "disarm_serve_provider", AsyncMock(return_value=False))
    monkeypatch.setattr(teardown, "terminate_pod", AsyncMock())
    audit = AsyncMock(side_effect=lambda pool, **row: audits.append(row))
    monkeypatch.setattr(teardown, "insert_audit", audit)

    result = await teardown.run_teardown(
        "lease-target",
        pool=object(),
        reason="idle",
        now=_TERMINATED_AT,
    )

    lease_audit = next(row for row in audits if row["entity_type"] == "lease")
    assert result.lease.state == LeaseState.STOPPED
    assert lease_audit == {
        "actor": "system:lease-controller",
        "action": "stop",
        "entity_type": "lease",
        "entity_id": "lease-target",
        "old_value": {"state": "active"},
        "new_value": {
            "state": "stopped",
            "reason": "idle",
            "external_resource_id": "resource-target",
        },
        "change_reason": "automated lease stop: idle",
    }


@pytest.mark.anyio
async def test_run_teardown_reports_audit_failure_without_failing_after_disarm_and_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _lease()
    provider = _provider().model_copy(
        update={
            "config": {
                **_provider().config,
                "active_pod_id": current.runpod_pod_id,
                "active_lease_id": current.id,
            }
        }
    )

    class LeaseRepo(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease:
            assert lease_id == current.id
            return current

        async def capability_name(self, lease_id: str) -> str:
            assert lease_id == current.id
            return "llm.target"

        async def update_state(self, lease_id: str, state: str) -> Lease:
            assert lease_id == current.id
            return current.model_copy(update={"state": state})

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease:
            assert lease_id == current.id
            return current.model_copy(update=changes)

    class ProviderRepo:
        async def get(self, provider_id: str) -> Provider:
            assert provider_id == provider.id
            return provider

        async def patch(self, provider_id: str, **changes: Any) -> Provider:
            nonlocal provider
            assert provider_id == provider.id
            provider = provider.model_copy(update=changes)
            return provider

    redis = AsyncMock()
    redis.publish.return_value = 1
    stopped = AsyncMock()
    audit = AsyncMock(side_effect=RuntimeError("audit unavailable"))
    pool = object()
    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LeaseRepo())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: ProviderRepo())
    monkeypatch.setattr(teardown, "terminate_pod", AsyncMock())
    monkeypatch.setattr(teardown, "publish_lease_event", stopped)
    monkeypatch.setattr(teardown, "insert_audit", audit)

    result = await teardown.run_teardown(
        current.id,
        pool=pool,
        redis_client=redis,
        reason="idle",
        now=_TERMINATED_AT,
    )

    assert result.errors == ("audit: RuntimeError", "disarm: RuntimeError")
    assert "active_pod_id" not in provider.config
    assert "active_lease_id" not in provider.config
    assert provider.health_status == "disarmed"
    assert audit.await_count == 2
    redis.publish.assert_awaited_once()
    assert json.loads(redis.publish.await_args.args[1])["event"] == "lease.terminated"
    stopped.assert_awaited_once()
    assert stopped.await_args.args[2]["event"] == "lease.stopped"


@pytest.mark.anyio
async def test_run_teardown_terminates_only_target_pod_closes_cost_and_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated_pods: list[str] = []
    state_updates: list[str] = []
    close_kwargs: dict[str, Any] = {}

    class FakeLeaseRepository(UnlockedTeardown):
        def __init__(self, pool: object) -> None:
            assert pool == "pool"

        async def get(self, lease_id: str) -> Lease:
            assert lease_id == "lease-target"
            return _lease()

        async def update_state(self, lease_id: str, state: str) -> Lease:
            assert lease_id == "lease-target"
            state_updates.append(state)
            return _lease(LeaseState(state))

        async def close_teardown(self, lease_id: str, **kwargs: Any) -> Lease:
            assert lease_id == "lease-target"
            close_kwargs.update(kwargs)
            return _lease(
                LeaseState(kwargs["state"]),
                cost_accrued_usd=kwargs["cost_accrued_usd"],
                terminated_at=kwargs["terminated_at"],
                terminated_reason=kwargs["terminated_reason"],
            )

        async def capability_name(self, lease_id: str) -> str:
            assert lease_id == "lease-target"
            return "llm.target"

    class FakeProviderRepository:
        def __init__(self, pool: object) -> None:
            assert pool == "pool"

        async def get(self, provider_id: str) -> Provider:
            assert provider_id == "provider-target"
            return _provider()

    class FakeCapabilityRepository:
        def __init__(self, pool: object) -> None:
            assert pool == "pool"

        async def get(self, capability_id: str) -> Capability:
            assert capability_id == "cap-target"
            return Capability(
                id="cap-target",
                name="llm.target",
                version="1.0.0",
                class_=CapabilityClass.LLM,
                cost_mode="per_second",
                source=CapabilitySource.API,
                created_at=_CREATED_AT,
                updated_at=_CREATED_AT,
            )

    class FakeRedis:
        def __init__(self) -> None:
            self.published: list[tuple[str, str]] = []

        async def publish(self, channel: str, payload: str) -> int:
            self.published.append((channel, payload))
            return 1

    async def fake_terminate_pod(pod_id: str) -> None:
        terminated_pods.append(pod_id)

    redis = FakeRedis()
    publish_stopped = AsyncMock()
    monkeypatch.setattr(teardown, "LeaseRepository", FakeLeaseRepository)
    monkeypatch.setattr(teardown, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(teardown, "CapabilityRepository", FakeCapabilityRepository)
    monkeypatch.setattr(teardown, "publish_lease_event", publish_stopped)
    monkeypatch.setattr(teardown, "terminate_pod", fake_terminate_pod)
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())

    result = await teardown.run_teardown(
        "lease-target",
        pool="pool",
        redis_client=redis,
        reason="operator requested",
        now=_TERMINATED_AT,
    )

    assert terminated_pods == ["pod-target"]
    assert state_updates == ["stopping"]
    assert close_kwargs == {
        "state": "stopped",
        "cost_accrued_usd": Decimal("1.200000"),
        "terminated_at": _TERMINATED_AT,
        "terminated_reason": "operator requested",
    }
    assert result.lease.state is LeaseState.STOPPED
    assert result.published_subscribers == 1
    assert len(redis.published) == 1

    channel, payload = redis.published[0]
    assert channel == teardown.LEASE_TERMINATED_CHANNEL
    assert json.loads(payload) == {
        "cost_accrued_usd": "1.200000",
        "event": "lease.terminated",
        "external_resource_id": "pod-target",
        "lease_id": "lease-target",
        "provider_id": "provider-target",
        "runpod_pod_id": "pod-target",
        "state": "stopped",
        "terminated_at": _TERMINATED_AT.isoformat(),
        "terminated_reason": "operator requested",
    }
    stopped_event = publish_stopped.await_args.args[2]
    assert stopped_event["event"] == "lease.stopped"
    assert stopped_event["data"] == {
        "capability": "llm.target",
        "lease_id": "lease-target",
        "reason": "operator",
    }


@pytest.mark.anyio
async def test_run_teardown_publishes_stopped_when_provider_row_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _lease()

    class LeaseRepo(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease:
            assert lease_id == current.id
            return current

        async def capability_name(self, lease_id: str) -> str:
            assert lease_id == current.id
            return "llm.persisted"

        async def update_state(self, lease_id: str, state: str) -> Lease:
            return current.model_copy(update={"state": state})

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease:
            return current.model_copy(update=changes)

    provider_repo = AsyncMock()
    provider_repo.get.return_value = None
    publish = AsyncMock()
    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LeaseRepo())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: provider_repo)
    monkeypatch.setattr(teardown, "terminate_pod", AsyncMock())
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())
    monkeypatch.setattr(teardown, "publish_lease_event", publish)

    await teardown.run_teardown(
        current.id,
        pool=object(),
        reason="budget",
        now=_TERMINATED_AT,
    )

    stopped = publish.await_args.args[2]
    assert stopped["capability"] == "llm.persisted"
    assert stopped["data"] == {
        "capability": "llm.persisted",
        "lease_id": current.id,
        "reason": "budget",
    }


@pytest.mark.anyio
async def test_run_teardown_omits_stopped_event_without_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _lease()

    class LeaseRepo(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease:
            assert lease_id == current.id
            return current

        async def capability_name(self, lease_id: str) -> str | None:
            assert lease_id == current.id
            return None

        async def update_state(self, lease_id: str, state: str) -> Lease:
            return current.model_copy(update={"state": state})

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease:
            return current.model_copy(update={**changes, "state": LeaseState(changes["state"])})

    provider_repo = AsyncMock()
    provider_repo.get.return_value = None
    publish = AsyncMock()
    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LeaseRepo())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: provider_repo)
    monkeypatch.setattr(teardown, "terminate_pod", AsyncMock())
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())
    monkeypatch.setattr(teardown, "publish_lease_event", publish)

    result = await teardown.run_teardown(
        current.id,
        pool=object(),
        now=_TERMINATED_AT,
    )

    assert result.lease.state is LeaseState.STOPPED
    publish.assert_not_awaited()


@pytest.mark.anyio
async def test_run_teardown_can_persist_expired_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated_pods: list[str] = []
    close_kwargs: dict[str, Any] = {}

    class FakeLeaseRepository(UnlockedTeardown):
        def __init__(self, pool: object) -> None:
            assert pool == "pool"

        async def get(self, lease_id: str) -> Lease:
            assert lease_id == "lease-target"
            return _lease()

        async def capability_name(self, lease_id: str) -> str:
            assert lease_id == "lease-target"
            return "llm.target"

        async def update_state(self, lease_id: str, state: str) -> Lease:
            assert lease_id == "lease-target"
            assert state == "stopping"
            return _lease(LeaseState.STOPPING)

        async def close_teardown(self, lease_id: str, **kwargs: Any) -> Lease:
            assert lease_id == "lease-target"
            close_kwargs.update(kwargs)
            return _lease(
                LeaseState(kwargs["state"]),
                cost_accrued_usd=kwargs["cost_accrued_usd"],
                terminated_at=kwargs["terminated_at"],
                terminated_reason=kwargs["terminated_reason"],
            )

    class FakeProviderRepository:
        def __init__(self, pool: object) -> None:
            assert pool == "pool"

        async def get(self, provider_id: str) -> Provider:
            assert provider_id == "provider-target"
            return _provider()

    class FakeRedis:
        def __init__(self) -> None:
            self.published: list[tuple[str, str]] = []

        async def publish(self, channel: str, payload: str) -> int:
            self.published.append((channel, payload))
            return 1

    async def fake_terminate_pod(pod_id: str) -> None:
        terminated_pods.append(pod_id)

    redis = FakeRedis()
    monkeypatch.setattr(teardown, "LeaseRepository", FakeLeaseRepository)
    monkeypatch.setattr(teardown, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(teardown, "terminate_pod", fake_terminate_pod)
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())

    result = await teardown.run_teardown(
        "lease-target",
        pool="pool",
        redis_client=redis,
        now=_TERMINATED_AT,
        terminal_state=LeaseState.EXPIRED,
    )

    assert terminated_pods == ["pod-target"]
    assert close_kwargs == {
        "state": "expired",
        "cost_accrued_usd": Decimal("1.200000"),
        "terminated_at": _TERMINATED_AT,
        "terminated_reason": "lease_expired",
    }
    assert result.lease.state is LeaseState.EXPIRED
    assert result.published_subscribers == 1

    channel, payload = redis.published[0]
    assert channel == teardown.LEASE_TERMINATED_CHANNEL
    event = json.loads(payload)
    assert event["event"] == "lease.terminated"
    assert event["state"] == "expired"
    assert event["terminated_reason"] == "lease_expired"
    assert event["cost_accrued_usd"] == "1.200000"


@pytest.mark.anyio
async def test_run_teardown_is_idempotent_for_terminal_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated_pods: list[str] = []
    stopped = _lease(
        LeaseState.STOPPED,
        cost_accrued_usd=Decimal("0.500000"),
        terminated_at=_TERMINATED_AT,
        terminated_reason="already stopped",
    )

    class FakeLeaseRepository(UnlockedTeardown):
        def __init__(self, _pool: object) -> None:
            pass

        async def get(self, lease_id: str) -> Lease:
            assert lease_id == "lease-target"
            return stopped

    class FakeProviderRepository:
        def __init__(self, _pool: object) -> None:
            pass

    async def fake_terminate_pod(pod_id: str) -> None:
        terminated_pods.append(pod_id)

    monkeypatch.setattr(teardown, "LeaseRepository", FakeLeaseRepository)
    monkeypatch.setattr(teardown, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(teardown, "terminate_pod", fake_terminate_pod)

    result = await teardown.run_teardown("lease-target", pool=object())

    assert result.lease is stopped
    assert result.event is None
    assert result.published_subscribers == 0
    assert terminated_pods == []


def test_close_lease_cost_ignores_accrued_cost_and_charges_the_fallback_without_a_rate() -> None:
    lease = _lease(cost_accrued_usd=Decimal("0.125000"))

    assert teardown.close_lease_cost(lease, provider=None, terminated_at=_TERMINATED_AT) == Decimal(
        "0.083333"
    )  # 0.50 USD/h x 10 min


def test_close_lease_cost_uses_max_usd_per_hour_without_provider() -> None:
    created_at = dt.datetime(2026, 5, 28, 11, 30, tzinfo=dt.UTC)
    terminated_at = dt.datetime(2026, 5, 28, 12, 0, tzinfo=dt.UTC)
    lease = _lease().model_copy(
        update={"created_at": created_at, "max_usd_per_hour": Decimal("0.50")}
    )

    assert teardown.close_lease_cost(lease, provider=None, terminated_at=terminated_at) == Decimal(
        "0.250000"
    )


def test_close_lease_cost_is_never_zero_without_provider_rate_or_max_usd_per_hour() -> None:
    lease = _lease(cost_accrued_usd=None)

    assert teardown.close_lease_cost(lease, provider=None, terminated_at=_TERMINATED_AT) == Decimal(
        "0.083333"
    )  # 0.50 USD/h x 10 min


@pytest.mark.anyio
@pytest.mark.parametrize(
    "state",
    [LeaseState.CREATING, LeaseState.WAITING_RUNTIME, LeaseState.WAITING_PROBE],
)
async def test_mark_stopping_accepts_pre_active_leases(state: LeaseState) -> None:
    """Marking a pre-active raw-pod lease stopping must not raise LeaseStateConflict."""

    class FakeLeaseRepository(UnlockedTeardown):
        async def update_state(self, lease_id: str, next_state: str) -> Lease:
            assert next_state == "stopping"
            return _lease(LeaseState.STOPPING)

    stopping = await teardown._mark_stopping(FakeLeaseRepository(), _lease(state=state))
    assert stopping.state is LeaseState.STOPPING


@pytest.mark.anyio
async def test_stop_route_delegates_to_scoped_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    stopped = _lease(
        LeaseState.STOPPED,
        cost_accrued_usd=Decimal("1.200000"),
        terminated_at=_TERMINATED_AT,
        terminated_reason="operator requested",
    )

    async def fake_run_teardown(
        lease_id: str,
        *,
        pool: object,
        redis_client: object | None,
        reason: str,
        terminated_reason: str | None,
    ) -> teardown.LeaseTeardownResult:
        calls.append(
            {
                "lease_id": lease_id,
                "pool": pool,
                "redis_client": redis_client,
                "reason": reason,
                "terminated_reason": terminated_reason,
            }
        )
        return teardown.LeaseTeardownResult(lease=stopped, event=None)

    monkeypatch.setattr(lease_routes, "run_teardown", fake_run_teardown)

    response = await lease_routes.stop_lease(
        "lease-target",
        body=LeaseStop(reason="operator requested"),
        pool="pool",
        redis_client="redis",
    )

    assert calls == [
        {
            "lease_id": "lease-target",
            "pool": "pool",
            "redis_client": "redis",
            "reason": "operator",
            "terminated_reason": "operator requested",
        }
    ]
    assert response["state"] == "stopped"
    assert response["terminated_at"] == _TERMINATED_AT.isoformat()
    assert response["terminated_reason"] == "operator requested"
    assert response["cost_accrued_usd"] == "1.200000"


@pytest.mark.anyio
async def test_disarm_serve_provider_clears_pod_facts_and_marks_unhealthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    patched: dict[str, Any] = {}
    audits: list[dict[str, Any]] = []

    class FakeProviderRepository:
        def __init__(self, pool: object) -> None:
            pass

        async def patch(self, provider_id: str, **kwargs: Any) -> Any:
            patched["provider_id"] = provider_id
            patched.update(kwargs)
            return None

    async def fake_insert_audit(pool: object, **kwargs: Any) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(teardown, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(teardown, "insert_audit", fake_insert_audit)
    provider = _provider()
    provider = provider.model_copy(
        update={
            "config": {
                **provider.config,
                "openai_proxy_port": 8000,
                "active_pod_id": "pod-target",
                "active_lease_id": "lease-target",
            }
        }
    )

    disarmed = await teardown.disarm_serve_provider(
        "pool", provider=provider, lease_id="lease-target"
    )

    assert disarmed is True
    assert patched["health_status"] == "disarmed"
    # Disarming is not a failure: the probe's failure counters are left alone (finding #2).
    assert not {"consecutive_failures", "cooldown_trips", "cooldown_until"} & patched.keys()
    assert "active_pod_id" not in patched["config"]
    assert "active_lease_id" not in patched["config"]
    assert patched["config"]["openai_proxy_port"] == 8000
    assert audits[0]["action"] == "lease_closed"


@pytest.mark.anyio
async def test_disarm_serve_provider_ignores_provider_armed_by_another_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExplodingProviderRepository:
        def __init__(self, pool: object) -> None:
            raise AssertionError("provider armed by another lease must not be touched")

    monkeypatch.setattr(teardown, "ProviderRepository", ExplodingProviderRepository)
    provider = _provider().model_copy(
        update={
            "config": {
                "openai_proxy_port": 8000,
                "active_pod_id": "pod-other",
                "active_lease_id": "lease-other",
            }
        }
    )
    assert (
        await teardown.disarm_serve_provider("pool", provider=provider, lease_id="lease-target")
        is False
    )
    assert (
        await teardown.disarm_serve_provider("pool", provider=None, lease_id="lease-target")
        is False
    )


@pytest.mark.anyio
async def test_run_teardown_disarms_serve_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    disarmed: list[str] = []

    class FakeLeaseRepository(UnlockedTeardown):
        def __init__(self, pool: object) -> None:
            pass

        async def get(self, lease_id: str) -> Lease:
            return _lease()

        async def capability_name(self, lease_id: str) -> str:
            return "llm.target"

        async def update_state(self, lease_id: str, state: str) -> Lease:
            return _lease(LeaseState(state))

        async def close_teardown(self, lease_id: str, **kwargs: Any) -> Lease:
            return _lease(
                LeaseState(kwargs["state"]),
                terminated_at=kwargs["terminated_at"],
                terminated_reason=kwargs["terminated_reason"],
                cost_accrued_usd=kwargs["cost_accrued_usd"],
            )

    class FakeProviderRepository:
        def __init__(self, pool: object) -> None:
            pass

        async def get(self, provider_id: str) -> Provider:
            return _provider()

    async def fake_terminate_pod(pod_id: str) -> None:
        pass

    async def fake_disarm(pool: object, *, provider: Any, lease_id: str) -> bool:
        disarmed.append(lease_id)
        return True

    monkeypatch.setattr(teardown, "LeaseRepository", FakeLeaseRepository)
    monkeypatch.setattr(teardown, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(teardown, "terminate_pod", fake_terminate_pod)
    monkeypatch.setattr(teardown, "disarm_serve_provider", fake_disarm)
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())

    await teardown.run_teardown("lease-target", pool="pool", now=_TERMINATED_AT)
    assert disarmed == ["lease-target"]


@pytest.mark.anyio
async def test_run_teardown_dispatches_exact_non_runpod_compute_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _lease().model_copy(update={"external_resource_id": "vast-55", "runpod_pod_id": None})
    provider = _provider().model_copy(
        update={
            "adapter_id": ProviderAdapterId.VAST,
            "credential_ref": "VAST_API_KEY",
        }
    )
    adapter_requests: list[TeardownRequest] = []

    class FakeLeaseRepository(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease:
            return current

        async def capability_name(self, lease_id: str) -> str:
            return "gpu.lease"

        async def update_state(self, lease_id: str, state: str) -> Lease:
            return current.model_copy(update={"state": LeaseState(state)})

        async def close_teardown(self, lease_id: str, **kwargs: Any) -> Lease:
            return current.model_copy(
                update={
                    "state": LeaseState(kwargs["state"]),
                    "cost_accrued_usd": kwargs["cost_accrued_usd"],
                    "terminated_at": kwargs["terminated_at"],
                    "terminated_reason": kwargs["terminated_reason"],
                }
            )

    class FakeProviderRepository:
        async def get(self, provider_id: str) -> Provider:
            assert provider_id == provider.id
            return provider

    class FakeAdapter:
        async def teardown(self, request: TeardownRequest) -> None:
            adapter_requests.append(request)

    class FakeRegistry:
        def lookup_compute(self, adapter_id: str) -> FakeAdapter:
            assert adapter_id == "vast"
            return FakeAdapter()

    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: FakeLeaseRepository())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: FakeProviderRepository())
    monkeypatch.setattr(teardown, "get_default_registry", lambda: FakeRegistry())
    runpod_terminate = AsyncMock(side_effect=AssertionError("RunPod must not be called"))
    monkeypatch.setattr(teardown, "terminate_pod", runpod_terminate)
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())
    monkeypatch.setattr(teardown, "disarm_serve_provider", AsyncMock(return_value=False))
    monkeypatch.setattr(teardown, "publish_lease_event", AsyncMock())

    result = await teardown.run_teardown(
        current.id,
        pool=object(),
        now=_TERMINATED_AT,
        reason="ttl",
        terminal_state=LeaseState.EXPIRED,
    )

    assert result.lease.state is LeaseState.EXPIRED
    assert len(adapter_requests) == 1
    request = adapter_requests[0]
    assert request.provider_record.adapter_id is ProviderAdapterId.VAST
    assert isinstance(request.credentials, CredentialReference)
    assert request.credentials.name == "VAST_API_KEY"
    assert request.lease_id == current.id
    assert request.reason == "ttl"
    assert request.terminal_state is LeaseState.EXPIRED
    runpod_terminate.assert_not_awaited()


@pytest.mark.anyio
async def test_unreachable_provider_teardown_is_typed_and_leaves_the_lease_stopping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.runpod_client.pods import RunPodError

    current = _lease()
    states: list[str] = []
    closed: list[str] = []

    class LeaseRepo(UnlockedTeardown):
        async def get(self, lease_id: str) -> Lease:
            return current

        async def update_state(self, lease_id: str, state: str) -> Lease:
            states.append(state)
            return current.model_copy(update={"state": state})

        async def close_teardown(self, lease_id: str, **changes: Any) -> Lease:
            closed.append(lease_id)
            return current.model_copy(update=changes)

        async def capability_name(self, lease_id: str) -> str:
            return "llm.target"

    class ProviderRepo:
        async def get(self, provider_id: str) -> Provider:
            return _provider()

    monkeypatch.setattr(teardown, "LeaseRepository", lambda _pool: LeaseRepo())
    monkeypatch.setattr(teardown, "ProviderRepository", lambda _pool: ProviderRepo())
    monkeypatch.setattr(
        teardown,
        "terminate_pod",
        AsyncMock(side_effect=RunPodError("terminate_pod(pod) failed: connection refused")),
    )

    with pytest.raises(teardown.TeardownFailed) as raised:
        await teardown.run_teardown("lease-target", pool=object(), now=_TERMINATED_AT)

    assert raised.value.error_code == "teardown_failed"
    assert raised.value.status_code == 502
    assert "connection refused" not in raised.value.to_response_body().get("detail", "")
    assert states == ["stopping"] and closed == [], "the lease stays stopping for a retry"


@pytest.mark.anyio
async def test_a_lease_closed_by_another_teardown_is_returned_without_a_second_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped = _lease(LeaseState.STOPPED, terminated_at=_TERMINATED_AT, terminated_reason="x")
    reads = iter([_lease(LeaseState.STOPPING), stopped])
    audits: list[object] = []

    class FakeLeaseRepository(UnlockedTeardown):
        def __init__(self, _pool: object) -> None:
            pass

        async def get(self, lease_id: str) -> Lease:
            return next(reads)

        async def capability_name(self, lease_id: str) -> str:
            return "llm.target"

        async def close_teardown(self, lease_id: str, **_changes: Any) -> None:
            return None  # the lease is no longer stopping: another teardown closed it

    class FakeProviderRepository:
        def __init__(self, _pool: object) -> None:
            pass

        async def get(self, provider_id: str) -> Provider:
            return _provider()

    async def fake_terminate_pod(pod_id: str) -> None:
        return None

    async def fake_audit(*args: object, **kwargs: object) -> None:
        audits.append(kwargs)

    monkeypatch.setattr(teardown, "LeaseRepository", FakeLeaseRepository)
    monkeypatch.setattr(teardown, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(teardown, "terminate_pod", fake_terminate_pod)
    monkeypatch.setattr(teardown, "insert_audit", fake_audit)

    result = await teardown.run_teardown("lease-target", pool=object())

    assert result.lease is stopped
    assert (result.event, result.published_subscribers) == (None, 0)
    assert audits == []


async def test_an_unreachable_redis_does_not_fail_a_completed_teardown() -> None:
    """The pod is gone and the lease closed before the publish; a Redis outage must not undo that."""
    from redis.exceptions import ConnectionError as RedisConnectionError

    class DownRedis:
        async def publish(self, channel: str, payload: str) -> int:
            raise RedisConnectionError("Error 111 connecting to localhost:6379")

    published = await teardown.publish_lease_terminated(DownRedis(), {"lease_id": "lease-x"})
    assert published == 0


def _patch_runpod_teardown(
    monkeypatch: pytest.MonkeyPatch, provider: Provider
) -> list[tuple[str, str | None]]:
    """Fake the repositories around ``run_teardown``; record each (pod, key) it terminates."""
    terminated: list[tuple[str, str | None]] = []

    class FakeLeaseRepository(UnlockedTeardown):
        def __init__(self, pool: object) -> None:
            pass

        async def get(self, lease_id: str) -> Lease:
            return _lease()

        async def capability_name(self, lease_id: str) -> str:
            return "llm.target"

        async def update_state(self, lease_id: str, state: str) -> Lease:
            return _lease(LeaseState(state))

        async def close_teardown(self, lease_id: str, **kwargs: Any) -> Lease:
            return _lease(
                LeaseState(kwargs["state"]),
                terminated_at=kwargs["terminated_at"],
                terminated_reason=kwargs["terminated_reason"],
                cost_accrued_usd=kwargs["cost_accrued_usd"],
            )

    class FakeProviderRepository:
        def __init__(self, pool: object) -> None:
            pass

        async def get(self, provider_id: str) -> Provider:
            return provider

    async def ambient_terminate(pod_id: str) -> None:
        terminated.append((pod_id, "ambient"))

    async def keyed_terminate(
        pod_id: str, *, api_key: str | None = None, rest_api_url: str | None = None
    ) -> None:
        terminated.append((pod_id, api_key))

    monkeypatch.setattr(teardown, "LeaseRepository", FakeLeaseRepository)
    monkeypatch.setattr(teardown, "ProviderRepository", FakeProviderRepository)
    monkeypatch.setattr(teardown, "terminate_pod", ambient_terminate)
    monkeypatch.setattr(teardown, "_terminate_pod", keyed_terminate)
    monkeypatch.setattr(teardown, "disarm_serve_provider", AsyncMock(return_value=False))
    monkeypatch.setattr(teardown, "insert_audit", AsyncMock())
    return terminated


@pytest.mark.anyio
async def test_run_teardown_terminates_with_the_providers_own_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", "ambient-key")
    monkeypatch.setenv("SECOND_RUNPOD_KEY", "second-account-key")
    provider = _provider().model_copy(update={"credential_ref": "SECOND_RUNPOD_KEY"})
    terminated = _patch_runpod_teardown(monkeypatch, provider)

    await teardown.run_teardown("lease-target", pool="pool", now=_TERMINATED_AT)

    assert terminated == [("pod-target", "second-account-key")]


@pytest.mark.anyio
async def test_run_teardown_explicit_key_overrides_the_providers_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SECOND_RUNPOD_KEY", "second-account-key")
    provider = _provider().model_copy(update={"credential_ref": "SECOND_RUNPOD_KEY"})
    terminated = _patch_runpod_teardown(monkeypatch, provider)

    await teardown.run_teardown(
        "lease-target", pool="pool", now=_TERMINATED_AT, api_key="explicit-key"
    )

    assert terminated == [("pod-target", "explicit-key")]


@pytest.mark.anyio
async def test_run_teardown_with_the_default_reference_keeps_the_process_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminated = _patch_runpod_teardown(monkeypatch, _provider())

    await teardown.run_teardown("lease-target", pool="pool", now=_TERMINATED_AT)

    assert terminated == [("pod-target", "ambient")]


@pytest.mark.anyio
async def test_run_teardown_with_an_unset_provider_credential_fails_typed_and_names_only_the_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SECOND_RUNPOD_KEY", raising=False)
    provider = _provider().model_copy(update={"credential_ref": "SECOND_RUNPOD_KEY"})
    terminated = _patch_runpod_teardown(monkeypatch, provider)

    with pytest.raises(teardown.TeardownFailed) as excinfo:
        await teardown.run_teardown("lease-target", pool="pool", now=_TERMINATED_AT)

    assert terminated == []
    assert isinstance(excinfo.value.__cause__, CredentialResolutionError)
    assert "SECOND_RUNPOD_KEY" in str(excinfo.value.__cause__)


class _LiveProviderStore:
    """One provider row behind a fake pool: reads see the live row, writes update it."""

    def __init__(self, config: dict[str, Any], health_status: str = "unknown") -> None:
        self.config = dict(config)
        self.health_status = health_status
        self.audits: list[dict[str, Any]] = []
        self.locked_reads = 0

    def pool(self) -> Any:
        store = self

        class _Conn:
            async def fetchrow(self, sql: str, provider_id: str) -> dict[str, Any]:
                assert "FOR UPDATE" in sql
                store.locked_reads += 1
                return {"config": dict(store.config), "health_status": store.health_status}

            def transaction(self) -> Any:
                return _Transaction()

        class _Transaction:
            async def __aenter__(self) -> None:
                return None

            async def __aexit__(self, *exc: object) -> None:
                return None

        class _Acquired:
            async def __aenter__(self) -> _Conn:
                return _Conn()

            async def __aexit__(self, *exc: object) -> None:
                return None

        class _Pool:
            def acquire(self) -> _Acquired:
                return _Acquired()

        return _Pool()

    def patch_repository(self, monkeypatch: pytest.MonkeyPatch, *modules: Any) -> None:
        store = self

        class _Repository:
            def __init__(self, pool: object) -> None:
                pass

            async def patch(self, provider_id: str, **kwargs: Any) -> None:
                store.config = dict(kwargs["config"])
                store.health_status = kwargs["health_status"]

        async def insert_audit(pool: object, **kwargs: Any) -> None:
            store.audits.append(kwargs)

        for module in modules:
            monkeypatch.setattr(module, "ProviderRepository", _Repository)
            monkeypatch.setattr(module, "insert_audit", insert_audit)


@pytest.mark.anyio
async def test_a_stale_teardown_snapshot_never_disarms_a_lease_armed_since(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.api.leases import launch

    store = _LiveProviderStore({"openai_proxy_port": 8000})
    store.patch_repository(monkeypatch, teardown, launch)
    pool = store.pool()
    provider = _provider().model_copy(update={"config": {"openai_proxy_port": 8000}})

    assert await launch.arm_serve_provider(
        pool, provider=provider, lease_id="lease-a", pod_id="pod-a"
    )
    stale_snapshot = provider.model_copy(update={"config": dict(store.config)})
    assert await launch.arm_serve_provider(
        pool, provider=provider, lease_id="lease-b", pod_id="pod-b"
    )
    store.audits.clear()

    disarmed = await teardown.disarm_serve_provider(
        pool, provider=stale_snapshot, lease_id="lease-a"
    )

    assert disarmed is False
    assert store.config["active_lease_id"] == "lease-b"
    assert store.config["active_pod_id"] == "pod-b"
    assert store.health_status == "healthy"
    assert store.audits == []


@pytest.mark.anyio
async def test_disarm_builds_its_config_and_audit_from_the_live_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall.api.leases import launch

    store = _LiveProviderStore(
        {
            "openai_proxy_port": 8000,
            "active_lease_id": "lease-a",
            "active_pod_id": "pod-a",
            "tuned_since_snapshot": True,
        },
        health_status="healthy",
    )
    store.patch_repository(monkeypatch, teardown, launch)
    stale_snapshot = _provider().model_copy(
        update={
            "config": {
                "openai_proxy_port": 8000,
                "active_lease_id": "lease-a",
                "active_pod_id": "pod-a",
            }
        }
    )

    assert await teardown.disarm_serve_provider(
        store.pool(), provider=stale_snapshot, lease_id="lease-a"
    )

    assert store.config == {"openai_proxy_port": 8000, "tuned_since_snapshot": True}
    assert store.health_status == "disarmed"
    assert store.audits[0]["old_value"]["health_status"] == "healthy"
    assert store.audits[0]["old_value"]["config"]["tuned_since_snapshot"] is True


@pytest.mark.anyio
async def test_arm_builds_its_config_from_the_live_row(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.api.leases import launch

    store = _LiveProviderStore(
        {"openai_proxy_port": 8000, "written_by_another_path": 7}, health_status="degraded"
    )
    store.patch_repository(monkeypatch, launch)
    stale_snapshot = _provider().model_copy(update={"config": {"openai_proxy_port": 8000}})

    assert await launch.arm_serve_provider(
        store.pool(), provider=stale_snapshot, lease_id="lease-a", pod_id="pod-a"
    )

    assert store.config == {
        "openai_proxy_port": 8000,
        "written_by_another_path": 7,
        "active_pod_id": "pod-a",
        "active_lease_id": "lease-a",
    }
    assert store.audits[0]["old_value"]["health_status"] == "degraded"


@pytest.mark.anyio
@pytest.mark.parametrize("snapshot_lease", [None, "lease-other"])
async def test_disarm_decides_on_the_live_row_not_a_snapshot_taken_before_arming(
    monkeypatch: pytest.MonkeyPatch, snapshot_lease: str | None
) -> None:
    from pitwall.api.leases import launch

    store = _LiveProviderStore(
        {"openai_proxy_port": 8000, "active_lease_id": "lease-a", "active_pod_id": "pod-a"},
        health_status="healthy",
    )
    store.patch_repository(monkeypatch, teardown, launch)
    snapshot_config: dict[str, Any] = {"openai_proxy_port": 8000}
    if snapshot_lease is not None:
        snapshot_config["active_lease_id"] = snapshot_lease
    snapshot = _provider().model_copy(update={"config": snapshot_config})

    assert await teardown.disarm_serve_provider(store.pool(), provider=snapshot, lease_id="lease-a")

    assert store.health_status == "disarmed"
    assert "active_lease_id" not in store.config
    assert [audit["action"] for audit in store.audits] == ["lease_closed"]


@pytest.mark.anyio
async def test_arm_decides_on_the_live_rows_proxy_port(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.api.leases import launch

    declared_since_snapshot = _LiveProviderStore({"openai_proxy_port": 8000})
    declared_since_snapshot.patch_repository(monkeypatch, launch)
    snapshot = _provider().model_copy(update={"config": {}})
    assert await launch.arm_serve_provider(
        declared_since_snapshot.pool(), provider=snapshot, lease_id="lease-a", pod_id="pod-a"
    )
    assert declared_since_snapshot.config["active_lease_id"] == "lease-a"

    dropped_since_snapshot = _LiveProviderStore({})
    dropped_since_snapshot.patch_repository(monkeypatch, launch)
    snapshot = _provider().model_copy(update={"config": {"openai_proxy_port": 8000}})
    assert not await launch.arm_serve_provider(
        dropped_since_snapshot.pool(), provider=snapshot, lease_id="lease-a", pod_id="pod-a"
    )
    assert dropped_since_snapshot.config == {}
    assert dropped_since_snapshot.audits == []
