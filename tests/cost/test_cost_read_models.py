"""Decimal-first estimate/actual/reconciliation read semantics."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from pitwall.core.cost_reporting import recent_workloads_read
from pitwall.cost.read_models import (
    CostSummaryEntry,
    CostSummaryRead,
    RecentWorkloadsRead,
    WorkloadCostRead,
    WorkloadCostRecord,
)


def test_estimate_only_read_does_not_present_an_invoice() -> None:
    cost = WorkloadCostRead(estimate=Decimal("1.250000"), actual=None)

    assert cost.reconciliation_status == "estimate_only"
    assert cost.effective == Decimal("1.250000")
    assert cost.variance is None
    assert cost.actual_provenance is None
    assert cost.actual_kind == "none"
    assert cost.provider_invoice is False
    assert cost.to_serializable_dict() == {
        "model": "persisted_workload_cost",
        "components": [],
        "estimate": "1.250000",
        "ceiling": None,
        "confidence": "unknown",
        "provenance": "pitwall.workloads",
        "currency": "USD",
        "assumptions": [
            "persisted cost_estimate_usd is the available pre-spend amount",
            "quote components and original confidence are not persisted",
        ],
        "actual": None,
        "variance": None,
        "effective": "1.250000",
        "reconciliation_status": "estimate_only",
        "actual_kind": "none",
        "actual_provenance": None,
        "reconciled_at": None,
        "provider_invoice": False,
    }


def test_recorded_actual_read_labels_variance_and_unspecified_provenance() -> None:
    cost = WorkloadCostRead(
        estimate=Decimal("1.250000"),
        actual=Decimal("1.000001"),
    )

    assert cost.reconciliation_status == "actual_recorded"
    assert cost.effective == Decimal("1.000001")
    assert cost.variance == Decimal("-0.249999")
    assert cost.actual_provenance == "recorded_actual_source_unspecified"
    assert cost.actual_kind == "recorded"
    assert cost.provider_invoice is False


def test_unpriced_read_has_explicit_status() -> None:
    cost = WorkloadCostRead(estimate=None, actual=None)

    assert cost.reconciliation_status == "unpriced"
    assert cost.effective is None


def test_read_models_require_decimal_money() -> None:
    with pytest.raises(TypeError, match="estimate must be Decimal"):
        WorkloadCostRead(estimate=1.25, actual=None)
    with pytest.raises(TypeError, match="cost_usd must be Decimal"):
        CostSummaryEntry(
            day=dt.date(2026, 9, 1),
            capability_class="llm",
            provider_type="public_endpoint",
            workload_count=1,
            cost_usd=1.25,
        )


def test_legacy_json_numbers_are_confined_to_explicit_compatibility_serializers() -> None:
    summary = CostSummaryRead(
        total_usd=Decimal("1.250000"),
        entries=(
            CostSummaryEntry(
                day=dt.date(2026, 9, 1),
                capability_class="llm",
                provider_type="public_endpoint",
                workload_count=1,
                cost_usd=Decimal("1.250000"),
            ),
        ),
    )
    workloads = RecentWorkloadsRead(
        workloads=(
            WorkloadCostRecord(
                fields={"id": "wkl-cost"},
                cost=WorkloadCostRead(
                    estimate=Decimal("1.250000"),
                    actual=Decimal("1.000000"),
                ),
            ),
        )
    )

    legacy_summary = summary.to_legacy_serializable_dict()
    legacy_workload = workloads.to_legacy_serializable_dict()["workloads"][0]

    assert legacy_summary["total_usd"] == "1.250000"
    assert legacy_workload["cost_estimate_usd"] == "1.250000"
    assert legacy_workload["cost_actual_usd"] == "1.000000"
    assert legacy_workload["cost"] == {
        "model": "persisted_workload_cost",
        "components": [],
        "estimate": "1.250000",
        "ceiling": None,
        "confidence": "unknown",
        "provenance": "pitwall.workloads",
        "currency": "USD",
        "assumptions": [
            "persisted cost_estimate_usd is the available pre-spend amount",
            "quote components and original confidence are not persisted",
        ],
        "actual": "1.000000",
        "variance": "-0.250000",
        "effective": "1.000000",
        "reconciliation_status": "actual_recorded",
        "actual_kind": "recorded",
        "actual_provenance": "recorded_actual_source_unspecified",
        "reconciled_at": None,
        "provider_invoice": False,
    }


def test_persisted_workload_mapping_does_not_invent_quote_detail() -> None:
    cost = WorkloadCostRead.from_persisted(
        cost_estimate_usd=Decimal("0.250000"),
        cost_actual_usd=None,
    )

    assert cost.estimate == Decimal("0.250000")
    assert cost.ceiling == Decimal("0.250000")
    assert cost.confidence == "unknown"
    assert cost.to_serializable_dict()["components"] == []


def test_persisted_structured_quote_retains_estimate_ceiling_and_truth_up() -> None:
    reconciled_at = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
    cost = WorkloadCostRead.from_persisted(
        cost_estimate_usd=Decimal("0.100000"),
        cost_ceiling_usd=Decimal("0.600000"),
        cost_quote={
            "model": "per_unit",
            "components": [
                {
                    "name": "run_units",
                    "unit": "image",
                    "rate": "0.10",
                    "ceiling_rate": "0.10",
                    "count": "1",
                    "ceiling_count": "6",
                    "estimate": "0.100000",
                    "ceiling": "0.600000",
                }
            ],
            "estimate": "0.100000",
            "ceiling": "0.600000",
            "confidence": "bounded",
            "provenance": "provider_config",
            "currency": "USD",
            "assumptions": [
                "unit_count is explicit, provider-enforced, and never inferred from payload text"
            ],
        },
        cost_actual_usd=Decimal("0.125000"),
        cost_actual_provenance="provider-billing-api",
        cost_reconciled_at=reconciled_at,
    )

    assert cost.model == "per_unit"
    assert cost.estimate == Decimal("0.100000")
    assert cost.ceiling == Decimal("0.600000")
    assert cost.confidence == "bounded"
    assert cost.components[0].estimated_count == Decimal("1")
    assert cost.reconciliation_status == "reconciled"
    assert cost.actual_kind == "provider_reported"
    assert cost.actual_provenance == "provider-billing-api"
    assert cost.provider_invoice is False
    assert cost.to_serializable_dict()["actual_kind"] == "provider_reported"
    assert cost.to_serializable_dict()["reconciled_at"] == reconciled_at.isoformat()


def test_usage_truth_up_is_not_mislabeled_as_provider_billing_actual() -> None:
    reconciled_at = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
    cost = WorkloadCostRead(
        estimate=Decimal("0.000200"),
        actual=Decimal("0.000150"),
        stored_actual_provenance="broker:provider_usage:together",
        reconciled_at=reconciled_at,
    )

    assert cost.actual_kind == "usage_derived"
    assert cost.provider_invoice is False


def test_broker_observed_zero_is_not_mislabeled_as_provider_reported() -> None:
    reconciled_at = dt.datetime(2026, 9, 1, 12, 0, tzinfo=dt.UTC)
    cost = WorkloadCostRead(
        estimate=Decimal("0"),
        actual=Decimal("0"),
        stored_actual_provenance="broker:no_provider_invocation",
        reconciled_at=reconciled_at,
    )

    assert cost.actual_kind == "recorded"

    provisioning_failure = WorkloadCostRead(
        estimate=Decimal("0.010000"),
        actual=Decimal("0"),
        stored_actual_provenance="broker_zero_provision_failed",
        reconciled_at=reconciled_at,
    )
    assert provisioning_failure.actual_kind == "recorded"


def test_cost_read_rejects_ceiling_below_estimate() -> None:
    with pytest.raises(ValueError, match="ceiling must be greater"):
        WorkloadCostRead(
            estimate=Decimal("1.000000"),
            ceiling=Decimal("0.999999"),
            actual=None,
        )

    with pytest.raises(ValueError, match="ceiling requires an estimate"):
        WorkloadCostRead(
            estimate=None,
            ceiling=Decimal("0.999999"),
            actual=None,
        )


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [0, -1, 101, 10_000])
async def test_recent_workload_limit_is_bounded_in_shared_service(limit: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 100"):
        await recent_workloads_read(object(), limit=limit)  # type: ignore[arg-type]  # reason: no I/O before validation
