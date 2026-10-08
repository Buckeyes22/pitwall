"""Tier ladder, emergency descent, and the prong-3 escape hatch (§9.6)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import Capability, Provider
from pitwall.routing.cascade_seed import (
    escape_hatch_message,
    ladder_rank,
    order_ladder,
)
from pitwall.routing.quota import QuotaRecord, QuotaSnapshot

NOW = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
RESET_AT = dt.datetime(2026, 9, 10, 14, 0, tzinfo=dt.UTC)


def _capability() -> Capability:
    return Capability(
        id="cap_chat",
        name="coding.chat",
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode=CostMode.ZERO,
        served_model_id="coding.chat",
        source=CapabilitySource.YAML,
        created_at=NOW,
        updated_at=NOW,
    )


def _own_serve() -> Provider:
    return Provider(
        id="prov_own",
        capability_id="cap_chat",
        name="own",
        adapter_id=ProviderAdapterId.RUNPOD,
        provider_type=ProviderType.POD_LEASE,
        config={
            "cost": {"per_second_active": "0.001"},
            "expected_latency_ms": 10,
        },
        priority=1,
        updated_at=NOW,
    )


def _keyless() -> Provider:
    return Provider(
        id="prov_keyless",
        capability_id="cap_chat",
        name="keyless",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        config={
            "cost": {"kind": "zero"},
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "keyless/m1",
                "catalog": {
                    "free_type": "keyless",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                    "pool_key": "keyless-pool",
                },
            },
        },
        priority=10,
        updated_at=NOW,
    )


def _budgeted_free() -> Provider:
    return Provider(
        id="prov_budgeted",
        capability_id="cap_chat",
        name="budgeted",
        adapter_id=ProviderAdapterId.GATEWAY,
        provider_type=ProviderType.OPENAI_GATEWAY,
        config={
            "cost": {"kind": "zero"},
            "gateway": {
                "base_url": "http://127.0.0.1:20130/v1",
                "model_id": "budgeted/b1",
                "catalog": {
                    "free_type": "recurring-monthly",
                    "tos": "ok",
                    "hard_stop_guaranteed": True,
                    "pool_key": "budgeted-pool",
                    "monthly_tokens": 5_000_000,
                },
            },
        },
        priority=20,
        updated_at=NOW,
    )


def _metered() -> Provider:
    return Provider(
        id="prov_metered",
        capability_id="cap_chat",
        name="metered",
        adapter_id=ProviderAdapterId.TOGETHER,
        provider_type=ProviderType.SERVERLESS_LB,
        config={
            "cost": {
                "kind": "per_token",
                "per_million_input_tokens": "1",
                "per_million_output_tokens": "1",
            },
            "expected_latency_ms": 100,
        },
        priority=30,
        updated_at=NOW,
    )


@dataclass(frozen=True, slots=True)
class _Attempt:
    """Minimal stand-in for a routed attempt carrying an elimination reason."""

    provider_id: str
    provider: Provider
    attempt: int = 0
    eliminated: str | None = None


def _attempt(provider: Provider, *, eliminated: str | None = None) -> _Attempt:
    return _Attempt(provider_id=provider.id, provider=provider, eliminated=eliminated)


def _snapshot(reset_at: dt.datetime = RESET_AT) -> QuotaSnapshot:
    return QuotaSnapshot(
        records=(
            QuotaRecord(
                provider_id="prov_keyless",
                pool_key="keyless-pool",
                free_type="keyless",
                window_start=None,
                reset_at=reset_at,
                budget_units=None,
                used_units=Decimal("0"),
                tos_verdict="ok",
                evidence={},
            ),
            QuotaRecord(
                provider_id="prov_budgeted",
                pool_key="budgeted-pool",
                free_type="recurring-monthly",
                window_start=RESET_AT - dt.timedelta(days=10),
                reset_at=reset_at,
                budget_units=Decimal(5_000_000),
                used_units=Decimal(5_000_000),
                tos_verdict="ok",
                evidence={},
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class _CandidateView:
    provider_id: str
    provider: Provider


@dataclass(frozen=True, slots=True)
class _EliminatedView:
    provider_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class _PlanView:
    """Plan shape required by escape_hatch_message and ProductionRoutePlan.to_dict."""

    capability_snapshot: Capability
    ranked_candidates: tuple[_CandidateView, ...]
    eliminated: tuple[_EliminatedView, ...]
    escape_hatch_message: str | None = None

    @property
    def dropped_provider_reasons(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for item in self.eliminated:
            out.setdefault(item.provider_id, []).append(item.reason)
        return out

    def to_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "capability_name": self.capability_snapshot.name,
            "ranked_candidates": [{"provider_id": c.provider_id} for c in self.ranked_candidates],
            "eliminated": [
                {"provider_id": e.provider_id, "reason": e.reason} for e in self.eliminated
            ],
        }
        if self.escape_hatch_message is not None:
            proposed_command = (
                self.escape_hatch_message.split("— ", 1)[1]
                if "— " in self.escape_hatch_message
                else self.escape_hatch_message
            )
            document["escape_hatch"] = {
                "proposed_command": proposed_command,
                "executed": False,
                "reason": "all_free_pools_exhausted",
            }
        return document


def _plan_all_quota_ineligible(reset_at: dt.datetime = RESET_AT) -> _PlanView:
    keyless = _keyless()
    budgeted = _budgeted_free()
    return _PlanView(
        capability_snapshot=_capability(),
        ranked_candidates=(
            _CandidateView(provider_id=keyless.id, provider=keyless),
            _CandidateView(provider_id=budgeted.id, provider=budgeted),
        ),
        eliminated=(
            _EliminatedView(provider_id=keyless.id, reason="quota_ineligible"),
            _EliminatedView(provider_id=budgeted.id, reason="quota_ineligible"),
        ),
    )


def test_ladder_orders_own_serve_keyless_budgeted_metered() -> None:
    ranked = order_ladder([_metered(), _budgeted_free(), _keyless(), _own_serve()])
    assert [p.name for p in ranked] == ["own", "keyless", "budgeted", "metered"]


def test_escape_hatch_message_when_all_free_exhausted() -> None:
    message = escape_hatch_message(
        _plan_all_quota_ineligible(reset_at=RESET_AT),
        quota_snapshot=_snapshot(reset_at=RESET_AT),
        now=NOW,
        own_pod_usd_per_hour=Decimal("0.50"),
        gpu_class="RTX 4090",
    )
    assert message == (
        "all free pools exhausted until 14:00 UTC; own-pod at $0.50/hr would cover the gap "
        "— pitwall serve --model coding.chat --gpu-class RTX 4090"
    )


def test_escape_hatch_is_never_auto_executed() -> None:
    plan = _plan_all_quota_ineligible(reset_at=RESET_AT)
    message = escape_hatch_message(
        plan,
        quota_snapshot=_snapshot(reset_at=RESET_AT),
        now=NOW,
        own_pod_usd_per_hour=Decimal("0.50"),
        gpu_class="RTX 4090",
    )
    assert message is not None
    populated = _PlanView(
        capability_snapshot=plan.capability_snapshot,
        ranked_candidates=plan.ranked_candidates,
        eliminated=plan.eliminated,
        escape_hatch_message=message,
    )
    document = populated.to_dict()
    assert document["escape_hatch"]["proposed_command"].startswith(
        "pitwall serve --model coding.chat --gpu-class RTX 4090"
    )
    assert document["escape_hatch"]["executed"] is False


@pytest.mark.parametrize(
    "provider,expected",
    [
        (_own_serve, 0),
        (_keyless, 1),
        (_budgeted_free, 2),
        (_metered, 3),
    ],
)
def test_ladder_rank_mapping(provider: Any, expected: int) -> None:
    assert ladder_rank(provider()) == expected


def test_order_ladder_drops_unknown_pricing_to_rank_four() -> None:
    broken = _metered()
    broken.config.pop("cost", None)
    ranked = order_ladder([_metered(), broken, _keyless()])
    assert ranked[0].name == "keyless"
    assert ranked[-1].id == broken.id
    assert ladder_rank(broken) == 4


def test_escape_hatch_accepts_stage_prefixed_reasons_from_production_plans() -> None:
    plan = _plan_all_quota_ineligible(reset_at=RESET_AT)
    prefixed = _PlanView(
        capability_snapshot=plan.capability_snapshot,
        ranked_candidates=plan.ranked_candidates,
        eliminated=tuple(
            _EliminatedView(provider_id=item.provider_id, reason=f"quota:{item.reason}")
            for item in plan.eliminated
        ),
    )
    message = escape_hatch_message(
        prefixed,
        quota_snapshot=_snapshot(reset_at=RESET_AT),
        now=NOW,
        own_pod_usd_per_hour=Decimal("0.50"),
        gpu_class="RTX 4090",
    )
    assert message is not None and message.startswith("all free pools exhausted until 14:00 UTC")


def test_escape_hatch_fires_from_a_production_shaped_plan() -> None:
    """build_production_plan never ranks a Stage-2 quota elimination, so the hatch must read
    ``dropped_provider_reasons`` (stage-prefixed) with an empty ranked list."""
    plan = _PlanView(
        capability_snapshot=_capability(),
        ranked_candidates=(),
        eliminated=(
            _EliminatedView(provider_id="prov_keyless", reason="quota:quota_ineligible"),
            _EliminatedView(provider_id="prov_budgeted", reason="quota:quota_ineligible"),
        ),
    )
    message = escape_hatch_message(
        plan,
        quota_snapshot=_snapshot(reset_at=RESET_AT),
        now=NOW,
        own_pod_usd_per_hour=Decimal("0.50"),
        gpu_class="RTX 4090",
    )
    assert message == (
        "all free pools exhausted until 14:00 UTC; own-pod at $0.50/hr would cover the gap "
        "— pitwall serve --model coding.chat --gpu-class RTX 4090"
    )


def test_escape_hatch_needs_at_least_one_exhausted_free_pool() -> None:
    plan = _PlanView(
        capability_snapshot=_capability(),
        ranked_candidates=(),
        eliminated=(_EliminatedView(provider_id="prov_keyless", reason="health:disabled"),),
    )
    assert (
        escape_hatch_message(
            plan,
            quota_snapshot=_snapshot(),
            now=NOW,
            own_pod_usd_per_hour=Decimal("0.50"),
            gpu_class="RTX 4090",
        )
        is None
    )


def test_escape_hatch_stays_quiet_while_one_free_pool_is_still_executable() -> None:
    keyless = _keyless()
    plan = _PlanView(
        capability_snapshot=_capability(),
        ranked_candidates=(_CandidateView(provider_id=keyless.id, provider=keyless),),
        eliminated=(_EliminatedView(provider_id="prov_budgeted", reason="quota:quota_ineligible"),),
    )
    assert (
        escape_hatch_message(
            plan,
            quota_snapshot=_snapshot(),
            now=NOW,
            own_pod_usd_per_hour=Decimal("0.50"),
            gpu_class="RTX 4090",
        )
        is None
    )
