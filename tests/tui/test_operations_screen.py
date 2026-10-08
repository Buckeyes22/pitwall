"""Hermetic tests for the Textual Operations screen."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import replace
from decimal import Decimal
from typing import cast

import asyncpg
import pytest
from hypothesis import given
from hypothesis import strategies as st
from textual.widgets import Button, Input, Static

from pitwall.autopilot import AutopilotHardLimits, AutopilotMode, AutopilotRunResult
from pitwall.config import PitwallSettings
from pitwall.core.enums import CapabilityClass, CapabilitySource, CostMode, ProviderType
from pitwall.core.models import Capability, Provider
from pitwall.policy import Policy, PolicyRule, PolicySet, PolicyTarget
from pitwall.security.pre_spend import PreSpendInspectionService
from pitwall.tui import PitwallApp
from pitwall.tui.operations import (
    AutopilotSummary,
    CatalogEntry,
    JobEntry,
    OperationsSnapshot,
    PolicySummary,
    PostgresOperationsSource,
    ProviderResilienceEntry,
    RoutingSummary,
    StaticOperationsSource,
    display_text,
    format_catalog_table,
    format_guardrail_rules,
    format_jobs_table,
    format_resilience_table,
)
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource
from pitwall.tui.providers import ProvidersSnapshot, StaticProvidersSource

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_guardrail_preview_button_is_not_clipped(size: tuple[int, int]) -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        operations_source=StaticOperationsSource(_operations_snapshot()),
    )
    async with app.run_test(size=size) as pilot:
        await pilot.press("a")
        await pilot.pause()
        button = app.screen.query_one("#guardrail-preview-button", Button)
        # Loaded guardrail rules can push the control below the initial viewport.
        # Scrolling may reveal it, but must not leave it clipped by its own row.
        button.scroll_visible(animate=False)
        await pilot.pause()
        assert button.parent is not None
        assert button.content_region.height > 0
        assert button.parent.content_region.contains_region(button.region)
        assert app.screen.region.contains_region(button.region)


_NOW = dt.datetime(2026, 6, 2, 16, 30, tzinfo=dt.UTC)


def _overview_snapshot() -> OverviewSnapshot:
    return OverviewSnapshot(
        provider_total=3,
        provider_enabled=3,
        provider_health_counts={"healthy": 3},
        lease_state_counts={},
        active_leases=0,
        total_cost_usd=Decimal("0"),
        cost_entry_count=0,
        recent_workload_count=0,
        refreshed_at=_NOW,
    )


def _capability(capability_id: str = "cap_alpha") -> Capability:
    return Capability(
        id=capability_id,
        name="embedding.alpha",
        version="1.0.0",
        class_=CapabilityClass.EMBEDDING,
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        enabled=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _provider(
    provider_id: str,
    *,
    capability_id: str = "cap_alpha",
    priority: int = 1,
    health_status: str = "healthy",
    cooldown_until: dt.datetime | None = None,
    config: dict[str, object] | None = None,
) -> Provider:
    return Provider(
        id=provider_id,
        capability_id=capability_id,
        name=provider_id,
        provider_type=ProviderType.SERVERLESS_QUEUE,
        config=config or {"cost": {"per_second_active": "0.001"}},
        priority=priority,
        enabled=True,
        health_status=health_status,
        consecutive_failures=1 if health_status == "unhealthy" else 0,
        cooldown_trips=1 if cooldown_until is not None else 0,
        recent_error_rate=0.125 if health_status == "unhealthy" else 0.0,
        cooldown_until=cooldown_until,
        source=CapabilitySource.API,
        updated_at=_NOW,
    )


def _operations_snapshot() -> OperationsSnapshot:
    return OperationsSnapshot(
        catalog=(
            CatalogEntry(
                capability_id="cap_alpha",
                name="embedding.alpha",
                class_label="embedding",
                cost_mode="per_second",
                enabled=True,
                provider_count=2,
            ),
            CatalogEntry(
                capability_id="cap_beta",
                name="llm.beta",
                class_label="llm",
                cost_mode="per_token",
                enabled=False,
                provider_count=1,
            ),
        ),
        job_state_counts={"queued": 1, "running": 1, "completed": 1},
        recent_jobs=(
            JobEntry(
                workload_id="job_alpha",
                capability_id="cap_alpha",
                provider_id="prov_alpha",
                state="running",
                submitted_at=_NOW,
                cost_usd=Decimal("0.125000"),
            ),
        ),
        resilience=(
            ProviderResilienceEntry(
                provider_id="prov_alpha",
                health="healthy",
                consecutive_failures=0,
                cooldown_trips=0,
                cooldown_until=None,
                recent_error_rate=0.0,
            ),
            ProviderResilienceEntry(
                provider_id="prov_beta",
                health="unhealthy",
                consecutive_failures=3,
                cooldown_trips=2,
                cooldown_until=_NOW,
                recent_error_rate=0.125,
            ),
        ),
        routing=RoutingSummary(
            capability_name="embedding.alpha",
            selected_provider_id="prov_alpha",
            fallback_chain=("prov_alpha", "prov_beta"),
            candidate_count=2,
            eliminated_count=0,
            capacity_dropped_count=0,
        ),
        policy=PolicySummary(policy_count=3, decision="allow", violation_count=0),
        autopilot=AutopilotSummary(
            mode="shadow",
            decision_count=2,
            applied_count=0,
            outcome_counts={"shadowed": 2},
        ),
        guardrails=PreSpendInspectionService().status(),
        refreshed_at=_NOW,
    )


@pytest.mark.property
@given(st.text(max_size=32))
def test_display_text_returns_display_safe_non_empty_label(value: str) -> None:
    label = display_text(value)

    assert label
    assert label == label.strip()


def test_operations_snapshot_summaries_are_stable() -> None:
    snapshot = _operations_snapshot()

    assert snapshot.summary == (
        "Operations: 2 capabilities | 3 providers | 3 jobs | policies allow | autopilot shadow"
    )
    assert snapshot.jobs_summary == "Jobs: completed 1 | queued 1 | running 1"
    assert snapshot.resilience_summary == "Resilience: 2 providers | 1 unhealthy | 1 in cooldown"
    assert snapshot.guardrail_summary.startswith("Guardrails: balanced | inspected 0")
    assert snapshot.refreshed_label == "2026-06-02 16:30 UTC"


def test_operation_tables_render_catalog_jobs_and_resilience() -> None:
    snapshot = _operations_snapshot()

    catalog_table = format_catalog_table(snapshot.catalog)
    jobs_table = format_jobs_table(snapshot.recent_jobs)
    resilience_table = format_resilience_table(snapshot.resilience)

    assert "Capability ID" in catalog_table
    assert "embedding.alpha" in catalog_table
    assert "per_token" in catalog_table
    assert "disabled" in catalog_table
    assert "Job ID" in jobs_table
    assert "job_alpha" in jobs_table
    assert "$0.13" in jobs_table
    assert "Provider ID" in resilience_table
    assert "prov_beta" in resilience_table
    assert "12.5%" in resilience_table
    assert "2026-06-02 16:30 UTC" in resilience_table

    guardrail_rules = format_guardrail_rules(snapshot.guardrails)
    assert "Balanced action" in guardrail_rules
    assert "secret_field" in guardrail_rules
    assert "explicitly secret-bearing field name" in guardrail_rules


def test_guardrail_last_decision_summary_is_complete_and_non_disclosing() -> None:
    service = PreSpendInspectionService(now_factory=lambda: _NOW)
    secret = "sk-abcdefghijklmnop12345678"
    service.inspect({"token": secret})
    snapshot = replace(_operations_snapshot(), guardrails=service.status())

    summary = snapshot.guardrail_last_decision_summary

    assert "block at 2026-06-02 16:30:00 UTC" in summary
    assert "findings 1" in summary
    assert "rules secret_field" in summary
    assert "inspected" in summary
    assert "elapsed" in summary
    assert "limit none" in summary
    assert secret not in summary


def test_limited_decision_without_reason_renders_unspecified() -> None:
    service = PreSpendInspectionService(now_factory=lambda: _NOW)
    service.inspect({"token": "sk-abcdefghijklmnop12345678"})
    status = service.status()
    assert status.last_decision is not None
    limited_status = replace(
        status,
        last_decision=replace(status.last_decision, limited=True, limit_reason=None),
    )
    snapshot = replace(_operations_snapshot(), guardrails=limited_status)

    summary = snapshot.guardrail_last_decision_summary

    assert "limit unspecified" in summary
    assert "limit None" not in summary


async def test_postgres_operations_source_maps_read_only_inputs() -> None:
    async def pool_factory() -> asyncpg.Pool:
        return cast(asyncpg.Pool, object())

    async def capability_loader(pool: asyncpg.Pool) -> Sequence[Capability]:
        return (_capability(),)

    async def provider_loader(pool: asyncpg.Pool) -> Sequence[Provider]:
        return (
            _provider(
                "prov_alpha",
                config={
                    "cost": {"per_second_active": "0.001"},
                    "fallback_chain": ["prov_beta"],
                    "autopilot_allowed": True,
                },
            ),
            _provider(
                "prov_beta",
                priority=2,
                config={
                    "cost": {"per_second_active": "0.002"},
                    "fallback_for": ["prov_alpha"],
                    "autopilot_allowed": True,
                },
            ),
        )

    async def job_state_counts_loader(pool: asyncpg.Pool) -> dict[str, int]:
        return {"queued": 1, "completed": 2}

    async def recent_jobs_loader(pool: asyncpg.Pool) -> Sequence[JobEntry]:
        return (
            JobEntry(
                workload_id="job_alpha",
                capability_id="cap_alpha",
                provider_id="prov_alpha",
                state="completed",
                submitted_at=_NOW,
                cost_usd=Decimal("0.010000"),
            ),
        )

    def policy_loader() -> PolicySet:
        return PolicySet(
            policies=[
                Policy(
                    id="autopilot.provider-opt-in",
                    target=PolicyTarget.PROVIDER,
                    rules=[
                        PolicyRule(
                            path="config.autopilot_allowed",
                            operator="equals",
                            value=True,
                        )
                    ],
                )
            ]
        )

    def autopilot_runner(
        now: dt.datetime,
        policy_set: PolicySet,
        capabilities: Sequence[Capability],
        providers: Sequence[Provider],
    ) -> AutopilotRunResult:
        return AutopilotRunResult(
            now=now,
            mode=AutopilotMode.SHADOW,
            limits=AutopilotHardLimits(),
            decisions=(),
        )

    source = PostgresOperationsSource(
        pool_factory=pool_factory,
        capability_loader=capability_loader,
        provider_loader=provider_loader,
        job_state_counts_loader=job_state_counts_loader,
        recent_jobs_loader=recent_jobs_loader,
        policy_loader=policy_loader,
        autopilot_runner=autopilot_runner,
        guardrail_service=PreSpendInspectionService(),
        routing_settings=PitwallSettings(),
        now=lambda: _NOW,
    )

    snapshot = await source.load_operations()

    assert snapshot.catalog[0].as_row() == (
        "cap_alpha",
        "embedding.alpha",
        "embedding",
        "per_second",
        "enabled",
        "2",
    )
    assert snapshot.recent_jobs[0].as_row() == (
        "job_alpha",
        "cap_alpha",
        "prov_alpha",
        "completed",
        "2026-06-02 16:30 UTC",
        "$0.01",
    )
    assert snapshot.routing.selected_provider_id == "prov_alpha"
    assert snapshot.routing.fallback_chain == ("prov_alpha", "prov_beta")
    assert snapshot.routing.plan_id is not None
    assert snapshot.routing.objective == "1"
    assert snapshot.routing.cost_component is not None
    assert snapshot.routing.latency_component == "0.000"
    assert snapshot.routing.latency_ms == "0"
    assert snapshot.policy.summary == "Policies: allow | 1 policy | 0 violations"
    assert snapshot.autopilot.summary == "Autopilot: shadow | 0 decisions | 0 applied"
    assert snapshot.guardrails.counters.total == 0


async def test_pitwall_app_switches_to_operations_screen() -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        operations_source=StaticOperationsSource(_operations_snapshot()),
    )

    async with app.run_test(size=(130, 36)) as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()

        assert app.screen.name == "operations"
        assert str(app.screen.query_one("#operations-title", Static).content) == "Operations"
        assert "Volume files and bounded pod diagnostics" in str(
            app.screen.query_one("#volume-files-title", Static).content
        )
        assert "Production routing and jobs" in str(
            app.screen.query_one("#routing-jobs-title", Static).content
        )


async def test_operations_screen_renders_snapshot() -> None:
    source = StaticOperationsSource(_operations_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        operations_source=source,
    )

    async with app.run_test(size=(130, 36)) as pilot:
        await pilot.press("a")
        await pilot.pause()

        assert source.load_count == 1
        assert "Operations: 2 capabilities" in str(
            app.screen.query_one("#operations-summary", Static).content
        )
        assert "embedding.alpha" in str(app.screen.query_one("#catalog-table", Static).content)
        assert "job_alpha" in str(app.screen.query_one("#jobs-table", Static).content)
        assert "prov_beta" in str(app.screen.query_one("#resilience-table", Static).content)
        assert "Routing: embedding.alpha -> prov_alpha" in str(
            app.screen.query_one("#routing-summary", Static).content
        )
        assert "plan none | objective none" in str(
            app.screen.query_one("#routing-summary", Static).content
        )
        assert "Policies: allow" in str(app.screen.query_one("#policy-summary", Static).content)
        assert "Autopilot: shadow" in str(
            app.screen.query_one("#autopilot-summary", Static).content
        )
        assert "Guardrails: balanced | inspected 0" in str(
            app.screen.query_one("#guardrail-summary", Static).content
        )
        assert "secret_field" in str(app.screen.query_one("#guardrail-rules", Static).content)
        assert str(app.screen.query_one("#guardrail-last-decision", Static).content) == (
            "Last guardrail decision: none"
        )
        assert "Last refreshed: 2026-06-02 16:30 UTC" in str(
            app.screen.query_one("#operations-refreshed", Static).content
        )


async def test_operations_screen_refresh_reloads_source() -> None:
    source = StaticOperationsSource(_operations_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        operations_source=source,
    )

    async with app.run_test(size=(130, 36)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        assert source.load_count == 2


async def test_operations_screen_reports_source_failure() -> None:
    class FailingOperationsSource:
        async def load_operations(self) -> OperationsSnapshot:
            raise RuntimeError("boom")

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        operations_source=FailingOperationsSource(),
    )

    async with app.run_test(size=(130, 36)) as pilot:
        await pilot.press("a")
        await pilot.pause()

        assert str(app.screen.query_one("#operations-error", Static).content) == (
            "Operations unavailable: boom"
        )
        for widget_id in (
            "operations-summary",
            "guardrail-rules",
            "catalog-table",
            "jobs-table",
            "resilience-table",
        ):
            assert str(app.screen.query_one(f"#{widget_id}", Static).content) == "Unavailable"


async def test_operations_guardrail_preview_is_non_persisting_and_non_disclosing() -> None:
    service = PreSpendInspectionService()
    source = StaticOperationsSource(_operations_snapshot(), guardrail_service=service)
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        operations_source=source,
    )

    secret = "sk-abcdefghijklmnop12345678"
    async with app.run_test(size=(130, 42)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        preview_input = app.screen.query_one("#guardrail-preview-input", Input)
        preview_input.value = '{"token":"' + secret + '"}'
        app.screen.query_one("#guardrail-preview-button", Button).press()
        await pilot.pause()

        rendered = str(app.screen.query_one("#guardrail-preview-result", Static).content)
        assert "Guardrail preview: block" in rendered
        assert "secret_field@$.token" in rendered
        assert secret not in rendered
        assert preview_input.value == ""
        assert service.status().counters.total == 0


async def test_invalid_guardrail_preview_keeps_the_typed_payload() -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        operations_source=StaticOperationsSource(_operations_snapshot()),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        screen = app.screen
        screen.query_one("#guardrail-preview-input", Input).value = '{"messages": [}'
        screen.query_one("#guardrail-preview-button", Button).press()
        await pilot.pause()

        assert screen.query_one("#guardrail-preview-input", Input).value == '{"messages": [}'
        assert "unavailable" in str(screen.query_one("#guardrail-preview-result", Static).content)


async def test_operations_empty_snapshot_survives_resize_and_refresh() -> None:
    snapshot = replace(
        _operations_snapshot(), catalog=(), recent_jobs=(), resilience=(), job_state_counts={}
    )
    source = StaticOperationsSource(snapshot)
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()), operations_source=source
    )
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("a")
        await pilot.pause()
        for size in ((80, 24), (140, 45)):
            await pilot.resize_terminal(*size)
            await pilot.pause()
            summary = str(app.screen.query_one("#operations-summary", Static).content)
            assert "0 capabilities" in summary and "0 jobs" in summary
            assert "Loading" not in summary
            assert str(app.screen.query_one("#operations-error", Static).content) == ""
            assert "job_alpha" not in str(app.screen.query_one("#jobs-table", Static).content)
            assert "embedding.alpha" not in str(
                app.screen.query_one("#catalog-table", Static).content
            )
        await pilot.press("r")
        await pilot.pause()
        assert source.load_count == 2
