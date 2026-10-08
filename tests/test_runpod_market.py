"""Cached RunPod market product service and COST truth-up tests."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st

from pitwall.cost.reconcile_cost import (
    CostReconcileAdjustment,
    CostReconcilePlan,
    CostReconcileWindow,
    ProviderActualWorkloadCost,
)
from pitwall.models.prices import gpu_price_snapshot_from_market
from pitwall.runpod_client.billing import RunPodBillingClient
from pitwall.runpod_client.catalog import RunPodCatalogV2Client
from pitwall.runpod_client.discovery import GpuDiscoveryService, _build_snapshot
from pitwall.runpod_client.graphql import RunpodDatacenter, RunpodGpuType, RunpodGraphQLClient
from pitwall.runpod_market import (
    RunpodActualCostReference,
    RunpodMarketService,
)

pytestmark = pytest.mark.anyio
_FIXTURE = Path(__file__).parent / "fixtures" / "runpod_market_contract_2026-09-01.json"
_START = dt.date(2026, 8, 30)
_END = dt.date(2026, 9, 1)


def _fixture() -> dict[str, Any]:
    return json.loads(_FIXTURE.read_text(), parse_float=Decimal)


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, default=str, separators=(",", ":")).encode()


@dataclass
class _Clock:
    wall: dt.datetime = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)
    monotonic: float = 1000.0

    def wall_now(self) -> dt.datetime:
        return self.wall

    def monotonic_now(self) -> float:
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.wall += dt.timedelta(seconds=seconds)
        self.monotonic += seconds


class _RecordedTransports:
    def __init__(self) -> None:
        self.graphql_requests: list[httpx.Request] = []
        self.catalogue_requests: list[httpx.Request] = []
        self.billing_requests: list[httpx.Request] = []
        self.fail_graphql = False
        self.fail_catalogue = False
        self.billing_payload: object = _fixture()["podBilling"]

    def graphql(self, request: httpx.Request) -> httpx.Response:
        self.graphql_requests.append(request)
        if self.fail_graphql:
            return httpx.Response(
                200,
                json={"errors": [{"message": "Authorization: Bearer rpa_secret_canary"}]},
            )
        body = json.loads(request.content)
        query = body["query"]
        fixture = _fixture()
        if "pitwallGpuTypes" in query:
            payload = fixture["graphqlGpuTypes"]
        elif "pitwallDatacenters" in query:
            payload = fixture["graphqlDatacenters"]
        elif "pitwallCreditsBalance" in query:
            payload = fixture["graphqlBalance"]
        else:
            raise AssertionError("unexpected GraphQL query")
        return httpx.Response(200, content=_json_bytes(payload))

    def catalogue(self, request: httpx.Request) -> httpx.Response:
        self.catalogue_requests.append(request)
        if self.fail_catalogue:
            return httpx.Response(503, text="Bearer rpa_secret_canary")
        return httpx.Response(200, content=_json_bytes(_fixture()["restV2Catalogue"]))

    def billing(self, request: httpx.Request) -> httpx.Response:
        self.billing_requests.append(request)
        return httpx.Response(200, content=_json_bytes(self.billing_payload))


def _service(
    transports: _RecordedTransports,
    clock: _Clock,
    *,
    ttl_s: float = 60.0,
) -> RunpodMarketService:
    graphql = RunpodGraphQLClient(
        api_key="test-key",  # pragma: allowlist secret
        graphql_url="https://graphql.test/query",
        transport=httpx.MockTransport(transports.graphql),
    )
    discovery = GpuDiscoveryService(graphql, ttl_s=ttl_s)
    catalogue = RunPodCatalogV2Client(
        api_key="test-key",  # pragma: allowlist secret
        rest_api_url="https://catalogue.test/v2",
        transport=httpx.MockTransport(transports.catalogue),
    )
    billing = RunPodBillingClient(
        api_key="test-key",  # pragma: allowlist secret
        rest_v1_api_url="https://billing.test/v1",
        transport=httpx.MockTransport(transports.billing),
    )
    return RunpodMarketService(
        discovery,
        catalogue,
        billing,
        cache_ttl_s=ttl_s,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
    )


@pytest.mark.property
@given(st.lists(st.from_regex(r"GPU-[A-Z]{1,4}", fullmatch=True), unique=True, max_size=20))
def test_discovery_normalization_order_is_deterministic(gpu_ids: list[str]) -> None:
    forward = _build_snapshot(
        [RunpodGpuType(id=value) for value in gpu_ids],
        [RunpodDatacenter(id=f"DC-{index:02d}") for index in range(len(gpu_ids))],
    )
    reverse = _build_snapshot(
        [RunpodGpuType(id=value) for value in reversed(gpu_ids)],
        [RunpodDatacenter(id=f"DC-{index:02d}") for index in reversed(range(len(gpu_ids)))],
    )

    assert forward.gpus == reverse.gpus
    assert forward.datacenters == reverse.datacenters
    assert [item.gpu_type_id for item in forward.gpus] == sorted(gpu_ids)


@pytest.mark.parametrize("ttl_s", [-1.0, float("inf"), float("nan")])
def test_cache_ttl_rejects_negative_or_non_finite_values(ttl_s: float) -> None:
    with pytest.raises(ValueError, match="finite and non-negative"):
        RunpodMarketService(None, None, None, cache_ttl_s=ttl_s)


async def test_snapshot_combines_sources_without_conflation_and_preserves_decimals() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    service = _service(transports, clock)

    snapshot = await service.read()
    serialized = snapshot.to_serializable_dict()
    await service.aclose()

    assert snapshot.state == "fresh"
    assert [item.gpu_type_id for item in snapshot.gpus] == [
        "NVIDIA A100 80GB PCIe",
        "NVIDIA GeForce RTX 4090",
        "NVIDIA L4",
    ]
    l4 = snapshot.gpus[2]
    assert l4.graphql is not None
    assert l4.rest_v2 is not None
    assert l4.graphql.secure_price == Decimal("0.440000000000000001")
    assert l4.graphql.lowest_bid_price == Decimal("0.190000000000000001")
    assert l4.rest_v2.pool == "ADA_24"
    assert l4.rest_v2.cuda_versions == (("12.4", False), ("12.8", True))
    assert (
        serialized["gpus"][2]["graphql"]["on_demand_price_usd_per_gpu_hour"]["secure"]
        == "0.440000000000000001"
    )
    assert serialized["balance"]["client_balance_usd"] == "42.250000000000000001"
    assert "account-not-for-public-output" not in json.dumps(serialized)
    assert [row["datacenter_id"] for row in serialized["availability"]] == [
        "EU-SE-1",
        "US-KS-2",
    ]
    assert len(transports.graphql_requests) == 3
    assert len(transports.catalogue_requests) == 1


async def test_cache_hit_forced_refresh_and_expiry_are_single_flight() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    service = _service(transports, clock, ttl_s=10)

    first = await service.read()
    hit = await service.read()
    forced_results = await asyncio.gather(*(service.read(force_refresh=True) for _ in range(12)))
    clock.advance(11)
    expired_results = await asyncio.gather(*(service.read() for _ in range(12)))
    await service.aclose()

    assert not first.cache_hit
    assert hit.cache_hit
    assert sum(not item.cache_hit for item in forced_results) == 1
    assert all(item.forced_refresh for item in forced_results)
    assert sum(not item.cache_hit for item in expired_results) == 1
    assert len(transports.graphql_requests) == 9
    assert len(transports.catalogue_requests) == 3


async def test_zero_ttl_concurrent_reads_still_share_one_refresh() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    service = _service(transports, clock, ttl_s=0)

    results = await asyncio.gather(*(service.read() for _ in range(16)))
    await service.aclose()

    assert sum(not item.cache_hit for item in results) == 1
    assert len(transports.graphql_requests) == 3
    assert len(transports.catalogue_requests) == 1


async def test_failed_refresh_retains_stale_data_with_safe_component_errors() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    service = _service(transports, clock, ttl_s=10)

    fresh = await service.read()
    clock.advance(11)
    transports.fail_graphql = True
    transports.fail_catalogue = True
    stale = await service.read()
    serialized = stale.to_serializable_dict()
    await service.aclose()

    assert fresh.state == "fresh"
    assert stale.state == "stale"
    assert stale.age_seconds == 11
    assert len(stale.gpus) == 3
    assert {item.state for item in stale.components} == {"stale"}
    assert {item.unavailable_reason for item in stale.components} == {"provider_unavailable"}
    assert "rpa_secret_canary" not in json.dumps(serialized)


async def test_partial_and_unconfigured_snapshots_are_explicit() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    transports.fail_catalogue = True
    partial_service = _service(transports, clock)
    partial = await partial_service.read()
    await partial_service.aclose()

    unavailable_service = RunpodMarketService(
        None,
        None,
        None,
        wall_clock=clock.wall_now,
        monotonic_clock=clock.monotonic_now,
    )
    unavailable = await unavailable_service.read()

    assert partial.state == "partial"
    assert len(partial.gpus) == 2
    assert partial.components[1].state == "unavailable"
    assert unavailable.state == "unavailable"
    assert unavailable.gpus == ()
    assert unavailable.balance is None
    assert {item.unavailable_reason for item in unavailable.components} == {"not_configured"}
    assert unavailable.billing_categories[0].actual_state == "unavailable"


async def test_shared_snapshot_feeds_fit_and_routing_while_static_fallback_survives() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    service = _service(transports, clock)
    snapshot = await service.read()
    await service.aclose()

    fit = gpu_price_snapshot_from_market(snapshot, cloud="secure")
    availability = snapshot.to_availability_snapshot()
    empty_service = RunpodMarketService(None, None, None, wall_clock=clock.wall_now)
    unavailable = await empty_service.read()
    fallback = gpu_price_snapshot_from_market(unavailable, cloud="secure")

    assert fit.source == "live"
    assert [item.id for item in fit.gpu_types] == [
        "NVIDIA A100 80GB PCIe",
        "NVIDIA L4",
    ]
    assert availability.is_available("US-KS-2", "NVIDIA L4", "SECURE", 1) is True
    assert availability.is_available("EU-SE-1", "NVIDIA L4", "COMMUNITY", 4) is False
    assert fallback.source == "fallback"
    assert fallback.gpu_types
    assert all(item.secure_price is None for item in fallback.gpu_types)


async def test_exact_pod_billing_maps_to_decimal_provider_actual() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    service = _service(transports, clock)

    actual = await service.provider_actual_cost(
        provider_id="provider-runpod-1",
        start_day=_START,
        end_day=_END,
        references=(
            RunpodActualCostReference(
                workload_id="workload-1",
                category="pods",
                external_resource_id="pod-exact-1",
            ),
        ),
    )
    await service.aclose()

    assert actual.availability == "available"
    # COST's durable public actual contract normalizes USD to six decimal places.
    assert actual.workloads[0].actual_usd == Decimal("0.323457")
    assert actual.workloads[0].source == "runpod-rest-v1-pod-billing"
    assert transports.billing_requests[0].url.params["podId"] == "pod-exact-1"


async def test_empty_or_non_pod_billing_never_becomes_zero_actual() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    transports.billing_payload = []
    service = _service(transports, clock)
    pod_ref = RunpodActualCostReference("workload-1", "pods", "pod-exact-1")

    empty = await service.provider_actual_cost(
        provider_id="provider-runpod-1",
        start_day=_START,
        end_day=_END,
        references=(pod_ref,),
    )
    endpoint = await service.provider_actual_cost(
        provider_id="provider-runpod-1",
        start_day=_START,
        end_day=_END,
        references=(RunpodActualCostReference("workload-2", "endpoints", "endpoint-1"),),
    )
    await service.aclose()

    assert empty.availability == "unavailable"
    assert empty.workloads == ()
    assert "no authoritative rows" in (empty.unavailable_reason or "")
    assert endpoint.availability == "unavailable"
    assert "only podId-grouped" in (endpoint.unavailable_reason or "")
    assert len(transports.billing_requests) == 1


@pytest.mark.parametrize(
    "billing_payload",
    [
        [
            {
                "amount": "1.00",
                "podId": "different-pod",
                "time": "2026-08-30T00:00:00Z",
            }
        ],
        [
            {
                "amount": "1.00",
                "podId": "pod-exact-1",
                "time": "2026-09-01T00:00:00Z",
            }
        ],
        [
            {
                "amount": "1.00",
                "podId": "pod-exact-1",
                "time": "2026-08-30T00:00:00Z",
            },
            {
                "amount": "2.00",
                "podId": "pod-exact-1",
                "time": "2026-08-30T00:00:00Z",
            },
        ],
    ],
)
async def test_mismatched_out_of_window_or_duplicate_buckets_are_unavailable(
    billing_payload: object,
) -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    transports.billing_payload = billing_payload
    service = _service(transports, clock)

    actual = await service.provider_actual_cost(
        provider_id="provider-runpod-1",
        start_day=_START,
        end_day=_END,
        references=(RunpodActualCostReference("workload-1", "pods", "pod-exact-1"),),
    )
    await service.aclose()

    assert actual.availability == "unavailable"
    assert actual.workloads == ()


async def test_duplicate_pod_mapping_is_rejected_before_provider_read() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    service = _service(transports, clock)

    actual = await service.provider_actual_cost(
        provider_id="provider-runpod-1",
        start_day=_START,
        end_day=_END,
        references=(
            RunpodActualCostReference("workload-1", "pods", "pod-exact-1"),
            RunpodActualCostReference("workload-2", "pods", "pod-exact-1"),
        ),
    )
    await service.aclose()

    assert actual.availability == "unavailable"
    assert "cannot map to multiple" in (actual.unavailable_reason or "")
    assert transports.billing_requests == []


class _MemoryTruthUpRepository:
    def __init__(self) -> None:
        self.amount = Decimal("0")
        self.source: str | None = None
        self.calls = 0

    async def truth_up(
        self,
        *,
        provider_id: str,
        observed_at: dt.datetime,
        start_day: dt.date,
        end_day: dt.date,
        provider_actuals: Iterable[ProviderActualWorkloadCost],
        tolerance_usd: Decimal,
    ) -> tuple[CostReconcilePlan, int]:
        del observed_at, tolerance_usd
        self.calls += 1
        actual = tuple(provider_actuals)[0]
        if actual.actual_usd == self.amount and actual.source == self.source:
            return CostReconcilePlan(adjustments=(), window_count=1), 0
        adjustment = CostReconcileAdjustment(
            window=CostReconcileWindow(start_day, "llm", "pod_lease"),
            workload_id=actual.workload_id,
            recorded_usd=self.amount,
            provider_actual_usd=actual.actual_usd,
            adjustment_usd=actual.actual_usd - self.amount,
            sources=(actual.source,),
        )
        self.amount = actual.actual_usd
        self.source = actual.source
        assert provider_id == "provider-runpod-1"
        assert end_day == _END
        return CostReconcilePlan(adjustments=(adjustment,), window_count=1), 1


async def test_provider_actual_reconciliation_replay_is_idempotent() -> None:
    clock = _Clock()
    transports = _RecordedTransports()
    service = _service(transports, clock)
    repository = _MemoryTruthUpRepository()
    reference = RunpodActualCostReference("workload-1", "pods", "pod-exact-1")

    first = await service.reconcile_provider_actual_cost(
        repository,
        provider_id="provider-runpod-1",
        start_day=_START,
        end_day=_END,
        references=(reference,),
    )
    replay = await service.reconcile_provider_actual_cost(
        repository,
        provider_id="provider-runpod-1",
        start_day=_START,
        end_day=_END,
        references=(reference,),
    )
    await service.aclose()

    assert first.status == "reconciled"
    assert first.applied_count == 1
    assert replay.status == "in_sync"
    assert replay.idempotent_noop
    assert repository.calls == 2
    assert repository.amount == Decimal("0.323457")
