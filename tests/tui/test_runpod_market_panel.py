"""Pilot tests for the feature-local RunPod market panel."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from pitwall.runpod_client.discovery import DatacenterCatalogEntry, GpuCatalogEntry
from pitwall.runpod_market import (
    RunpodBillingCategoryState,
    RunpodCreditBalance,
    RunpodMarketComponent,
    RunpodMarketGpu,
    RunpodMarketRead,
)
from pitwall.tui.runpod_market import (
    RunpodMarketPanel,
    StaticRunpodMarketSource,
)

pytestmark = pytest.mark.anyio
_NOW = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)


def _snapshot() -> RunpodMarketRead:
    return RunpodMarketRead(
        state="fresh",
        refresh_attempted_at=_NOW,
        cache_expires_at=_NOW + dt.timedelta(minutes=5),
        age_seconds=0,
        cache_hit=False,
        forced_refresh=False,
        components=(
            RunpodMarketComponent(
                name="graphql_discovery",
                source="runpod-graphql",
                state="fresh",
                observed_at=_NOW,
            ),
            RunpodMarketComponent(
                name="rest_v2_catalogue",
                source="runpod-rest-v2-catalogue",
                state="fresh",
                observed_at=_NOW,
            ),
            RunpodMarketComponent(
                name="graphql_balance",
                source="runpod-graphql",
                state="fresh",
                observed_at=_NOW,
            ),
        ),
        gpus=(
            RunpodMarketGpu(
                gpu_type_id="NVIDIA L4",
                graphql=GpuCatalogEntry(
                    gpu_type_id="NVIDIA L4",
                    secure_cloud=True,
                    secure_price=Decimal("0.44"),
                    lowest_bid_price=Decimal("0.19"),
                    stock_status="High",
                ),
            ),
        ),
        datacenters=(
            DatacenterCatalogEntry(
                datacenter_id="US-KS-2",
                location="United States",
                gpu_types=("NVIDIA L4",),
                gpu_availability={"NVIDIA L4": True},
            ),
        ),
        balance=RunpodCreditBalance(
            client_balance_usd=Decimal("42.25"),
            current_spend_per_hr_usd=Decimal("1.125"),
            spend_limit_usd=Decimal("100"),
            min_balance_usd=Decimal("5"),
            under_balance=False,
        ),
        billing_categories=(
            RunpodBillingCategoryState(
                category="pods",
                history_read_supported=True,
                workload_actual_supported=True,
                identity_field="pod_id",
                actual_state="supported",
            ),
            RunpodBillingCategoryState(
                category="endpoints",
                history_read_supported=True,
                workload_actual_supported=False,
                identity_field="endpoint_id",
                actual_state="unavailable",
            ),
            RunpodBillingCategoryState(
                category="network_volumes",
                history_read_supported=True,
                workload_actual_supported=False,
                identity_field=None,
                actual_state="unavailable",
            ),
        ),
    )


class _PanelApp(App[None]):
    def __init__(self, source: object) -> None:
        super().__init__()
        self.source = source

    def compose(self) -> ComposeResult:
        yield RunpodMarketPanel(self.source)  # type: ignore[arg-type]  # reason: focused source fake


async def test_panel_renders_populated_and_forced_refresh_states() -> None:
    source = StaticRunpodMarketSource(_snapshot())
    app = _PanelApp(source)

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert "fresh | 0s old" in str(app.query_one("#runpod-market-state", Static).content)
        assert "NVIDIA L4 | 0.44" in str(app.query_one("#runpod-market-gpus", Static).content)
        assert "US-KS-2" in str(app.query_one("#runpod-market-datacenters", Static).content)
        assert "$42.25 USD" in str(app.query_one("#runpod-market-balance", Static).content)
        assert "pods: actual" in str(app.query_one("#runpod-market-billing", Static).content)
        await app.query_one(RunpodMarketPanel).load_snapshot(force_refresh=True)

    assert source.calls == [False, True]


@pytest.mark.parametrize(
    ("snapshot", "state_text", "gpu_text", "balance_text"),
    [
        (
            replace(_snapshot(), gpus=(), datacenters=(), balance=None),
            "fresh",
            "No GPU types reported",
            "Balance unavailable",
        ),
        (
            replace(_snapshot(), state="stale", age_seconds=601),
            "stale cached data",
            "NVIDIA L4",
            "$42.25 USD",
        ),
        (
            replace(
                _snapshot(),
                state="unavailable",
                gpus=(),
                datacenters=(),
                balance=None,
            ),
            "unavailable",
            "No GPU types reported",
            "Balance unavailable",
        ),
    ],
)
async def test_panel_renders_empty_stale_and_unavailable_states(
    snapshot: RunpodMarketRead,
    state_text: str,
    gpu_text: str,
    balance_text: str,
) -> None:
    app = _PanelApp(StaticRunpodMarketSource(snapshot))

    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        assert state_text in str(app.query_one("#runpod-market-state", Static).content)
        assert gpu_text in str(app.query_one("#runpod-market-gpus", Static).content)
        assert balance_text in str(app.query_one("#runpod-market-balance", Static).content)


async def test_panel_error_is_inline_and_redacted() -> None:
    class FailingSource:
        async def load_runpod_market(self, *, force_refresh: bool) -> RunpodMarketRead:
            del force_refresh
            raise RuntimeError("Authorization: Bearer rpa_secret_should_not_render")

    app = _PanelApp(FailingSource())
    async with app.run_test(size=(100, 28)) as pilot:
        await pilot.pause()
        error = str(app.query_one("#runpod-market-error", Static).content)

    assert "RunPod catalogue unavailable" in error
    assert "[REDACTED]" in error
    assert "rpa_secret_should_not_render" not in error
