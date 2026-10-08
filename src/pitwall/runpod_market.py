"""Cached product read for RunPod catalogue, market, balance, and billing.

This is the one semantic service consumed by the REST, MCP, CLI, TUI, model
fit, and routing adapters.  It composes the existing GraphQL discovery and
REST v2 catalogue without merging fields whose provider meanings differ.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Literal

import asyncpg
import httpx

from pitwall.config import PitwallSettings, load_settings_from_env
from pitwall.cost.billing_read import BillingSnapshot
from pitwall.cost.reconcile_cost import (
    CostTruthUpRepository,
    CostTruthUpResult,
    ProviderActualCostResult,
    ProviderActualWorkloadCost,
    reconcile_provider_actual_cost,
)
from pitwall.routing.context import AvailabilitySnapshot
from pitwall.runpod_client.billing import RunPodBillingClient, RunPodBillingRecord
from pitwall.runpod_client.catalog import GpuTypeV2, RunPodCatalogV2Client
from pitwall.runpod_client.discovery import (
    DatacenterCatalogEntry,
    GpuCatalogEntry,
    GpuDiscoveryService,
    GpuDiscoverySnapshot,
)
from pitwall.runpod_client.graphql import RunpodGpuType, RunpodGraphQLClient

DEFAULT_RUNPOD_MARKET_CACHE_TTL_S = 300.0
MAX_ACTUAL_WORKLOADS_PER_READ = 100
RUNPOD_POD_ACTUAL_COST_SOURCE = "runpod-rest-v1-pod-billing"
_TERMINAL_WORKLOAD_STATES = ("completed", "failed", "cancelled", "timed_out")
_TERMINAL_LEASE_STATES = ("stopped", "failed", "expired")

type ComponentState = Literal["fresh", "stale", "unavailable"]
type MarketState = Literal["fresh", "partial", "stale", "unavailable"]
type RunpodBillingCategory = Literal["pods", "endpoints", "network_volumes"]


@dataclass(frozen=True, slots=True)
class RunpodMarketComponent:
    """Freshness and safe failure metadata for one provider read."""

    name: Literal["graphql_discovery", "rest_v2_catalogue", "graphql_balance"]
    source: str
    state: ComponentState
    observed_at: dt.datetime | None = None
    unavailable_reason: str | None = None

    def to_serializable_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "source": self.source,
            "state": self.state,
            "observed_at": _timestamp(self.observed_at),
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class RunpodRestGpuCatalogue:
    """REST v2-only fields retained under explicit REST provenance."""

    memory_gb: int
    pool: str | None
    cuda_versions: tuple[tuple[str, bool], ...]

    def to_serializable_dict(self) -> dict[str, object]:
        return {
            "memory_gb": self.memory_gb,
            "pool": self.pool,
            "cuda_versions": [
                {"version": version, "available": available}
                for version, available in self.cuda_versions
            ],
        }


@dataclass(frozen=True, slots=True)
class RunpodMarketGpu:
    """One exact GPU id with GraphQL and REST fields kept separate."""

    gpu_type_id: str
    graphql: GpuCatalogEntry | None = None
    rest_v2: RunpodRestGpuCatalogue | None = None

    def to_serializable_dict(self) -> dict[str, object]:
        return {
            "gpu_type_id": self.gpu_type_id,
            "graphql": _graphql_gpu_dict(self.graphql),
            "rest_v2": self.rest_v2.to_serializable_dict() if self.rest_v2 else None,
        }


@dataclass(frozen=True, slots=True)
class RunpodCreditBalance:
    """Account credit fields with no account identifier or credential data."""

    client_balance_usd: Decimal
    current_spend_per_hr_usd: Decimal | None
    spend_limit_usd: Decimal | None
    min_balance_usd: Decimal | None
    under_balance: bool

    @classmethod
    def from_billing_snapshot(cls, snapshot: BillingSnapshot) -> RunpodCreditBalance:
        return cls(
            client_balance_usd=snapshot.client_balance_usd,
            current_spend_per_hr_usd=snapshot.current_spend_per_hr_usd,
            spend_limit_usd=snapshot.spend_limit_usd,
            min_balance_usd=snapshot.min_balance_usd,
            under_balance=snapshot.under_balance,
        )

    def to_serializable_dict(self) -> dict[str, object]:
        return {
            "currency": "USD",
            "client_balance_usd": str(self.client_balance_usd),
            "current_spend_per_hr_usd": _money(self.current_spend_per_hr_usd),
            "spend_limit_usd": _money(self.spend_limit_usd),
            "min_balance_usd": _money(self.min_balance_usd),
            "under_balance": self.under_balance,
        }


@dataclass(frozen=True, slots=True)
class RunpodBillingCategoryState:
    """Support boundary for one official RunPod billing category."""

    category: RunpodBillingCategory
    history_read_supported: bool
    workload_actual_supported: bool
    identity_field: str | None
    actual_state: Literal["supported", "unavailable"]
    unavailable_reason: str | None = None

    def to_serializable_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "history_read_supported": self.history_read_supported,
            "workload_actual_supported": self.workload_actual_supported,
            "identity_field": self.identity_field,
            "actual_state": self.actual_state,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class RunpodMarketRead:
    """Shared deterministic read returned to every product surface."""

    state: MarketState
    refresh_attempted_at: dt.datetime
    cache_expires_at: dt.datetime
    age_seconds: int
    cache_hit: bool
    forced_refresh: bool
    components: tuple[RunpodMarketComponent, ...]
    gpus: tuple[RunpodMarketGpu, ...]
    datacenters: tuple[DatacenterCatalogEntry, ...]
    balance: RunpodCreditBalance | None
    billing_categories: tuple[RunpodBillingCategoryState, ...]

    @property
    def stale(self) -> bool:
        return self.state == "stale"

    @property
    def unavailable(self) -> bool:
        return self.state == "unavailable"

    def to_serializable_dict(self) -> dict[str, object]:
        return {
            "provider": "runpod",
            "state": self.state,
            "stale": self.stale,
            "unavailable": self.unavailable,
            "refresh_attempted_at": _timestamp(self.refresh_attempted_at),
            "cache_expires_at": _timestamp(self.cache_expires_at),
            "age_seconds": self.age_seconds,
            "cache_hit": self.cache_hit,
            "forced_refresh": self.forced_refresh,
            "components": [item.to_serializable_dict() for item in self.components],
            "gpus": [item.to_serializable_dict() for item in self.gpus],
            "datacenters": [_datacenter_dict(item) for item in self.datacenters],
            "availability": _availability_rows(self.datacenters),
            "balance": self.balance.to_serializable_dict() if self.balance else None,
            "billing_categories": [item.to_serializable_dict() for item in self.billing_categories],
        }

    def gpu_types_for_fit(
        self, *, cloud: Literal["secure", "community"]
    ) -> tuple[RunpodGpuType, ...]:
        """Return supported live on-demand rows for existing model-fit logic."""

        rows: list[RunpodGpuType] = []
        for item in self.gpus:
            gpu = item.graphql
            if gpu is None:
                continue
            enabled = gpu.secure_cloud if cloud == "secure" else gpu.community_cloud
            if not enabled:
                continue
            rows.append(
                RunpodGpuType(
                    id=gpu.gpu_type_id,
                    displayName=gpu.display_name,
                    manufacturer=gpu.manufacturer,
                    memoryInGb=gpu.memory_in_gb,
                    cudaCores=gpu.cuda_cores,
                    secureCloud=gpu.secure_cloud,
                    communityCloud=gpu.community_cloud,
                    securePrice=gpu.secure_price,
                    communityPrice=gpu.community_price,
                    secureSpotPrice=gpu.secure_spot_price,
                    communitySpotPrice=gpu.community_spot_price,
                    maxGpuCount=gpu.max_gpu_count,
                )
            )
        return tuple(rows)

    def to_availability_snapshot(self) -> AvailabilitySnapshot:
        """Return the existing routing snapshot shape without another provider read."""

        datacenters = {item.datacenter_id: item for item in self.datacenters}
        entries: list[tuple[str, str, str, int, bool]] = []
        for item in self.gpus:
            gpu = item.graphql
            if gpu is None:
                continue
            clouds: list[str] = []
            if gpu.secure_cloud:
                clouds.append("SECURE")
            if gpu.community_cloud:
                clouds.append("COMMUNITY")
            if not clouds:
                clouds.append("SECURE")
            counts = gpu.available_gpu_counts or (1,)
            for datacenter_id in gpu.datacenter_ids:
                datacenter = datacenters.get(datacenter_id)
                if datacenter is None:
                    continue
                available = datacenter.gpu_availability.get(gpu.gpu_type_id, False)
                for count in counts:
                    for cloud in clouds:
                        entries.append((datacenter_id, gpu.gpu_type_id, cloud, count, available))
        return AvailabilitySnapshot.from_entries(entries)


@dataclass(frozen=True, slots=True, order=True)
class RunpodActualCostReference:
    """Authoritative Pitwall workload to RunPod billing-resource mapping."""

    workload_id: str
    category: RunpodBillingCategory
    external_resource_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workload_id", _non_empty(self.workload_id, "workload_id"))
        object.__setattr__(
            self,
            "external_resource_id",
            _non_empty(self.external_resource_id, "external_resource_id"),
        )
        if self.category not in {"pods", "endpoints", "network_volumes"}:
            raise ValueError("unsupported RunPod billing category")


@dataclass(frozen=True, slots=True)
class RunpodActualCostBatch:
    """One provider-scoped, exact set of pending Pod billing identities."""

    provider_id: str
    start_day: dt.date
    end_day: dt.date
    references: tuple[RunpodActualCostReference, ...]


class AsyncpgRunpodActualCostReferenceRepository:
    """Read terminal, exclusively owned Pod identities eligible for truth-up."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def pending_batches(
        self,
        *,
        now: dt.datetime,
        limit: int = MAX_ACTUAL_WORKLOADS_PER_READ,
    ) -> tuple[RunpodActualCostBatch, ...]:
        if isinstance(limit, bool) or not 1 <= limit <= MAX_ACTUAL_WORKLOADS_PER_READ:
            raise ValueError(f"limit must be between 1 and {MAX_ACTUAL_WORKLOADS_PER_READ}")
        observed = _aware_utc(now, "now")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT l.provider_id,
                       l.external_resource_id,
                       l.workload_id,
                       w.submitted_at,
                       l.created_at,
                       l.terminated_at
                FROM pitwall.leases AS l
                JOIN pitwall.workloads AS w ON w.id = l.workload_id
                JOIN pitwall.providers AS p ON p.id = l.provider_id
                WHERE p.adapter_id = 'runpod'
                  AND l.workload_id IS NOT NULL
                  AND l.external_resource_id IS NOT NULL
                  AND l.terminated_at IS NOT NULL
                  AND l.terminated_at <= $1
                  AND l.state = ANY($2::text[])
                  AND w.state = ANY($3::text[])
                  AND w.cost_actual_provenance IS DISTINCT FROM $4
                ORDER BY l.terminated_at, l.provider_id, l.workload_id
                LIMIT $5
                """,
                observed,
                list(_TERMINAL_LEASE_STATES),
                list(_TERMINAL_WORKLOAD_STATES),
                RUNPOD_POD_ACTUAL_COST_SOURCE,
                limit,
            )
        grouped: dict[str, list[asyncpg.Record]] = {}
        for row in rows:
            if not all(
                field in row
                for field in (
                    "provider_id",
                    "external_resource_id",
                    "workload_id",
                    "submitted_at",
                    "created_at",
                    "terminated_at",
                )
            ):
                continue
            grouped.setdefault(str(row["provider_id"]), []).append(row)
        batches: list[RunpodActualCostBatch] = []
        for provider_id in sorted(grouped):
            provider_rows = grouped[provider_id]
            start_day = min(
                min(row["submitted_at"], row["created_at"]).astimezone(dt.UTC).date()
                for row in provider_rows
            )
            end_day = max(
                row["terminated_at"].astimezone(dt.UTC).date() for row in provider_rows
            ) + dt.timedelta(days=1)
            batches.append(
                RunpodActualCostBatch(
                    provider_id=provider_id,
                    start_day=start_day,
                    end_day=end_day,
                    references=tuple(
                        RunpodActualCostReference(
                            workload_id=str(row["workload_id"]),
                            category="pods",
                            external_resource_id=str(row["external_resource_id"]),
                        )
                        for row in provider_rows
                    ),
                )
            )
        return tuple(batches)


@dataclass(frozen=True, slots=True)
class _CachedMarketData:
    discovery: GpuDiscoverySnapshot | None
    rest_v2: tuple[GpuTypeV2, ...] | None
    balance: RunpodCreditBalance | None
    components: tuple[RunpodMarketComponent, ...]
    refresh_attempted_at: dt.datetime
    cache_expires_at: dt.datetime
    monotonic_expires_at: float


class _NotConfigured(RuntimeError):
    pass


class RunpodMarketService:
    """Single-flight cached RunPod product service with stale-data retention."""

    def __init__(
        self,
        discovery_service: GpuDiscoveryService | None,
        catalog_client: RunPodCatalogV2Client | None,
        billing_client: RunPodBillingClient | None,
        *,
        cache_ttl_s: float = DEFAULT_RUNPOD_MARKET_CACHE_TTL_S,
        wall_clock: Callable[[], dt.datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        if not math.isfinite(cache_ttl_s) or cache_ttl_s < 0:
            raise ValueError("cache_ttl_s must be finite and non-negative")
        self._discovery = discovery_service
        self._catalog = catalog_client
        self._billing = billing_client
        self._cache_ttl_s = cache_ttl_s
        self._wall_clock = wall_clock or (lambda: dt.datetime.now(dt.UTC))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._cached: _CachedMarketData | None = None
        self._generation = 0
        self._lock = asyncio.Lock()

    async def read(self, *, force_refresh: bool = False) -> RunpodMarketRead:
        """Return a cached read or perform one serialized, concurrent refresh."""

        generation = self._generation
        cached = self._cached
        if not force_refresh and cached is not None and self._cache_is_fresh(cached):
            return self._read(cached, cache_hit=True, forced_refresh=False)
        async with self._lock:
            cached = self._cached
            if self._generation != generation and cached is not None:
                return self._read(
                    cached,
                    cache_hit=True,
                    forced_refresh=force_refresh,
                )
            if not force_refresh and cached is not None and self._cache_is_fresh(cached):
                return self._read(cached, cache_hit=True, forced_refresh=False)
            refreshed = await self._refresh(cached)
            self._cached = refreshed
            self._generation += 1
            return self._read(
                refreshed,
                cache_hit=False,
                forced_refresh=force_refresh,
            )

    def invalidate(self) -> None:
        """Make the next read refresh without discarding stale fallback data."""

        if self._cached is not None:
            self._cached = replace(
                self._cached,
                monotonic_expires_at=float("-inf"),
            )

    async def provider_actual_cost(
        self,
        *,
        provider_id: str,
        start_day: dt.date,
        end_day: dt.date,
        references: Iterable[RunpodActualCostReference],
    ) -> ProviderActualCostResult:
        """Read exact Pod buckets and map them to exact Pitwall workload ids."""

        observed_at = _aware_utc(self._wall_clock(), "wall_clock")
        normalized_provider_id = _non_empty(provider_id, "provider_id")
        start_time, end_time = _date_window(start_day, end_day)
        refs = tuple(sorted(references))

        def unavailable(reason: str) -> ProviderActualCostResult:
            return ProviderActualCostResult.unavailable(
                provider_id=normalized_provider_id,
                source=RUNPOD_POD_ACTUAL_COST_SOURCE,
                observed_at=observed_at,
                reason=reason,
            )

        if self._billing is None:
            return unavailable("RunPod billing credentials are not configured")
        if not refs:
            return unavailable("no exact workload-to-pod billing mappings were supplied")
        if len(refs) > MAX_ACTUAL_WORKLOADS_PER_READ:
            return unavailable(
                f"RunPod actual-cost reads are limited to {MAX_ACTUAL_WORKLOADS_PER_READ} workloads"
            )
        if len({item.workload_id for item in refs}) != len(refs):
            return unavailable("workload billing mappings contain duplicate workload ids")
        if len({item.external_resource_id for item in refs}) != len(refs):
            return unavailable("one RunPod pod cannot map to multiple Pitwall workloads")
        if any(item.category != "pods" for item in refs):
            return unavailable("only podId-grouped RunPod billing can map to workload actual cost")

        actuals: list[ProviderActualWorkloadCost] = []
        for ref in refs:
            try:
                records = await self._billing.pod_history(
                    start_time=start_time,
                    end_time=end_time,
                    pod_id=ref.external_resource_id,
                )
            except Exception as exc:  # reason: provider reads become a safe availability result
                return unavailable(f"RunPod pod billing read failed ({_error_code(exc)})")
            reason = _invalid_pod_records_reason(
                records,
                pod_id=ref.external_resource_id,
                start_time=start_time,
                end_time=end_time,
            )
            if reason is not None:
                return unavailable(reason)
            actuals.append(
                ProviderActualWorkloadCost(
                    workload_id=ref.workload_id,
                    actual_usd=sum((item.amount for item in records), start=Decimal("0")),
                    source=RUNPOD_POD_ACTUAL_COST_SOURCE,
                )
            )

        return ProviderActualCostResult(
            provider_id=normalized_provider_id,
            availability="available",
            source=RUNPOD_POD_ACTUAL_COST_SOURCE,
            observed_at=observed_at,
            workloads=tuple(actuals),
        )

    async def reconcile_provider_actual_cost(
        self,
        repository: CostTruthUpRepository,
        *,
        provider_id: str,
        start_day: dt.date,
        end_day: dt.date,
        references: Iterable[RunpodActualCostReference],
        tolerance_usd: Decimal = Decimal("0"),
    ) -> CostTruthUpResult:
        """Read provider actuals and apply COST's transactional idempotent truth-up."""

        actual = await self.provider_actual_cost(
            provider_id=provider_id,
            start_day=start_day,
            end_day=end_day,
            references=references,
        )
        return await reconcile_provider_actual_cost(
            repository,
            start_day=start_day,
            end_day=end_day,
            provider_actual=actual,
            tolerance_usd=tolerance_usd,
        )

    async def aclose(self) -> None:
        """Close every configured provider client."""

        if self._discovery is not None:
            await self._discovery.aclose()
        if self._billing is not None:
            await self._billing.aclose()
        if self._catalog is not None:
            await asyncio.to_thread(self._catalog.close)

    async def _refresh(self, previous: _CachedMarketData | None) -> _CachedMarketData:
        attempted_at = _aware_utc(self._wall_clock(), "wall_clock")
        discovery_result, catalogue_result, balance_result = await asyncio.gather(
            self._refresh_discovery(),
            self._refresh_catalogue(),
            self._refresh_balance(),
            return_exceptions=True,
        )
        old_discovery = previous.discovery if previous else None
        old_catalogue = previous.rest_v2 if previous else None
        old_balance = previous.balance if previous else None
        old_components = {item.name: item for item in previous.components} if previous else {}

        discovery, discovery_status = _merge_component(
            discovery_result,
            old_value=old_discovery,
            old_status=old_components.get("graphql_discovery"),
            name="graphql_discovery",
            source="runpod-graphql",
            observed_at=attempted_at,
        )
        catalogue, catalogue_status = _merge_component(
            catalogue_result,
            old_value=old_catalogue,
            old_status=old_components.get("rest_v2_catalogue"),
            name="rest_v2_catalogue",
            source="runpod-rest-v2-catalogue",
            observed_at=attempted_at,
        )
        balance, balance_status = _merge_component(
            balance_result,
            old_value=old_balance,
            old_status=old_components.get("graphql_balance"),
            name="graphql_balance",
            source="runpod-graphql",
            observed_at=attempted_at,
        )
        return _CachedMarketData(
            discovery=discovery,
            rest_v2=catalogue,
            balance=balance,
            components=(discovery_status, catalogue_status, balance_status),
            refresh_attempted_at=attempted_at,
            cache_expires_at=attempted_at + dt.timedelta(seconds=self._cache_ttl_s),
            monotonic_expires_at=self._monotonic_clock() + self._cache_ttl_s,
        )

    async def _refresh_discovery(self) -> GpuDiscoverySnapshot:
        if self._discovery is None:
            raise _NotConfigured
        return await self._discovery.refresh()

    async def _refresh_catalogue(self) -> tuple[GpuTypeV2, ...]:
        if self._catalog is None:
            raise _NotConfigured
        rows = await asyncio.to_thread(self._catalog.list_gpu_types_v2, product="POD")
        return tuple(sorted(rows, key=lambda item: item.id))

    async def _refresh_balance(self) -> RunpodCreditBalance:
        if self._discovery is None:
            raise _NotConfigured
        balance = await self._discovery.read_credits_balance()
        return RunpodCreditBalance.from_billing_snapshot(BillingSnapshot.from_runpod(balance))

    def _cache_is_fresh(self, cached: _CachedMarketData) -> bool:
        return self._cache_ttl_s > 0 and self._monotonic_clock() <= cached.monotonic_expires_at

    def _read(
        self,
        cached: _CachedMarketData,
        *,
        cache_hit: bool,
        forced_refresh: bool,
    ) -> RunpodMarketRead:
        now = _aware_utc(self._wall_clock(), "wall_clock")
        return RunpodMarketRead(
            state=_market_state(cached),
            refresh_attempted_at=cached.refresh_attempted_at,
            cache_expires_at=cached.cache_expires_at,
            age_seconds=_data_age_seconds(cached.components, now=now),
            cache_hit=cache_hit,
            forced_refresh=forced_refresh,
            components=cached.components,
            gpus=_combine_gpu_catalogue(cached.discovery, cached.rest_v2),
            datacenters=(cached.discovery.datacenters if cached.discovery else ()),
            balance=cached.balance,
            billing_categories=_billing_categories(configured=self._billing is not None),
        )


def build_configured_runpod_market_service(
    settings: PitwallSettings | None = None,
    *,
    cache_ttl_s: float | None = None,
    timeout_s: float = 60.0,
) -> RunpodMarketService:
    """Build one process-local market service without provider I/O.

    An unconfigured credential produces explicit unavailable state, keeping
    help, imports, previews, and ordinary tests local and side-effect free.
    """

    resolved = settings or load_settings_from_env()
    ttl = resolved.runpod_market_cache_ttl_s if cache_ttl_s is None else cache_ttl_s
    if not resolved.runpod_api_key:
        return RunpodMarketService(None, None, None, cache_ttl_s=ttl)
    graphql = RunpodGraphQLClient(api_key=resolved.runpod_api_key, timeout_s=timeout_s)
    return RunpodMarketService(
        GpuDiscoveryService(graphql, ttl_s=ttl),
        RunPodCatalogV2Client(
            api_key=resolved.runpod_api_key,
            rest_api_url=resolved.runpod_rest_api_url,
            timeout_s=timeout_s,
        ),
        RunPodBillingClient(
            api_key=resolved.runpod_api_key,
            rest_v1_api_url=resolved.runpod_rest_v1_api_url,
            timeout_s=timeout_s,
        ),
        cache_ttl_s=ttl,
    )


def _merge_component[ValueT](
    result: ValueT | BaseException,
    *,
    old_value: ValueT | None,
    old_status: RunpodMarketComponent | None,
    name: Literal["graphql_discovery", "rest_v2_catalogue", "graphql_balance"],
    source: str,
    observed_at: dt.datetime,
) -> tuple[ValueT | None, RunpodMarketComponent]:
    if isinstance(result, BaseException):
        reason = _error_code(result)
        if old_value is not None:
            return old_value, RunpodMarketComponent(
                name=name,
                source=source,
                state="stale",
                observed_at=old_status.observed_at if old_status else None,
                unavailable_reason=reason,
            )
        return None, RunpodMarketComponent(
            name=name,
            source=source,
            state="unavailable",
            unavailable_reason=reason,
        )
    return result, RunpodMarketComponent(
        name=name,
        source=source,
        state="fresh",
        observed_at=observed_at,
    )


def _combine_gpu_catalogue(
    discovery: GpuDiscoverySnapshot | None,
    rest_v2: tuple[GpuTypeV2, ...] | None,
) -> tuple[RunpodMarketGpu, ...]:
    graphql_by_id = {item.gpu_type_id: item for item in discovery.gpus} if discovery else {}
    rest_by_id = {item.id: item for item in rest_v2 or ()}
    rows: list[RunpodMarketGpu] = []
    for gpu_id in sorted(graphql_by_id.keys() | rest_by_id.keys()):
        rest = rest_by_id.get(gpu_id)
        rows.append(
            RunpodMarketGpu(
                gpu_type_id=gpu_id,
                graphql=graphql_by_id.get(gpu_id),
                rest_v2=(
                    RunpodRestGpuCatalogue(
                        memory_gb=rest.memory,
                        pool=rest.pool,
                        cuda_versions=tuple(
                            sorted(
                                ((item.version, item.available) for item in rest.cuda_versions),
                                key=lambda item: item[0],
                            )
                        ),
                    )
                    if rest is not None
                    else None
                ),
            )
        )
    return tuple(rows)


def _market_state(cached: _CachedMarketData) -> MarketState:
    catalogue_configured = cached.discovery is not None or cached.rest_v2 is not None
    if not catalogue_configured:
        return "unavailable"
    states = {item.state for item in cached.components}
    if "stale" in states:
        return "stale"
    if "unavailable" in states:
        return "partial"
    return "fresh"


def _data_age_seconds(
    components: tuple[RunpodMarketComponent, ...],
    *,
    now: dt.datetime,
) -> int:
    observed = [item.observed_at for item in components if item.observed_at is not None]
    if not observed:
        return 0
    return max(0, int((now - min(observed)).total_seconds()))


def _graphql_gpu_dict(gpu: GpuCatalogEntry | None) -> dict[str, object] | None:
    if gpu is None:
        return None
    return {
        "display_name": gpu.display_name,
        "manufacturer": gpu.manufacturer,
        "memory_gb": gpu.memory_in_gb,
        "cuda_cores": gpu.cuda_cores,
        "cloud": {
            "secure": gpu.secure_cloud,
            "community": gpu.community_cloud,
        },
        "on_demand_price_usd_per_gpu_hour": {
            "secure": _money(gpu.secure_price),
            "community": _money(gpu.community_price),
        },
        "spot_price_usd_per_gpu_hour": {
            "secure": _money(gpu.secure_spot_price),
            "community": _money(gpu.community_spot_price),
        },
        "minimum_bid_usd_per_gpu_hour": _money(gpu.lowest_bid_price),
        "uninterruptable_price_usd_per_gpu_hour": _money(gpu.uninterruptable_price),
        "datacenter_ids": list(gpu.datacenter_ids),
        "available_gpu_counts": list(gpu.available_gpu_counts),
        "stock_status": gpu.stock_status,
        "max_gpu_count": gpu.max_gpu_count,
    }


def _datacenter_dict(item: DatacenterCatalogEntry) -> dict[str, object]:
    return {
        "datacenter_id": item.datacenter_id,
        "name": item.name,
        "location": item.location,
        "global_network": item.global_network,
        "storage_support": item.storage_support,
        "listed": item.listed,
        "compliance": list(item.compliance),
        "gpu_types": list(item.gpu_types),
    }


def _availability_rows(
    datacenters: tuple[DatacenterCatalogEntry, ...],
) -> list[dict[str, object]]:
    return [
        {
            "datacenter_id": datacenter.datacenter_id,
            "gpu_type_id": gpu_id,
            "available": datacenter.gpu_availability[gpu_id],
            "source": "runpod-graphql",
        }
        for datacenter in datacenters
        for gpu_id in sorted(datacenter.gpu_availability)
    ]


def _billing_categories(*, configured: bool) -> tuple[RunpodBillingCategoryState, ...]:
    state: Literal["supported", "unavailable"] = "supported" if configured else "unavailable"
    configuration_reason = None if configured else "billing credentials are not configured"
    return (
        RunpodBillingCategoryState(
            category="pods",
            history_read_supported=True,
            workload_actual_supported=True,
            identity_field="pod_id",
            actual_state=state,
            unavailable_reason=configuration_reason,
        ),
        RunpodBillingCategoryState(
            category="endpoints",
            history_read_supported=True,
            workload_actual_supported=False,
            identity_field="endpoint_id",
            actual_state="unavailable",
            unavailable_reason="billing rows do not contain a Pitwall workload or job identity",
        ),
        RunpodBillingCategoryState(
            category="network_volumes",
            history_read_supported=True,
            workload_actual_supported=False,
            identity_field=None,
            actual_state="unavailable",
            unavailable_reason="billing rows do not contain a network-volume identity",
        ),
    )


def _invalid_pod_records_reason(
    records: tuple[RunPodBillingRecord, ...],
    *,
    pod_id: str,
    start_time: dt.datetime,
    end_time: dt.datetime,
) -> str | None:
    if not records:
        return "RunPod pod billing returned no authoritative rows for the requested window"
    if any(item.pod_id != pod_id for item in records):
        return "RunPod pod billing did not preserve the requested exact pod identity"
    if any(not (start_time <= item.time < end_time) for item in records):
        return "RunPod pod billing returned a bucket outside the requested window"
    bucket_keys = [(item.pod_id, item.time) for item in records]
    if len(bucket_keys) != len(set(bucket_keys)):
        return "RunPod pod billing returned duplicate resource buckets"
    return None


def _date_window(start_day: dt.date, end_day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    if not isinstance(start_day, dt.date) or isinstance(start_day, dt.datetime):
        raise TypeError("start_day must be datetime.date")
    if not isinstance(end_day, dt.date) or isinstance(end_day, dt.datetime):
        raise TypeError("end_day must be datetime.date")
    if start_day >= end_day:
        raise ValueError("start_day must be before end_day")
    return (
        dt.datetime.combine(start_day, dt.time.min, tzinfo=dt.UTC),
        dt.datetime.combine(end_day, dt.time.min, tzinfo=dt.UTC),
    )


def _error_code(exc: BaseException) -> str:
    provider_reason = getattr(exc, "reason_code", None)
    if provider_reason in {
        "authentication_failed",
        "invalid_provider_response",
        "provider_unavailable",
        "timeout",
    }:
        return str(provider_reason)
    if isinstance(exc, _NotConfigured):
        return "not_configured"
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    status_code = getattr(exc, "status_code", None)
    if status_code in {401, 403}:
        return "authentication_failed"
    if "ResponseError" in type(exc).__name__ or "ValidationError" in type(exc).__name__:
        return "invalid_provider_response"
    return "provider_unavailable"


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _timestamp(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _aware_utc(value: dt.datetime, field_name: str) -> dt.datetime:
    if not isinstance(value, dt.datetime):
        raise TypeError(f"{field_name} must return datetime.datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must return a timezone-aware datetime")
    return value.astimezone(dt.UTC)


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-empty")
    return value.strip()


__all__ = [
    "AsyncpgRunpodActualCostReferenceRepository",
    "DEFAULT_RUNPOD_MARKET_CACHE_TTL_S",
    "MAX_ACTUAL_WORKLOADS_PER_READ",
    "RUNPOD_POD_ACTUAL_COST_SOURCE",
    "RunpodActualCostBatch",
    "RunpodActualCostReference",
    "RunpodBillingCategoryState",
    "RunpodCreditBalance",
    "RunpodMarketComponent",
    "RunpodMarketGpu",
    "RunpodMarketRead",
    "RunpodMarketService",
    "RunpodRestGpuCatalogue",
    "build_configured_runpod_market_service",
]
