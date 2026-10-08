from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.providers import ProviderOperationContext, ProvisionRequest
from pitwall.providers.provisioning import (
    LAUNCH_REQUEST_DIGEST_KEY,
    ProvisionReplayConflict,
    ProvisionReplayFailed,
    ProvisionReplayInProgress,
    load_provision_replay,
)
from pitwall.providers.vast import VastCredentials, VastProvider

NOW = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)


class FakeAcquire:
    def __init__(self, pool: FakePool) -> None:
        self._pool = pool

    async def __aenter__(self) -> FakePool:
        return self._pool

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object | None,
    ) -> None:
        return None


@dataclass
class FakePool:
    workload_row: dict[str, Any] | None = None
    fail_lease_insert: bool = False
    commands: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.commands.append((query, args))
        if "FROM pitwall.workloads" in query:
            return self.workload_row
        if "INSERT INTO pitwall.leases" in query and self.fail_lease_insert:
            raise RuntimeError("injected lease persistence failure")
        return {
            "id": args[0],
            "provider_id": "prov_vast",
            "external_resource_id": "55",
            "runpod_pod_id": None,
            "state": "creating",
            "created_at": NOW,
            "expires_at": NOW + dt.timedelta(hours=1),
            "renewal_policy": "manual",
            "auto_teardown_on_expiry": True,
            "endpoints": None,
            "readiness": None,
            "cost_accrued_usd": None,
            "last_health_at": None,
            "terminated_at": None,
            "terminated_reason": None,
        }

    async def execute(self, query: str, *args: Any) -> str:
        self.commands.append((query, args))
        return "UPDATE 1"


@dataclass
class BudgetGateFake:
    is_new: bool

    async def try_launch_admission(self, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(workload_id="wkl-vast", is_new=self.is_new)


def _request(
    pool: FakePool,
    budget: BudgetGateFake,
    *,
    idempotency_key: str = "idem-1",
    request_fingerprint: str | None = None,
) -> Any:
    capability = Capability(
        id="cap_gpu",
        name="gpu.lease",
        version="1",
        class_=CapabilityClass.GPU_LEASE,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        created_at=NOW,
        updated_at=NOW,
    )
    provider = ProviderRecord(
        id="prov_vast",
        capability_id=capability.id,
        name="vast-test",
        adapter_id=ProviderAdapterId.VAST,
        provider_type=ProviderType.POD_LEASE,
        config={
            "ask_id": 12,
            "create": {"image": "example/image:latest"},
            "cost": {"kind": "per_second", "price_per_hour": "0.36"},
        },
        priority=1,
        source=CapabilitySource.API,
        updated_at=NOW,
    )
    return ProvisionRequest(
        context=ProviderOperationContext(pool=pool, now=NOW),
        capability=capability,
        provider_record=provider,
        credentials=VastCredentials(api_key=SecretStr("vast-key")),
        budget_gate=budget,
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
    )


def _completed_row(**extra: Any) -> dict[str, Any]:
    return {
        "state": "completed",
        "result": {"external_id": "55", "lease_id": "lease-vast"},
        "capability_id": "cap_gpu",
        "provider_id": "prov_vast",
        "type": "vm_lease",
        **extra,
    }


@pytest.mark.anyio
async def test_completed_idempotent_replay_performs_zero_provider_calls() -> None:
    pool = FakePool(
        workload_row={
            "state": "completed",
            "result": {"external_id": "55", "lease_id": "lease-vast"},
            "capability_id": "cap_gpu",
            "provider_id": "prov_vast",
            "type": "vm_lease",
        }
    )
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    result = await VastProvider(transport=httpx.MockTransport(handler)).provision(
        _request(pool, BudgetGateFake(is_new=False))
    )

    assert result.external_id == "55"
    assert result.lease_id == "lease-vast"
    assert result.raw == {"workload_id": "wkl-vast", "idempotent_replay": True}
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mismatch",
    [{"provider_id": "prov_other"}, {"capability_id": "cap_other"}, {"type": "inference"}],
)
async def test_replay_of_a_different_request_is_refused_without_provider_calls(
    mismatch: dict[str, str],
) -> None:
    """Finding #14 class: the key's stored workload was returned whatever request replayed it."""
    pool = FakePool(
        workload_row={
            "state": "completed",
            "result": {"external_id": "55", "lease_id": "lease-vast"},
            "capability_id": "cap_gpu",
            "provider_id": "prov_vast",
            "type": "vm_lease",
            **mismatch,
        }
    )
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    with pytest.raises(ProvisionReplayConflict):
        await VastProvider(transport=httpx.MockTransport(handler)).provision(
            _request(pool, BudgetGateFake(is_new=False))
        )
    assert calls == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("state", "error"),
    [
        ("queued", ProvisionReplayInProgress),
        ("running", ProvisionReplayInProgress),
        ("failed", ProvisionReplayFailed),
    ],
)
async def test_non_completed_replay_fails_closed(state: str, error: type[Exception]) -> None:
    pool = FakePool(
        workload_row={
            "state": state,
            "result": None,
            "capability_id": "cap_gpu",
            "provider_id": "prov_vast",
            "type": "vm_lease",
        }
    )

    with pytest.raises(error):
        await load_provision_replay(
            pool,
            "wkl-vast",
            capability_id="cap_gpu",
            provider_id="prov_vast",
            request_fingerprint=None,
        )


@pytest.mark.anyio
async def test_persistence_failure_compensates_remote_and_releases_reservation() -> None:
    pool = FakePool(fail_lease_insert=True)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "PUT":
            return httpx.Response(200, json={"new_contract": 55})
        assert request.method == "DELETE"
        assert str(request.url) == "https://console.vast.ai/api/v0/instances/55/"
        return httpx.Response(204)

    with pytest.raises(RuntimeError, match="injected lease persistence"):
        await VastProvider(transport=httpx.MockTransport(handler)).provision(
            _request(pool, BudgetGateFake(is_new=True))
        )

    assert [request.method for request in requests] == ["PUT", "DELETE"]
    failure_updates = [query for query, _ in pool.commands if "state = 'failed'" in query]
    assert len(failure_updates) == 1
    assert "cost_actual_usd" in failure_updates[0]


@pytest.mark.anyio
async def test_ambiguous_launch_timeout_retains_cost_ceiling_for_reconciliation() -> None:
    pool = FakePool()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("ambiguous provider outcome", request=request)

    with pytest.raises(httpx.ReadTimeout):
        await VastProvider(transport=httpx.MockTransport(handler)).provision(
            _request(pool, BudgetGateFake(is_new=True))
        )

    failure_updates = [query for query, _ in pool.commands if "state = 'failed'" in query]
    assert len(failure_updates) == 1
    assert "cost_actual_usd" not in failure_updates[0]
    assert "ambiguous provider outcome" not in repr(pool.commands)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("stored_input", "caller_digest", "replays"),
    [
        ({LAUNCH_REQUEST_DIGEST_KEY: "digest-a"}, "digest-a", True),
        ({LAUNCH_REQUEST_DIGEST_KEY: "digest-a"}, "digest-b", False),
        ({LAUNCH_REQUEST_DIGEST_KEY: "digest-a"}, None, False),
        (None, None, True),
        ({}, None, True),
        (None, "digest-a", True),
        ({"other": 1}, "digest-a", True),
    ],
)
async def test_replay_requires_the_same_request_digest(
    stored_input: dict[str, Any] | None, caller_digest: str | None, replays: bool
) -> None:
    """A completed admission replays only the request it admitted; a legacy row (no stored digest) replays."""
    pool = FakePool(workload_row=_completed_row(input=stored_input))

    async def replay() -> Any:
        return await load_provision_replay(
            pool,
            "wkl-vast",
            capability_id="cap_gpu",
            provider_id="prov_vast",
            request_fingerprint=caller_digest,
        )

    if replays:
        assert (await replay()).external_id == "55"
    else:
        with pytest.raises(ProvisionReplayConflict):
            await replay()
    selected = next(query for query, _ in pool.commands if "FROM pitwall.workloads" in query)
    assert "input" in selected


@pytest.mark.anyio
async def test_vast_replay_of_the_same_request_returns_the_original_vm() -> None:
    pool = FakePool(workload_row=_completed_row(input={LAUNCH_REQUEST_DIGEST_KEY: "digest-a"}))
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    result = await VastProvider(transport=httpx.MockTransport(handler)).provision(
        _request(pool, BudgetGateFake(is_new=False), request_fingerprint="digest-a")
    )

    assert result.external_id == "55"
    assert calls == []


@pytest.mark.anyio
async def test_vast_key_reused_with_a_different_request_is_refused_without_egress() -> None:
    pool = FakePool(workload_row=_completed_row(input={LAUNCH_REQUEST_DIGEST_KEY: "digest-a"}))
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    with pytest.raises(ProvisionReplayConflict):
        await VastProvider(transport=httpx.MockTransport(handler)).provision(
            _request(pool, BudgetGateFake(is_new=False), request_fingerprint="digest-b")
        )
    assert calls == []
