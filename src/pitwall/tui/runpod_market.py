"""Injectable TUI panel for the shared RunPod market read."""

from __future__ import annotations

from typing import Protocol

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static

from pitwall.runpod_market import RunpodMarketRead, RunpodMarketService
from pitwall.security.redaction import redact_text
from pitwall.tui.errors import source_failure_message


class RunpodMarketSource(Protocol):
    """Async source boundary used by the embedded market panel."""

    async def load_runpod_market(self, *, force_refresh: bool) -> RunpodMarketRead:
        """Return the shared semantic read."""


class ServiceRunpodMarketSource:
    """Production source backed by one process-local product service."""

    def __init__(self, service: RunpodMarketService) -> None:
        self._service = service

    async def load_runpod_market(self, *, force_refresh: bool) -> RunpodMarketRead:
        return await self._service.read(force_refresh=force_refresh)


class StaticRunpodMarketSource:
    """Hermetic source for Pilot tests and local snapshots."""

    def __init__(self, snapshot: RunpodMarketRead) -> None:
        self._snapshot = snapshot
        self.calls: list[bool] = []

    async def load_runpod_market(self, *, force_refresh: bool) -> RunpodMarketRead:
        self.calls.append(force_refresh)
        return self._snapshot


class RunpodMarketPanel(Vertical):
    """Catalogue and balance panel intended for the existing Resources view."""

    def __init__(self, source: RunpodMarketSource, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._source = source

    def compose(self) -> ComposeResult:
        yield Static("RunPod catalogue", classes="resource-section")
        yield Static("Loading RunPod catalogue", id="runpod-market-state", classes="summary")
        yield Static("", id="runpod-market-gpus", classes="resource-table")
        yield Static("", id="runpod-market-datacenters", classes="resource-table")
        yield Static("Loading RunPod balance", id="runpod-market-balance", classes="summary")
        yield Static("Loading billing support", id="runpod-market-billing", classes="summary")
        yield Static("", id="runpod-market-error", classes="error")

    async def on_mount(self) -> None:
        await self.load_snapshot()

    async def load_snapshot(self, *, force_refresh: bool = False) -> None:
        """Load and render one shared snapshot; refresh never performs a mutation."""

        self.query_one("#runpod-market-error", Static).update("")
        try:
            snapshot = await self._source.load_runpod_market(force_refresh=force_refresh)
        except (
            Exception
        ) as exc:  # reason: source errors stay inline and redact through shared helper
            self.query_one("#runpod-market-error", Static).update(
                source_failure_message(
                    "RunPod catalogue unavailable",
                    RuntimeError(redact_text(exc)),
                )
            )
            return
        self._render_snapshot(snapshot)

    def _render_snapshot(self, snapshot: RunpodMarketRead) -> None:
        state = f"{snapshot.state} | {snapshot.age_seconds}s old"
        if snapshot.stale:
            state = f"stale cached data | {snapshot.age_seconds}s old"
        elif snapshot.unavailable:
            state = "unavailable"
        self.query_one("#runpod-market-state", Static).update(state)
        self.query_one("#runpod-market-gpus", Static).update(_gpu_table(snapshot))
        self.query_one("#runpod-market-datacenters", Static).update(_datacenter_table(snapshot))
        balance = snapshot.balance
        self.query_one("#runpod-market-balance", Static).update(
            "Balance unavailable"
            if balance is None
            else (
                f"Credit balance: ${balance.client_balance_usd} USD | "
                f"spend/hr: {_optional(balance.current_spend_per_hr_usd)}"
            )
        )
        supported = ", ".join(
            f"{item.category}: "
            f"{'actual' if item.workload_actual_supported and item.actual_state == 'supported' else 'unavailable'}"
            for item in snapshot.billing_categories
        )
        self.query_one("#runpod-market-billing", Static).update(
            f"Billing truth-up: {supported or 'empty'}"
        )


def _gpu_table(snapshot: RunpodMarketRead) -> str:
    if not snapshot.gpus:
        return "No GPU types reported"
    lines = ["GPU | Secure $/hr | Community $/hr | Minimum bid | Stock"]
    for row in snapshot.gpus:
        gpu = row.graphql
        lines.append(
            " | ".join(
                (
                    row.gpu_type_id,
                    _optional(gpu.secure_price if gpu else None),
                    _optional(gpu.community_price if gpu else None),
                    _optional(gpu.lowest_bid_price if gpu else None),
                    gpu.stock_status or "unavailable" if gpu else "unavailable",
                )
            )
        )
    return "\n".join(lines)


def _datacenter_table(snapshot: RunpodMarketRead) -> str:
    if not snapshot.datacenters:
        return "No datacenters reported"
    lines = ["Datacenter | Location | GPU types"]
    lines.extend(
        f"{item.datacenter_id} | {item.location or 'unavailable'} | {len(item.gpu_types)}"
        for item in snapshot.datacenters
    )
    return "\n".join(lines)


def _optional(value: object | None) -> str:
    return str(value) if value is not None else "unavailable"


__all__ = [
    "RunpodMarketPanel",
    "RunpodMarketSource",
    "ServiceRunpodMarketSource",
    "StaticRunpodMarketSource",
]
