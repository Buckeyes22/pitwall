"""Textual application shell for the Pitwall operator console."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import asyncpg
import rich
from rich.segment import Segments
from rich.traceback import Traceback
from textual.app import App
from textual.binding import Binding

from pitwall.config import load_settings_from_env
from pitwall.models import load_catalogue
from pitwall.onboarding import RunPodOnboardingService, create_runpod_onboarding_service
from pitwall.personal.backend import select_backend
from pitwall.runpod_files import VolumeFileService, build_configured_volume_file_service
from pitwall.runpod_market import RunpodMarketService, build_configured_runpod_market_service
from pitwall.tui.console import CommandScreen, SearchScreen
from pitwall.tui.cost import CostScreen, CostSource, PostgresCostSource
from pitwall.tui.hardware_fit import HardwareFitSource, LiveHardwareFitSource
from pitwall.tui.help import HelpScreen
from pitwall.tui.leases import LeasesScreen, LeasesSnapshot, LeasesSource, PostgresLeasesSource
from pitwall.tui.models import LocalModelCatalogueSource, ModelCatalogueSource, ModelsScreen
from pitwall.tui.onboarding import OnboardingSource, ServiceOnboardingSource
from pitwall.tui.operations import OperationsScreen, OperationsSource, PostgresOperationsSource
from pitwall.tui.overview import (
    OverviewScreen,
    OverviewSnapshot,
    OverviewSource,
    PostgresOverviewSource,
)
from pitwall.tui.personal import (
    PersonalServeSource,
    PodsScreen,
    RoutesScreen,
    ServeWizardScreen,
    ServicePersonalSource,
)
from pitwall.tui.providers import ProvidersScreen, ProvidersSource, ServiceProvidersSource
from pitwall.tui.resources import ResourcesScreen, ResourcesSource, RunPodResourcesSource
from pitwall.tui.runpod_market import (
    RunpodMarketSource,
    ServiceRunpodMarketSource,
)
from pitwall.tui.serve import PitwallServeActionSource, ServeActionSource
from pitwall.tui.volume_files import ServiceVolumeFilesOperationsSource

PoolFactory = Callable[[], Awaitable[asyncpg.Pool]]


class _DeferredPoolOverviewSource:
    """Open the pool inside each refresh, so an unreachable database is a screen error."""

    def __init__(self, pool_factory: PoolFactory) -> None:
        self._pool_factory = pool_factory

    async def load_overview(self) -> OverviewSnapshot:
        return await PostgresOverviewSource(await self._pool_factory()).load_overview()


class _DeferredPoolLeasesSource:
    """Open the pool inside each refresh, so an unreachable database is a screen error."""

    def __init__(self, pool_factory: PoolFactory) -> None:
        self._pool_factory = pool_factory

    async def load_leases(self) -> LeasesSnapshot:
        return await PostgresLeasesSource(await self._pool_factory()).load_leases()


class PitwallApp(App[None]):
    """Textual shell with read views and a guarded model-launch action."""

    TITLE = "Pitwall"
    SUB_TITLE = "Operator Console"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("?", "show_help", "Help"),
        Binding(":", "show_command", "Command"),
        Binding("/", "show_search", "Search"),
        Binding("o", "show_overview", "Overview"),
        Binding("p", "show_providers", "Providers"),
        Binding("l", "show_leases", "Leases"),
        Binding("m", "show_models", "Models"),
        Binding("s", "show_serve", "Serve"),
        Binding("d", "show_pods", "Pods"),
        Binding("t", "show_routes", "Routes"),
        Binding("c", "show_cost", "Cost"),
        Binding("e", "show_resources", "Resources"),
        Binding("a", "show_operations", "Operations"),
    ]
    CSS = """
    Screen {
        layout: vertical;
    }

    #shell {
        height: 1fr;
    }

    #nav-panel {
        width: 24;
        padding: 1;
        border-right: solid $primary;
        background: $panel;
    }

    #nav-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #shell-nav {
        height: auto;
    }

    #overview-panel {
        padding: 1 2;
        width: 1fr;
    }

    #leases-panel {
        padding: 1 2;
        width: 1fr;
    }

    #models-panel {
        padding: 1 2;
        width: 1fr;
    }

    #pods-panel, #routes-panel {
        padding: 1 2;
        width: 1fr;
    }

    #pods-table, #routes-table {
        height: 1fr;
    }

    #pods-logs, #routes-detail {
        height: auto;
        margin-top: 1;
    }

    #models-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #models-table {
        height: 1fr;
    }

    #models-details, #fit-details {
        height: auto;
        margin-top: 1;
    }

    #help-dialog {
        width: 60;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $primary;
        background: $panel;
    }

    #console-command-dialog, #console-search-dialog {
        width: 60;
        height: auto;
        padding: 1 2;
        border: thick $primary;
        background: $panel;
    }

    #console-command-title, #console-search-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #help-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #fit-panel {
        padding: 1 2;
        width: 1fr;
    }

    #fit-table {
        height: 1fr;
    }

    #model-dossier {
        height: 1fr;
        padding: 1 2;
    }

    #overview-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #providers-panel {
        padding: 1 2;
        width: 1fr;
    }

    #providers-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #leases-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #providers-summary {
        margin-bottom: 1;
    }

    #leases-table {
        height: 1fr;
        margin-top: 1;
    }

    #cost-panel {
        padding: 1 2;
        width: 1fr;
    }

    #cost-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #resources-panel {
        padding: 1 2;
        width: 1fr;
    }

    #resources-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #resource-actions, #guardrail-preview-controls {
        height: auto;
    }

    #guardrail-preview-input {
        width: 1fr;
    }

    #operations-panel {
        padding: 1 2;
        width: 1fr;
    }

    #operations-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #serve-cloud-ttl {
        height: auto;
    }

    #serve-cloud, #serve-ttl {
        width: 1fr;
    }

    .resource-section {
        text-style: bold;
        margin-top: 1;
    }

    .operation-section {
        text-style: bold;
        margin-top: 1;
    }

    .resource-table {
        margin-bottom: 1;
    }

    .operation-table {
        margin-bottom: 1;
    }

    #sub-budget-table {
        margin-top: 1;
        margin-bottom: 1;
    }

    #metric-row {
        height: auto;
        margin-bottom: 1;
    }

    .metric {
        width: 1fr;
        height: auto;
        min-height: 4;
        padding: 1 2;
        margin-right: 1;
        border: solid $accent;
    }

    .summary {
        height: 1;
        margin-top: 1;
    }

    .error {
        color: $error;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        *,
        overview_source: OverviewSource | None = None,
        providers_source: ProvidersSource | None = None,
        leases_source: LeasesSource | None = None,
        models_source: ModelCatalogueSource | None = None,
        hardware_fit_source: HardwareFitSource | None = None,
        serve_action_source: ServeActionSource | None = None,
        personal_source: PersonalServeSource | None = None,
        cost_source: CostSource | None = None,
        resources_source: ResourcesSource | None = None,
        runpod_market_source: RunpodMarketSource | None = None,
        operations_source: OperationsSource | None = None,
        onboarding_source: OnboardingSource | None = None,
        pool_factory: PoolFactory | None = None,
    ) -> None:
        super().__init__()
        self._overview_source = overview_source
        self._providers_source = providers_source
        self._leases_source = leases_source
        self._models_source = models_source
        self._hardware_fit_source = hardware_fit_source
        self._serve_action_source = serve_action_source
        self._personal_source = personal_source
        self._personal_installed = False
        self._pool_factory = pool_factory
        self._pool: asyncpg.Pool | None = None
        self._leases_installed = False
        self._cost_source = cost_source
        self._resources_source = resources_source
        self._runpod_market_source = runpod_market_source
        self._runpod_market_service: RunpodMarketService | None = None
        self._operations_source = operations_source
        self._onboarding_source = onboarding_source

    async def on_mount(self) -> None:
        if self._runpod_market_source is None:
            self._runpod_market_service = build_configured_runpod_market_service()
            self._runpod_market_source = ServiceRunpodMarketSource(self._runpod_market_service)
        self._install_models()
        if select_backend() == "personal":
            self._install_personal()
            await self.push_screen("serve")
            return
        await self._install_overview()
        self._install_providers()
        self._install_cost()
        self._install_resources()
        self._install_operations()
        await self.push_screen("overview")

    async def action_show_overview(self) -> None:
        if self.screen.name == "overview" or not self.is_screen_installed("overview"):
            return
        await self.switch_screen("overview")

    async def action_show_providers(self) -> None:
        if self.screen.name == "providers" or not self.is_screen_installed("providers"):
            return
        await self.switch_screen("providers")

    async def action_show_leases(self) -> None:
        if self.screen.name == "leases":
            return
        if not self.is_screen_installed("overview"):
            return
        await self._install_leases()
        await self.switch_screen("leases")

    async def action_show_models(self) -> None:
        if self.screen.name == "models":
            return
        await self.switch_screen("models")

    async def action_show_serve(self) -> None:
        self._install_personal()
        if self.screen.name == "serve":
            return
        await self.switch_screen("serve")

    async def action_show_pods(self) -> None:
        self._install_personal()
        if self.screen.name == "pods":
            return
        await self.switch_screen("pods")

    async def action_show_routes(self) -> None:
        self._install_personal()
        if self.screen.name == "routes":
            return
        await self.switch_screen("routes")

    async def action_show_cost(self) -> None:
        if self.screen.name == "cost" or not self.is_screen_installed("cost"):
            return
        await self.switch_screen("cost")

    async def action_show_resources(self) -> None:
        if self.screen.name == "resources" or not self.is_screen_installed("resources"):
            return
        await self.switch_screen("resources")

    async def action_show_operations(self) -> None:
        if self.screen.name == "operations" or not self.is_screen_installed("operations"):
            return
        await self.switch_screen("operations")

    async def action_show_help(self) -> None:
        await self.push_screen(HelpScreen(self.screen))

    async def action_show_command(self) -> None:
        await self.push_screen(CommandScreen(), self._dispatch_command)

    async def action_show_search(self) -> None:
        screen = self.screen
        apply_filter = getattr(screen, "apply_filter", None)
        if callable(apply_filter):
            await self.push_screen(SearchScreen(apply_filter, self._show_filter))

    def _show_filter(self, query: str) -> None:
        """Keep an applied list filter visible after its input overlay closes."""
        normalized = query.strip()
        self.sub_title = (
            f"filter: {normalized} — / to change, Esc to clear"
            if normalized
            else "Operator Console"
        )

    async def _dispatch_command(self, command: str | None) -> None:
        if command is None:
            return
        actions: dict[str, Callable[[], Awaitable[None]]] = {
            "overview": self.action_show_overview,
            "providers": self.action_show_providers,
            "leases": self.action_show_leases,
            "models": self.action_show_models,
            "serve": self.action_show_serve,
            "pods": self.action_show_pods,
            "routes": self.action_show_routes,
            "cost": self.action_show_cost,
            "resources": self.action_show_resources,
            "operations": self.action_show_operations,
            "help": self.action_show_help,
            "refresh": self._refresh_active_screen,
        }
        action = actions.get(command.strip().casefold())
        if action is not None:
            await action()

    async def _refresh_active_screen(self) -> None:
        screen = self.screen
        refresh = getattr(screen, "action_refresh", None)
        if refresh is not None:
            await refresh()

    async def _install_overview(self) -> None:
        source = await self._resolve_overview_source()
        self.install_screen(OverviewScreen(source), "overview")

    def _install_providers(self) -> None:
        source = self._providers_source or ServiceProvidersSource(pool_factory=self._resolve_pool)
        self._providers_source = source
        self.install_screen(ProvidersScreen(source, self._resolve_onboarding_source()), "providers")

    def _install_models(self) -> None:
        source = self._models_source or LocalModelCatalogueSource()
        self._models_source = source
        hardware_fit_source = self._hardware_fit_source or LiveHardwareFitSource(
            market_service=self._runpod_market_service
        )
        self._hardware_fit_source = hardware_fit_source
        if self._serve_action_source is None and self._pool is not None:
            settings = load_settings_from_env()
            base_url = settings.pitwall_base_url.strip().rstrip("/") or "http://127.0.0.1:8080"
            self._serve_action_source = PitwallServeActionSource(
                self._pool,
                base_url=base_url,
                settings=settings,
                catalogue=load_catalogue(),
            )
        self.install_screen(ModelsScreen(source, hardware_fit_source), "models")

    @property
    def serve_action_source(self) -> ServeActionSource | None:
        """Return the injected or production serve action boundary."""
        return self._serve_action_source

    def _install_personal(self) -> None:
        if self._personal_installed:
            return
        source = self._personal_source or ServicePersonalSource()
        self._personal_source = source
        self.install_screen(ServeWizardScreen(source), "serve")
        self.install_screen(PodsScreen(source), "pods")
        self.install_screen(RoutesScreen(source), "routes")
        self._personal_installed = True

    async def _install_leases(self) -> None:
        if self._leases_installed:
            return
        source = await self._resolve_leases_source()
        self.install_screen(LeasesScreen(source), "leases")
        self._leases_installed = True

    def _install_cost(self) -> None:
        source = self._cost_source or PostgresCostSource(pool_factory=self._resolve_pool)
        self._cost_source = source
        self.install_screen(CostScreen(source), "cost")

    def _install_resources(self) -> None:
        source = self._resources_source or RunPodResourcesSource()
        self._resources_source = source
        market_source = self._runpod_market_source
        if market_source is None:
            self._runpod_market_service = build_configured_runpod_market_service()
            market_source = ServiceRunpodMarketSource(self._runpod_market_service)
            self._runpod_market_source = market_source
        self.install_screen(
            ResourcesScreen(source, market_source, self._resolve_onboarding_source()),
            "resources",
        )

    def _fatal_error(self) -> None:
        """Exit after an unhandled exception without printing frame locals.

        Textual's default crash report renders every frame's local variables, and those
        include `DATABASE_URL` and the other secrets the console reads from the environment.
        """
        self.bell()
        report = Traceback(show_locals=False, width=None, suppress=[rich])
        self._exit_renderables.append(Segments(self.console.render(report, self.console.options)))
        self._close_messages_no_wait()

    async def on_unmount(self) -> None:
        if self._runpod_market_service is not None:
            await self._runpod_market_service.aclose()
            self._runpod_market_service = None

    def _install_operations(self) -> None:
        source = self._operations_source or PostgresOperationsSource(
            pool_factory=self._resolve_pool
        )
        self._operations_source = source
        volume_files_source = ServiceVolumeFilesOperationsSource(self._create_volume_file_service)
        self.install_screen(OperationsScreen(source, volume_files_source), "operations")

    async def _resolve_overview_source(self) -> OverviewSource:
        if self._overview_source is None:
            self._overview_source = _DeferredPoolOverviewSource(self._resolve_pool)
        return self._overview_source

    async def _resolve_leases_source(self) -> LeasesSource:
        if self._leases_source is None:
            self._leases_source = _DeferredPoolLeasesSource(self._resolve_pool)
        return self._leases_source

    async def _resolve_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            pool_factory = self._pool_factory or default_pool
            self._pool = await pool_factory()
        return self._pool

    def _resolve_onboarding_source(self) -> OnboardingSource:
        if self._onboarding_source is None:
            self._onboarding_source = ServiceOnboardingSource(self._create_onboarding_service)
        return self._onboarding_source

    async def _create_onboarding_service(self) -> RunPodOnboardingService:
        return create_runpod_onboarding_service(await self._resolve_pool(), actor="system")

    async def _create_volume_file_service(self) -> VolumeFileService:
        return build_configured_volume_file_service(
            audit_pool=await self._resolve_pool(),
            audit_actor="system",
        )


async def default_pool() -> asyncpg.Pool:
    """Return the default Pitwall asyncpg pool."""

    from pitwall.db import get_pool

    return await get_pool()


__all__ = [
    "PitwallApp",
]
