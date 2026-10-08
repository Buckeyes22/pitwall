"""Personal serving source boundary and console screens."""

from __future__ import annotations

import datetime as dt
import json
import os
from collections.abc import Callable
from typing import Protocol, cast

from pydantic import BaseModel, ValidationError
from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from pitwall.config import get_settings
from pitwall.models.fit import FitOption
from pitwall.personal.keys import ENDPOINT_KEY_ENV
from pitwall.personal.service import (
    PersonalServeService,
    ServeFailed,
    ServePreview,
    ServeRefused,
    ServeSpec,
    build_personal_service,
)
from pitwall.personal.state import PersonalLease
from pitwall.tui.errors import source_failure_message


class PersonalServeSource(Protocol):
    """Async boundary between personal console widgets and serving operations."""

    async def preview(self, spec: ServeSpec) -> ServePreview: ...

    async def serve(self, spec: ServeSpec, progress: Callable[[str], None]) -> PersonalLease: ...

    async def status(self) -> list[PersonalLease]: ...

    async def stop(self, route: str) -> PersonalLease: ...

    async def logs(self, route: str) -> str: ...

    def key_env_present(self) -> bool: ...

    async def gpu_choices(
        self,
        model: str,
        variant: str | None,
        cloud: str,
        ttl_minutes: int,
    ) -> list[FitOption]: ...


class ServicePersonalSource:
    """Production adapter for :class:`PersonalServeService`."""

    def __init__(self, service: PersonalServeService | None = None) -> None:
        self._built = service

    @property
    def _service(self) -> PersonalServeService:
        # Built on first use so a missing RunPod key reaches the screen as an error line
        # (RunPodCredentialMissing) instead of crashing the console.
        if self._built is None:
            self._built = build_personal_service(get_settings())
        return self._built

    async def preview(self, spec: ServeSpec) -> ServePreview:
        return await self._service.plan(spec)

    async def serve(self, spec: ServeSpec, progress: Callable[[str], None]) -> PersonalLease:
        return await self._service.serve(spec, progress=progress)

    async def status(self) -> list[PersonalLease]:
        return await self._service.status()

    async def stop(self, route: str) -> PersonalLease:
        return await self._service.stop(route)

    async def logs(self, route: str) -> str:
        return await self._service.logs(route)

    def key_env_present(self) -> bool:
        return bool(os.environ.get(ENDPOINT_KEY_ENV, "").strip())

    async def gpu_choices(
        self,
        model: str,
        variant: str | None,
        cloud: str,
        ttl_minutes: int,
    ) -> list[FitOption]:
        # PersonalServeService currently plans a selected GPU but does not expose
        # catalogue-wide fit discovery. The wizard therefore accepts the GPU class
        # directly until that engine API exists.
        return []


class StaticPersonalSource:
    """Hermetic scripted personal source with exact call recording."""

    def __init__(
        self,
        *,
        preview: ServePreview | None = None,
        lease: PersonalLease | None = None,
        leases: list[PersonalLease] | None = None,
        logs: str = "",
        key_present: bool = True,
        choices: list[FitOption] | None = None,
        preview_error: Exception | None = None,
        serve_error: Exception | None = None,
    ) -> None:
        self._preview = preview
        self._lease = lease
        self._leases = list(leases if leases is not None else ([] if lease is None else [lease]))
        self._logs = logs
        self._key_present = key_present
        self._choices = list(choices or [])
        self._preview_error = preview_error
        self._serve_error = serve_error
        self.preview_specs: list[ServeSpec] = []
        self.serve_specs: list[ServeSpec] = []
        self.serve_calls = 0
        self.stopped: list[str] = []

    async def preview(self, spec: ServeSpec) -> ServePreview:
        self.preview_specs.append(spec)
        if self._preview_error is not None:
            raise self._preview_error
        if self._preview is None:
            raise RuntimeError("no scripted preview")
        return self._preview

    async def serve(self, spec: ServeSpec, progress: Callable[[str], None]) -> PersonalLease:
        self.serve_calls += 1
        self.serve_specs.append(spec)
        if self._serve_error is not None:
            raise self._serve_error
        if self._lease is None:
            raise RuntimeError("no scripted lease")
        progress("creating pod")
        return self._lease

    async def status(self) -> list[PersonalLease]:
        return list(self._leases)

    async def stop(self, route: str) -> PersonalLease:
        self.stopped.append(route)
        for index, lease in enumerate(self._leases):
            if lease.route == route:
                stopped = lease.model_copy(update={"state": "stopped"})
                self._leases[index] = stopped
                return stopped
        raise KeyError(route)

    async def logs(self, route: str) -> str:
        if not any(lease.route == route for lease in self._leases):
            raise KeyError(route)
        return self._logs

    def key_env_present(self) -> bool:
        return self._key_present

    async def gpu_choices(
        self,
        model: str,
        variant: str | None,
        cloud: str,
        ttl_minutes: int,
    ) -> list[FitOption]:
        return list(self._choices)


class ServeWizardScreen(Screen[None]):
    """Preview, type-to-confirm, and launch one personal model route."""

    BINDINGS = [Binding("escape", "back", "Back")]

    def __init__(self, source: PersonalServeSource) -> None:
        super().__init__(name="serve")
        self._source = source
        self._previewed_spec: ServeSpec | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="personal-serve-panel"):
            yield Static("Serve a model", id="personal-serve-title")
            yield Label("Model")
            yield Input(id="serve-model")
            yield Label("Variant (optional)")
            yield Input(id="serve-variant")
            yield Label("GPU class")
            yield Input(id="serve-gpu-class")
            with Horizontal(id="serve-cloud-ttl"):
                yield Input(value="community", id="serve-cloud")
                yield Input(value="60", id="serve-ttl", type="integer")
            yield Label("Maximum USD per hour")
            yield Input(value="1.00", id="serve-cap", type="number")
            yield Label("Route name")
            yield Input(id="serve-route")
            yield Button("Preview", id="serve-preview", variant="primary")
            yield Static("", id="serve-preview-result")
            yield Label("Type the route name exactly to launch")
            yield Input(id="serve-confirm-text")
            yield Button("Launch", id="serve-launch", variant="error")
            yield Static("", id="serve-status")
        yield Footer()

    @on(Button.Pressed, "#serve-preview")
    @work(exclusive=True, group="personal-preview")
    async def preview_request(self) -> None:
        result = self.query_one("#serve-preview-result", Static)
        self._previewed_spec = None
        try:
            spec = self._spec()
            preview = await self._source.preview(spec)
        except ServeRefused as exc:
            result.update(_service_error("refused", exc.code, exc.detail))
            return
        except (ValueError, ValidationError) as exc:
            result.update(f"invalid request: {exc}")
            return
        except Exception as exc:  # reason: preview failures remain inline in the console
            result.update(source_failure_message("preview failed", exc))
            return
        self._previewed_spec = spec
        request = json.dumps(
            preview.request_preview,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        result.update(
            "\n".join(
                (
                    request,
                    f"price: {_money(preview.price_per_hour_usd)}/hour",
                    f"max spend: {_money(preview.max_spend_usd)}",
                    f"deadline: {preview.deadline_at.isoformat()}",
                )
            )
        )

    @on(Button.Pressed, "#serve-launch")
    @work(exclusive=True, group="personal-launch")
    async def launch_request(self) -> None:
        status = self.query_one("#serve-status", Static)
        try:
            spec = self._spec()
        except (ValueError, ValidationError) as exc:
            status.update(f"invalid request: {exc}")
            return
        confirmation = self.query_one("#serve-confirm-text", Input).value
        if confirmation != spec.route:
            status.update("type the route name exactly to launch")
            return
        if self._previewed_spec != spec:
            status.update("preview this exact request before launch")
            return
        progress_lines: list[str] = []

        def progress(message: str) -> None:
            progress_lines.append(message)
            status.update("\n".join(progress_lines))

        try:
            lease = await self._source.serve(spec, progress)
        except ServeRefused as exc:
            status.update(_service_error("refused", exc.code, exc.detail))
            return
        except ServeFailed as exc:
            message = f"failed after launch: {exc.code}; pod termination attempted"
            if exc.log_tail:
                # Pod logs are full of bracketed tags; show them as text, not markup.
                message += "\npod log tail:\n" + escape(exc.log_tail)
            status.update(message)
            return
        except Exception as exc:  # reason: launch failures remain inline in the console
            status.update(source_failure_message("launch failed", exc))
            return
        progress_lines.append(f"{lease.state}: {lease.route} · {lease.endpoint_url}")
        status.update("\n".join(progress_lines))

    async def action_back(self) -> None:
        await self.app.switch_screen("models")

    def _spec(self) -> ServeSpec:
        variant = self.query_one("#serve-variant", Input).value.strip() or None
        return ServeSpec.model_validate(
            {
                "model": self.query_one("#serve-model", Input).value.strip(),
                "variant": variant,
                "gpu_class": self.query_one("#serve-gpu-class", Input).value.strip(),
                "cloud": self.query_one("#serve-cloud", Input).value.strip(),
                "ttl_minutes": self.query_one("#serve-ttl", Input).value,
                "max_usd_per_hour": self.query_one("#serve-cap", Input).value,
                "route": self.query_one("#serve-route", Input).value.strip(),
            }
        )


class PodsScreen(Screen[None]):
    """List personal pods, show their logs, and stop them safely."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("r", "refresh", "Refresh")]

    def __init__(self, source: PersonalServeSource) -> None:
        super().__init__(name="pods")
        self._source = source
        self._leases: dict[str, PersonalLease] = {}
        self._selected_route: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="pods-panel"):
            yield Static("Pods", id="pods-title")
            table: DataTable[str] = DataTable(id="pods-table")
            table.cursor_type = "row"
            table.zebra_stripes = True
            yield table
            yield Static("", id="pods-logs")
            yield Button("Stop", id="pods-stop", variant="error", disabled=True)
            yield Static("", id="pods-confirm-prompt")
            yield Input(id="pods-confirm-text")
            yield Button("Confirm stop", id="pods-confirm-stop", variant="error")
            yield Static("", id="pods-status")
        yield Footer()

    async def on_mount(self) -> None:
        self._table().add_columns("Route", "Model", "GPU", "State", "Ends in", "Price/h")
        await self._refresh()

    @on(DataTable.RowSelected, "#pods-table")
    async def select_pod(self, event: DataTable.RowSelected) -> None:
        self._selected_route = str(event.row_key.value)
        self.query_one("#pods-stop", Button).disabled = False
        await self._load_logs(self._selected_route)

    @on(Button.Pressed, "#pods-stop")
    def open_stop_confirmation(self) -> None:
        route = self._selected_route
        if route is None:
            return
        self.query_one("#pods-confirm-prompt", Static).update(
            f"Type {route} exactly to stop this pod"
        )
        self.query_one("#pods-confirm-text", Input).value = ""
        self.query_one("#pods-status", Static).update("")

    @on(Button.Pressed, "#pods-confirm-stop")
    @work(exclusive=True, group="personal-stop")
    async def stop_pod(self) -> None:
        route = self._selected_route
        status = self.query_one("#pods-status", Static)
        if route is None:
            status.update("select a pod to stop")
            return
        confirmation = self.query_one("#pods-confirm-text", Input).value
        if confirmation != route:
            status.update("type the route name exactly to stop")
            return
        try:
            lease = await self._source.stop(route)
        except Exception as exc:  # reason: stop failures remain inline in the console
            status.update(source_failure_message("stop failed", exc))
            return
        status.update(f"{lease.route}: {lease.state}")
        await self._refresh()

    async def action_refresh(self) -> None:
        await self._refresh()

    async def action_back(self) -> None:
        await self.app.switch_screen("serve")

    async def _refresh(self) -> None:
        status = self.query_one("#pods-status", Static)
        try:
            leases = await self._source.status()
        except Exception as exc:  # reason: status failures remain inline in the console
            self._table().clear()
            self._leases = {}
            self._selected_route = None
            self.query_one("#pods-stop", Button).disabled = True
            status.update(source_failure_message("pods unavailable", exc))
            return
        self._leases = {lease.route: lease for lease in leases}
        table = self._table()
        table.clear()
        for lease in leases:
            table.add_row(
                lease.route,
                lease.model,
                f"{lease.gpu_count}× {lease.gpu_class}",
                lease.state,
                _ends_in(lease.deadline_at),
                _money(lease.price_per_hour_usd),
                key=lease.route,
            )
        if not leases:
            self._selected_route = None
            self.query_one("#pods-stop", Button).disabled = True
            self.query_one("#pods-logs", Static).update("No personal pods")
            return
        if self._selected_route not in self._leases:
            self._selected_route = leases[0].route
        self.query_one("#pods-stop", Button).disabled = False
        await self._load_logs(self._selected_route)

    async def _load_logs(self, route: str) -> None:
        logs = self.query_one("#pods-logs", Static)
        try:
            content = await self._source.logs(route)
        except Exception as exc:  # reason: log failures remain inline in the console
            logs.update(source_failure_message("logs unavailable", exc))
            return
        logs.update(content or "No pod logs")

    def _table(self) -> DataTable[str]:
        return cast(DataTable[str], self.query_one("#pods-table", DataTable))


class RoutesScreen(Screen[None]):
    """List personal routes and their local shim invocation."""

    BINDINGS = [Binding("escape", "back", "Back"), Binding("r", "refresh", "Refresh")]

    def __init__(self, source: PersonalServeSource) -> None:
        super().__init__(name="routes")
        self._source = source
        self._leases: dict[str, PersonalLease] = {}
        self._selected_route: str | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="routes-panel"):
            yield Static("Routes", id="routes-title")
            table: DataTable[str] = DataTable(id="routes-table")
            table.cursor_type = "row"
            table.zebra_stripes = True
            yield table
            yield Static("", id="routes-detail")
            yield Static("", id="routes-status")
        yield Footer()

    async def on_mount(self) -> None:
        self._table().add_columns("Route", "Endpoint", "Served model", "State")
        await self._refresh()

    @on(DataTable.RowSelected, "#routes-table")
    def select_route(self, event: DataTable.RowSelected) -> None:
        self._selected_route = str(event.row_key.value)
        self._render_detail()

    async def action_refresh(self) -> None:
        await self._refresh()

    async def action_back(self) -> None:
        await self.app.switch_screen("serve")

    async def _refresh(self) -> None:
        status = self.query_one("#routes-status", Static)
        try:
            leases = await self._source.status()
        except Exception as exc:  # reason: status failures remain inline in the console
            self._table().clear()
            self._leases = {}
            self._selected_route = None
            self.query_one("#routes-detail", Static).update("")
            status.update(source_failure_message("routes unavailable", exc))
            return
        status.update("")
        self._leases = {lease.route: lease for lease in leases}
        table = self._table()
        table.clear()
        for lease in leases:
            table.add_row(
                lease.route,
                lease.endpoint_url,
                lease.served_model_id,
                lease.state,
                key=lease.route,
            )
        if not leases:
            self._selected_route = None
            self.query_one("#routes-detail", Static).update("No personal routes")
            return
        if self._selected_route not in self._leases:
            self._selected_route = leases[0].route
        self._render_detail()

    def _render_detail(self) -> None:
        route = self._selected_route
        lease = self._leases.get(route) if route is not None else None
        if lease is None:
            self.query_one("#routes-detail", Static).update("")
            return
        lines = [
            f"Last probe: {lease.state}",
            f"route-shim.sh {lease.route} prompt.md",
        ]
        if not self._source.key_env_present():
            lines.append(f"Warning: {ENDPOINT_KEY_ENV} is not set")
        self.query_one("#routes-detail", Static).update("\n".join(lines))

    def _table(self) -> DataTable[str]:
        return cast(DataTable[str], self.query_one("#routes-table", DataTable))


def _money(value: object) -> str:
    return "unpriced" if value is None else f"${value}"


def _ends_in(deadline: dt.datetime) -> str:
    now = dt.datetime.now(deadline.tzinfo) if deadline.tzinfo is not None else dt.datetime.now()
    seconds = max(0, int((deadline - now).total_seconds()))
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes}m" if hours else f"{minutes}m"


def _service_error(prefix: str, code: str, detail: str) -> str:
    return f"{prefix}: {code}{f' {detail}' if detail else ''}"


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)


__all__ = [
    "PersonalServeSource",
    "PodsScreen",
    "RoutesScreen",
    "ServicePersonalSource",
    "ServeWizardScreen",
    "StaticPersonalSource",
]
