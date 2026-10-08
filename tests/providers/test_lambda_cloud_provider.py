from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx
import pytest

from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, LeaseState, ProviderType
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import CostQuote, PerVmSecondPricing
from pitwall.providers import (
    ProviderOperationContext,
    ProvisionRequest,
    ReconcileRequest,
    ResourceStatus,
    StatusRequest,
    TeardownRequest,
    create_default_registry,
)
from pitwall.providers.lambda_cloud import (
    LambdaCloudCredentials,
    LambdaCloudProvider,
    LambdaCloudProviderError,
)
from pitwall.providers.provisioning import ProvisionReplayConflict

Handler = Callable[[httpx.Request], httpx.Response]


@dataclass
class LambdaHttpFake:
    responses: list[httpx.Response | Handler] = field(default_factory=list)
    requests: list[httpx.Request] = field(default_factory=list)

    def add(self, response: httpx.Response | Handler) -> None:
        self.responses.append(response)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            return httpx.Response(500, json={"error": "unexpected request"})
        response = self.responses.pop(0)
        if callable(response):
            return response(request)
        return response

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


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
    fetchrow_results: list[dict[str, Any] | None] = field(default_factory=list)
    commands: list[tuple[str, tuple[Any, ...]]] = field(default_factory=list)

    def acquire(self) -> FakeAcquire:
        return FakeAcquire(self)

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        self.commands.append((query, args))
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return {
            "id": args[0] if args else "lease-lambda",
            "provider_id": "prov_lambda_cloud_a10",
            "external_resource_id": "0920582c7ff041399e34823a0be62549",
            "runpod_pod_id": None,
            "state": "creating",
            "created_at": dt.datetime(2026, 6, 2, 12, 0, tzinfo=dt.UTC),
            "expires_at": dt.datetime(2026, 6, 2, 14, 0, tzinfo=dt.UTC),
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


def _capability() -> Capability:
    now = dt.datetime(2026, 6, 2, 12, 0, tzinfo=dt.UTC)
    return Capability(
        id="cap_gpu_lease",
        name="gpu.lease",
        version="1",
        class_=CapabilityClass.GPU_LEASE,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        created_at=now,
        updated_at=now,
    )


def _provider_record(config: dict[str, Any] | None = None) -> ProviderRecord:
    return ProviderRecord(
        id="prov_lambda_cloud_a10",
        capability_id="cap_gpu_lease",
        name="lambda-cloud-a10",
        provider_type=ProviderType.POD_LEASE,
        region="us-west-1",
        config=config
        or {
            "launch": {
                "region_name": "us-west-1",
                "instance_type_name": "gpu_1x_a10",
                "ssh_key_names": ["pitwall-ci"],
                "image": {"family": "lambda-stack"},
            },
            "lease_ttl_ms": 7_200_000,
            "cost": {"kind": "per_vm_second", "rate_per_second": "0.00016"},
        },
        priority=1,
        source=CapabilitySource.API,
        updated_at=dt.datetime(2026, 6, 2, 12, 0, tzinfo=dt.UTC),
    )


def _credentials() -> LambdaCloudCredentials:
    return LambdaCloudCredentials(api_key="lambda-test-key")


def _json(request: httpx.Request) -> dict[str, Any]:
    raw = request.content.decode("utf-8")
    return json.loads(raw, parse_float=Decimal)


def test_default_registry_contains_lambda_cloud_provider() -> None:
    registry = create_default_registry()

    provider = registry.lookup("lambda_cloud")

    assert isinstance(provider, LambdaCloudProvider)
    assert "lambda_cloud" in registry.ids
    assert registry.validate_credentials("lambda_cloud", {"api_key": "lambda-test-key"})


def test_lambda_cloud_credentials_reject_secret_bearing_base_url() -> None:
    registry = create_default_registry()

    with pytest.raises(Exception, match="lambda_api_url"):
        registry.validate_credentials(
            "lambda_cloud",
            {
                "api_key": "lambda-test-key",
                "lambda_api_url": "https://lambda-test-key@cloud.lambda.ai/api/v1",
            },
        )


def test_lambda_cloud_pricing_model_uses_per_vm_second_rate() -> None:
    pricing = LambdaCloudProvider().pricing_model(_capability(), _provider_record())

    assert isinstance(pricing, PerVmSecondPricing)
    assert pricing.rate_per_second == Decimal("0.00016")
    assert pricing.estimate(_capability(), {}) == Decimal("0.009600")
    assert pricing.upper_bound(_capability(), {}) == Decimal("0.009600")


@pytest.mark.anyio
async def test_lambda_cloud_provider_provision_launches_vm_with_header_auth() -> None:
    fake = LambdaHttpFake()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == ("https://cloud.lambda.ai/api/v1/instance-operations/launch")
        assert request.headers["Authorization"] == "Bearer lambda-test-key"
        assert "api_key" not in str(request.url)
        body = _json(request)
        assert body["region_name"] == "us-west-1"
        assert body["instance_type_name"] == "gpu_1x_a10"
        assert body["ssh_key_names"] == ["pitwall-ci"]
        assert body["name"] == "pitwall-prov_lambda_cloud_a10-req-123"
        assert "prompt" not in body
        return httpx.Response(
            200,
            json={"data": {"instance_ids": ["0920582c7ff041399e34823a0be62549"]}},
        )

    fake.add(handler)
    provider = LambdaCloudProvider(transport=fake.transport())

    result = await provider.provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=object()),
            capability=_capability(),
            provider_record=_provider_record(),
            credentials=_credentials(),
            request_id="req-123",
            payload={"prompt": "do not send to Lambda launch body"},
        )
    )

    assert result.provider_id == "prov_lambda_cloud_a10"
    assert result.external_id == "0920582c7ff041399e34823a0be62549"
    assert result.lease_id is None
    assert result.raw["data"]["instance_ids"] == ["0920582c7ff041399e34823a0be62549"]
    assert len(fake.requests) == 1


@pytest.mark.anyio
async def test_lambda_cloud_provider_provision_requires_external_id_after_successful_launch() -> (
    None
):
    fake = LambdaHttpFake([httpx.Response(200, json={"data": {"instance_ids": []}})])

    with pytest.raises(LambdaCloudProviderError, match="external id"):
        await LambdaCloudProvider(transport=fake.transport()).provision(
            ProvisionRequest(
                context=ProviderOperationContext(pool=object()),
                capability=_capability(),
                provider_record=_provider_record(),
                credentials=_credentials(),
                request_id="req-missing-id",
            )
        )

    assert len(fake.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("quantity", [2, 0, "2"])
async def test_lambda_cloud_refuses_quantity_other_than_one_before_admission(
    quantity: object,
) -> None:
    """One lease per external resource: a multi-VM launch would bill VMs no lease tears down."""

    class BudgetGateFake:
        admitted = False

        async def try_launch(self, **_: Any) -> str:
            self.admitted = True
            return "wkl-lambda"

    fake = LambdaHttpFake()
    budget = BudgetGateFake()

    with pytest.raises(LambdaCloudProviderError, match="quantity"):
        await LambdaCloudProvider(transport=fake.transport()).provision(
            ProvisionRequest(
                context=ProviderOperationContext(pool=object()),
                capability=_capability(),
                provider_record=_provider_record(),
                credentials=_credentials(),
                payload={"quantity": quantity},
                budget_gate=budget,
            )
        )

    assert fake.requests == []
    assert budget.admitted is False


@pytest.mark.anyio
async def test_lambda_cloud_accepts_an_explicit_quantity_of_one() -> None:
    fake = LambdaHttpFake([httpx.Response(200, json={"data": {"instance_ids": ["instance-1"]}})])

    result = await LambdaCloudProvider(transport=fake.transport()).provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=object()),
            capability=_capability(),
            provider_record=_provider_record(),
            credentials=_credentials(),
            payload={"quantity": 1},
        )
    )

    assert result.external_id == "instance-1"
    assert _json(fake.requests[0])["quantity"] == 1


@pytest.mark.anyio
async def test_one_odd_instance_id_never_prevents_terminating_the_others() -> None:
    fake = LambdaHttpFake(
        [
            httpx.Response(200, json={"data": {"instance_ids": ["vm-1", "bad/id", "vm-2"]}}),
            httpx.Response(200, json={"data": {"terminated_instances": []}}),
        ]
    )

    with pytest.raises(LambdaCloudProviderError, match="3 instances"):
        await LambdaCloudProvider(transport=fake.transport()).provision(
            ProvisionRequest(
                context=ProviderOperationContext(pool=FakePool()),
                capability=_capability(),
                provider_record=_provider_record(),
                credentials=_credentials(),
                payload={"quantity": 1},
            )
        )

    assert [request.url.path for request in fake.requests][-1].endswith("/terminate")
    assert _json(fake.requests[-1])["instance_ids"] == ["vm-1", "bad/id", "vm-2"]


@pytest.mark.anyio
async def test_lambda_cloud_terminates_every_instance_of_a_multi_id_response() -> None:
    """A provider response with two ids for quantity 1 is a contract violation: terminate both."""
    fake = LambdaHttpFake(
        [
            httpx.Response(200, json={"data": {"instance_ids": ["vm-1", "vm-2"]}}),
            httpx.Response(200, json={"data": {"terminated_instances": []}}),
        ]
    )
    pool = FakePool()

    with pytest.raises(LambdaCloudProviderError, match="2 instances"):
        await LambdaCloudProvider(transport=fake.transport()).provision(
            ProvisionRequest(
                context=ProviderOperationContext(pool=pool),
                capability=_capability(),
                provider_record=_provider_record(),
                credentials=_credentials(),
                payload={"quantity": 1},
            )
        )

    assert [request.url.path for request in fake.requests] == [
        "/api/v1/instance-operations/launch",
        "/api/v1/instance-operations/terminate",
    ]
    assert _json(fake.requests[1])["instance_ids"] == ["vm-1", "vm-2"]
    assert not any("INSERT INTO pitwall.leases" in query for query, _ in pool.commands)


def _replay_pool(stored_input: dict[str, Any] | None) -> FakePool:
    return FakePool(
        fetchrow_results=[
            {
                "state": "completed",
                "result": {"external_id": "vm-original", "lease_id": "lease-original"},
                "capability_id": "cap_gpu_lease",
                "provider_id": "prov_lambda_cloud_a10",
                "type": "vm_lease",
                "input": stored_input,
            }
        ]
    )


class _ReplayGate:
    async def try_launch_admission(self, **_: Any) -> Any:
        return type("Admission", (), {"workload_id": "wkl-lambda", "is_new": False})()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("caller_digest", "replays"), [("digest-a", True), ("digest-b", False), (None, False)]
)
async def test_lambda_cloud_replay_requires_the_same_request(
    caller_digest: str | None, replays: bool
) -> None:
    fake = LambdaHttpFake()
    request = ProvisionRequest(
        context=ProviderOperationContext(pool=_replay_pool({"launch_request_sha256": "digest-a"})),
        capability=_capability(),
        provider_record=_provider_record(),
        credentials=_credentials(),
        budget_gate=_ReplayGate(),
        idempotency_key="idem-1",
        request_fingerprint=caller_digest,
    )
    provider = LambdaCloudProvider(transport=fake.transport())

    if replays:
        assert (await provider.provision(request)).external_id == "vm-original"
    else:
        with pytest.raises(ProvisionReplayConflict):
            await provider.provision(request)
    assert fake.requests == []


@pytest.mark.anyio
async def test_lambda_cloud_provider_persists_only_generic_resource_id() -> None:
    external_id = "0920582c7ff041399e34823a0be62549"
    fake = LambdaHttpFake([httpx.Response(200, json={"data": {"instance_ids": [external_id]}})])
    pool = FakePool()

    result = await LambdaCloudProvider(transport=fake.transport()).provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=pool),
            capability=_capability(),
            provider_record=_provider_record(),
            credentials=_credentials(),
        )
    )

    insert_query, insert_args = next(
        command for command in pool.commands if "INSERT INTO pitwall.leases" in command[0]
    )
    assert "external_resource_id" in insert_query
    assert insert_args[2] is None
    assert insert_args[3] == external_id
    assert insert_args[4] is None
    assert result.lease_id is not None


@pytest.mark.anyio
async def test_lambda_cloud_provider_provision_dry_run_returns_launch_body() -> None:
    fake = LambdaHttpFake()

    result = await LambdaCloudProvider(transport=fake.transport()).provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=object()),
            capability=_capability(),
            provider_record=_provider_record(),
            credentials=_credentials(),
            request_id="dry",
            dry_run=True,
        )
    )

    assert result.provider_id == "prov_lambda_cloud_a10"
    assert result.external_id is None
    assert result.raw["backend"] == "lambda_cloud"
    assert result.raw["dry_run"] is True
    assert result.raw["launch"]["name"] == "pitwall-prov_lambda_cloud_a10-dry"
    assert fake.requests == []


@pytest.mark.anyio
async def test_lambda_cloud_launches_are_serialized_at_official_interval() -> None:
    now = 0.0
    sleeps: list[float] = []

    def monotonic() -> float:
        return now

    async def sleeper(seconds: float) -> None:
        nonlocal now
        sleeps.append(seconds)
        now += seconds

    fake = LambdaHttpFake(
        [
            httpx.Response(200, json={"data": {"instance_ids": ["instance-1"]}}),
            httpx.Response(200, json={"data": {"instance_ids": ["instance-2"]}}),
        ]
    )
    provider = LambdaCloudProvider(
        transport=fake.transport(),
        monotonic=monotonic,
        sleeper=sleeper,
    )
    request = ProvisionRequest(
        context=ProviderOperationContext(pool=object()),
        capability=_capability(),
        provider_record=_provider_record(),
        credentials=_credentials(),
    )

    first = await provider.provision(request)
    second = await provider.provision(request)

    assert first.external_id == "instance-1"
    assert second.external_id == "instance-2"
    assert sleeps == [12.0]
    assert len(fake.requests) == 2


@pytest.mark.anyio
async def test_lambda_cloud_budget_admission_persists_structured_quote() -> None:
    class BudgetGateFake:
        estimate_usd: object | None = None

        async def try_launch(self, **kwargs: Any) -> str:
            self.estimate_usd = kwargs["estimate_usd"]
            return "wkl-lambda"

    fake = LambdaHttpFake([httpx.Response(200, json={"data": {"instance_ids": ["instance-1"]}})])
    budget = BudgetGateFake()

    await LambdaCloudProvider(transport=fake.transport()).provision(
        ProvisionRequest(
            context=ProviderOperationContext(pool=object()),
            capability=_capability(),
            provider_record=_provider_record(),
            credentials=_credentials(),
            budget_gate=budget,
        )
    )

    # A lease bills until teardown: the quote covers the 2 h lease TTL, not a 60 s timeout.
    assert isinstance(budget.estimate_usd, CostQuote)
    assert budget.estimate_usd.estimate() == Decimal("1.152000")
    assert budget.estimate_usd.upper_bound() == Decimal("1.152000")


@pytest.mark.anyio
async def test_lambda_cloud_provider_status_maps_active_instance() -> None:
    fake = LambdaHttpFake(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "id": "0920582c7ff041399e34823a0be62549",
                        "status": "active",
                        "ip": "203.0.113.10",
                    }
                },
            )
        ]
    )

    status = await LambdaCloudProvider(transport=fake.transport()).status(
        StatusRequest(
            context=ProviderOperationContext(pool=object()),
            provider_record=_provider_record(),
            credentials=_credentials(),
            external_id="0920582c7ff041399e34823a0be62549",
        )
    )

    assert status.status is ResourceStatus.RUNNING
    assert status.external_id == "0920582c7ff041399e34823a0be62549"
    assert status.raw["status"] == "active"


@pytest.mark.anyio
async def test_lambda_cloud_provider_status_maps_missing_instance_to_terminated() -> None:
    fake = LambdaHttpFake([httpx.Response(404, json={"error": {"code": "missing"}})])

    status = await LambdaCloudProvider(transport=fake.transport()).status(
        StatusRequest(
            context=ProviderOperationContext(pool=object()),
            provider_record=_provider_record(),
            credentials=_credentials(),
            external_id="0920582c7ff041399e34823a0be62549",
        )
    )

    assert status.status is ResourceStatus.TERMINATED
    assert status.raw == {}


@pytest.mark.anyio
async def test_lambda_cloud_provider_reconcile_marks_preempted_leases_failed() -> None:
    fake = LambdaHttpFake(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "id": "0920582c7ff041399e34823a0be62549",
                        "status": "preempted",
                    }
                },
            )
        ]
    )
    pool = FakePool()

    result = await LambdaCloudProvider(transport=fake.transport()).reconcile(
        ReconcileRequest(
            context=ProviderOperationContext(
                pool=pool, now=dt.datetime(2026, 6, 2, 12, 5, tzinfo=dt.UTC)
            ),
            provider_record=_provider_record(),
            credentials=_credentials(),
            external_ids=("0920582c7ff041399e34823a0be62549",),
        )
    )

    assert result.checked == 1
    assert result.updated == 1
    assert result.raw["resources"][0]["pitwall_preempted"] is True
    assert any("terminated_reason" in command[0] for command in pool.commands)
    assert any(LeaseState.FAILED.value in command[1] for command in pool.commands)


@pytest.mark.anyio
async def test_lambda_cloud_provider_teardown_terminates_instance_and_closes_lease() -> None:
    fake = LambdaHttpFake()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == ("https://cloud.lambda.ai/api/v1/instance-operations/terminate")
        assert request.headers["Authorization"] == "Bearer lambda-test-key"
        assert _json(request) == {"instance_ids": ["0920582c7ff041399e34823a0be62549"]}
        return httpx.Response(
            200,
            json={
                "data": {
                    "terminated_instances": [
                        {
                            "id": "0920582c7ff041399e34823a0be62549",
                            "status": "terminated",
                        }
                    ]
                }
            },
        )

    fake.add(handler)
    pool = FakePool()

    result = await LambdaCloudProvider(transport=fake.transport()).teardown(
        TeardownRequest(
            context=ProviderOperationContext(
                pool=pool, now=dt.datetime(2026, 6, 2, 12, 10, tzinfo=dt.UTC)
            ),
            provider_record=_provider_record(),
            credentials=_credentials(),
            lease_id="lease-lambda-1",
            reason="operator_stop",
            terminal_state=LeaseState.STOPPED,
        )
    )

    assert result.provider_id == "prov_lambda_cloud_a10"
    assert result.lease_id == "lease-lambda-1"
    assert result.external_id == "0920582c7ff041399e34823a0be62549"
    assert result.raw["data"]["terminated_instances"][0]["status"] == "terminated"
    assert any("terminated_reason" in command[0] for command in pool.commands)


@pytest.mark.anyio
async def test_lambda_cloud_provider_teardown_fails_closed_without_lease_external_id_mapping() -> (
    None
):
    fake = LambdaHttpFake()
    pool = FakePool(fetchrow_results=[None])

    with pytest.raises(LambdaCloudProviderError, match="external id"):
        await LambdaCloudProvider(transport=fake.transport()).teardown(
            TeardownRequest(
                context=ProviderOperationContext(pool=pool),
                provider_record=_provider_record(),
                credentials=_credentials(),
                lease_id="lease-without-mapping",
                reason="operator_stop",
                terminal_state=LeaseState.STOPPED,
            )
        )

    assert fake.requests == []
    assert not any("terminated_reason" in command[0] for command in pool.commands)
