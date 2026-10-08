"""Durable identity and reconciler wiring for RP-01 Pod actual billing."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import pitwall.reconciler as reconciler
from pitwall.core.enums import CapabilitySource, ProviderType
from pitwall.core.models import Provider
from pitwall.cost.reconcile_cost import (
    CostReconcilePlan,
    ProviderActualCostResult,
    ProviderActualWorkloadCost,
)
from pitwall.providers.registry import ProviderRegistry
from pitwall.providers.runpod import RunPodProvider
from pitwall.runpod_market import (
    RUNPOD_POD_ACTUAL_COST_SOURCE,
    AsyncpgRunpodActualCostReferenceRepository,
    RunpodActualCostBatch,
    RunpodActualCostReference,
)

pytestmark = pytest.mark.anyio


class _Acquire:
    def __init__(self, connection: object) -> None:
        self._connection = connection

    async def __aenter__(self) -> object:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _Pool:
    def __init__(self, connection: object) -> None:
        self.connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


async def test_pending_batches_use_only_persisted_exclusive_terminal_identities() -> None:
    class Connection:
        query = ""
        args: tuple[object, ...] = ()

        async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
            self.query = query
            self.args = args
            return [
                {
                    "provider_id": "prov-runpod",
                    "external_resource_id": "pod-exact",
                    "workload_id": "wkl-exact",
                    "submitted_at": dt.datetime(2026, 8, 29, 23, tzinfo=dt.UTC),
                    "created_at": dt.datetime(2026, 8, 30, 1, tzinfo=dt.UTC),
                    "terminated_at": dt.datetime(2026, 8, 31, 12, tzinfo=dt.UTC),
                }
            ]

    connection = Connection()
    repository = AsyncpgRunpodActualCostReferenceRepository(_Pool(connection))  # type: ignore[arg-type]  # reason: SQL recorder fake
    batches = await repository.pending_batches(now=dt.datetime(2026, 9, 1, tzinfo=dt.UTC))

    assert batches == (
        RunpodActualCostBatch(
            provider_id="prov-runpod",
            start_day=dt.date(2026, 8, 29),
            end_day=dt.date(2026, 9, 1),
            references=(RunpodActualCostReference("wkl-exact", "pods", "pod-exact"),),
        ),
    )
    assert "JOIN pitwall.workloads AS w ON w.id = l.workload_id" in connection.query
    assert "p.adapter_id = 'runpod'" in connection.query
    assert "l.terminated_at IS NOT NULL" in connection.query
    assert connection.args[3] == RUNPOD_POD_ACTUAL_COST_SOURCE


async def test_reconciler_truths_up_only_the_reference_repository_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = RunpodActualCostBatch(
        provider_id="prov-runpod",
        start_day=dt.date(2026, 8, 30),
        end_day=dt.date(2026, 9, 1),
        references=(RunpodActualCostReference("wkl-exact", "pods", "pod-exact"),),
    )
    calls: dict[str, Any] = {}

    class ReferenceRepository:
        async def pending_batches(self, *, now: dt.datetime) -> tuple[RunpodActualCostBatch, ...]:
            calls["now"] = now
            return (batch,)

    provider = Provider(
        id="prov-runpod",
        capability_id="cap-gpu",
        name="RunPod exact",
        credential_ref="PITWALL_TEST_RUNPOD_BILLING_KEY",
        provider_type=ProviderType.POD_LEASE,
        priority=1,
        source=CapabilitySource.API,
        updated_at=dt.datetime(2026, 8, 30, tzinfo=dt.UTC),
    )

    class ProviderRepository:
        async def get(self, provider_id: str) -> Provider | None:
            assert provider_id == provider.id
            return provider

    class TruthUpRepository:
        async def truth_up(self, **kwargs: Any) -> tuple[CostReconcilePlan, int]:
            calls["truth_up"] = kwargs
            return CostReconcilePlan(adjustments=(), window_count=1), 0

    async def fake_actual_cost(
        self: RunPodProvider,
        request: object,
    ) -> ProviderActualCostResult:
        del self
        calls["request"] = request
        return ProviderActualCostResult(
            provider_id=provider.id,
            availability="available",
            source=RUNPOD_POD_ACTUAL_COST_SOURCE,
            observed_at=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            workloads=(
                ProviderActualWorkloadCost(
                    workload_id="wkl-exact",
                    actual_usd=Decimal("2.500000"),
                    source=RUNPOD_POD_ACTUAL_COST_SOURCE,
                ),
            ),
        )

    monkeypatch.setattr(RunPodProvider, "actual_cost", fake_actual_cost)
    monkeypatch.setattr(
        reconciler,
        "load_settings_from_env",
        lambda: SimpleNamespace(runpod_rest_v1_api_url="https://billing.test/v1"),
    )
    registry = ProviderRegistry()
    registry.register(RunPodProvider())
    now = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)
    await reconciler._reconcile_runpod_pod_actual_cost(
        {
            "db_pool": object(),
            "environ": {"PITWALL_TEST_RUNPOD_BILLING_KEY": "secret"},
            "now": now,
            "provider_registry": registry,
            "provider_repository": ProviderRepository(),
            "runpod_actual_reference_repository": ReferenceRepository(),
            "cost_truth_up_repository": TruthUpRepository(),
        }
    )

    request = calls["request"]
    assert request.references[0].workload_id == "wkl-exact"
    assert request.references[0].external_resource_id == "pod-exact"
    assert calls["truth_up"]["provider_actuals"][0].actual_usd == Decimal("2.500000")
    assert calls["now"] == now
