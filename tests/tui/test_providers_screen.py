"""Hermetic tests for the Textual Providers screen."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import cast

import asyncpg
import pytest
from textual.widgets import Input, Static

import pitwall.tui.providers as providers
from pitwall.providers.registry import create_default_registry
from pitwall.providers.service import ProviderAvailabilityRead, ProviderHealthRead
from pitwall.tui import PitwallApp
from pitwall.tui.operations import StaticOperationsSource
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource
from pitwall.tui.providers import (
    ProviderEntry,
    ProvidersSnapshot,
    RegistryProvidersSource,
    ServiceProvidersSource,
    StaticProvidersSource,
)
from tests.hang_guard import HANG_GUARD_SECS
from tests.providers._provider_operations import (
    StubProviderOperationsService,
    availability,
    health,
)
from tests.tui.test_operations_screen import _operations_snapshot

pytestmark = pytest.mark.anyio


def test_descriptor_entry_reads_armed_state_from_config() -> None:
    from dataclasses import replace

    from pitwall.tui.providers import _provider_entry_from_descriptor
    from tests.providers._provider_operations import descriptor

    armed = replace(
        descriptor(),
        config={
            "cost": {"kind": "per_second", "price_per_hour": "1.250000"},
            "active_pod_id": "pod-serve-1",
            "active_lease_id": "lease-1",
        },
    )

    entry = _provider_entry_from_descriptor(armed)

    assert entry.armed is True
    assert entry.active_pod_id == "pod-serve-1"
    assert _provider_entry_from_descriptor(descriptor()).armed is False


def _overview_snapshot() -> OverviewSnapshot:
    import datetime as dt
    from decimal import Decimal

    return OverviewSnapshot(
        provider_total=3,
        provider_enabled=3,
        provider_health_counts={"healthy": 3},
        lease_state_counts={},
        active_leases=0,
        total_cost_usd=Decimal("0"),
        cost_entry_count=0,
        recent_workload_count=0,
        refreshed_at=dt.datetime(2026, 6, 2, 16, 30, tzinfo=dt.UTC),
    )


def _providers_snapshot() -> ProvidersSnapshot:
    return ProvidersSnapshot(
        entries=(
            ProviderEntry(
                provider_id="runpod",
                status="registered",
                pricing_model="tagged",
                armed=True,
                active_pod_id="pod-example-1",
            ),
            ProviderEntry(
                provider_id="vast",
                status="registered",
                pricing_model="tagged",
                armed=True,
                active_pod_id="pod-example-1",
            ),
            ProviderEntry(
                provider_id="together",
                status="registered",
                pricing_model="tagged",
                armed=True,
                active_pod_id="pod-example-1",
            ),
        )
    )


async def test_registry_provider_source_lists_default_registry_entries() -> None:
    source = RegistryProvidersSource(registry_factory=create_default_registry)

    snapshot = await source.load_providers()

    # Order-agnostic + not count-locked: the default registry grows as providers
    # land (runpod/vast/together/lambda_cloud …). Assert the known providers are
    # all present rather than pinning an exact list that breaks on every new one.
    listed = {entry.provider_id for entry in snapshot.entries}
    assert {"runpod", "vast", "together", "lambda_cloud"} <= listed
    assert all(entry.status == "registered" for entry in snapshot.entries)
    assert all(entry.pricing_model == "tagged" for entry in snapshot.entries)
    assert all(not entry.armed for entry in snapshot.entries)
    assert all(entry.active_pod_id is None for entry in snapshot.entries)
    assert {entry.adapter_id: entry.credential_ref for entry in snapshot.entries} == {
        "runpod": "RUNPOD_API_KEY",
        "vast": "VAST_API_KEY",
        "together": "TOGETHER_API_KEY",
        "lambda_cloud": "LAMBDA_CLOUD_API_KEY",
        "openai_gateway": "PITWALL_GATEWAY_API_KEY",
        "model_studio": "MODEL_STUDIO_API_KEY",
    }


async def test_pitwall_app_switches_to_providers_screen() -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(_providers_snapshot()),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()

        assert app.screen.name == "providers"
        assert str(app.screen.query_one("#providers-title", Static).content) == "Providers"


async def test_operations_hotkey_still_works_on_providers_screen() -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(_providers_snapshot()),
        # A static source keeps the operations screen off the ambient DATABASE_URL.
        operations_source=StaticOperationsSource(_operations_snapshot()),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert app.screen.name == "operations"


async def test_providers_refresh_failure_names_the_error_class_only() -> None:
    canary = "refresh-credential-canary"

    class BrokenSource(StaticProvidersSource):
        async def load_providers(self) -> ProvidersSnapshot:
            raise ConnectionError(canary)

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=BrokenSource(_providers_snapshot()),
    )
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        rendered = str(app.screen.query_one("#providers-error", Static).content)
        assert str(app.screen.query_one("#providers-table", Static).content) == "Unavailable"

    assert rendered == "Providers unavailable (ConnectionError)."
    assert canary not in rendered


async def test_providers_screen_renders_registered_provider_rows() -> None:
    source = StaticProvidersSource(_providers_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=source,
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("p")
        await pilot.pause()

        table = str(app.screen.query_one("#providers-table", Static).content)

        assert source.load_count == 1
        assert "Provider ID" in table
        assert "Adapter" in table
        assert "Credential ref" in table
        assert "Status" in table
        assert "Pricing model" in table
        assert "Credential set" in table
        assert "Capabilities" in table
        assert "Armed" in table
        assert "Active pod" in table
        assert "runpod" in table
        assert "vast" in table
        assert "together" in table
        assert "registered" in table
        assert "tagged" in table
        assert "yes" in table
        assert "pod-example-1" in table


async def test_providers_screen_pilot_probes_only_after_explicit_provider_id() -> None:
    probe = health()
    source = StaticProvidersSource(_providers_snapshot(), probe_results={"vast": probe})
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=source,
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("p")
        await pilot.pause()

        await pilot.press("g")
        await pilot.pause()
        assert source.probe_count == 0
        assert "Enter a provider ID" in str(
            app.screen.query_one("#providers-probe-result", Static).content
        )

        app.screen.query_one("#providers-probe-id", Input).value = "vast"
        await pilot.press("g")
        await pilot.pause()

        assert source.probe_count == 1
        result = str(app.screen.query_one("#providers-probe-result", Static).content)
        assert "prov-vast" in result
        assert "live: healthy" in result
        assert "availability sample: 1" in result


async def test_service_providers_source_uses_only_the_shared_service_methods() -> None:
    service = StubProviderOperationsService()

    async def pool_factory() -> asyncpg.Pool:
        return cast(asyncpg.Pool, object())

    source = ServiceProvidersSource(
        pool_factory=pool_factory, service_factory=lambda _pool: service
    )

    snapshot = await source.load_providers()
    probed = await source.probe_provider("prov-vast")
    available = await source.provider_availability("prov-vast")

    assert snapshot.entries == (
        ProviderEntry(
            provider_id="prov-vast",
            status="healthy",
            pricing_model="per_second",
            armed=False,
            active_pod_id=None,
            adapter_id="vast",
            credential_ref="VAST_API_KEY",
            credential_configured=True,
            capabilities=("availability", "compute"),
        ),
    )
    assert probed == health()
    assert available == availability()
    assert service.calls == [
        ("list", None, False, 100),
        ("health", "prov-vast", True, None),
        ("availability", "prov-vast", 25, None),
    ]


async def test_providers_screen_reads_actual_availability_and_pricing_only_on_action() -> None:
    source = StaticProvidersSource(
        _providers_snapshot(), availability_results={"vast": availability()}
    )
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=source,
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()

        await pilot.press("v")
        await pilot.pause()
        assert source.availability_count == 0
        assert "Enter a provider ID" in str(
            app.screen.query_one("#providers-probe-result", Static).content
        )

        app.screen.query_one("#providers-probe-id", Input).value = "vast"
        await pilot.press("v")
        await pilot.pause()

        assert source.availability_count == 1
        rendered = str(app.screen.query_one("#providers-probe-result", Static).content)
        assert "Availability/pricing populated." in rendered
        assert "source: vast-api-v0-bundles-2026-09-01" in rendered
        assert "offer-7" in rendered
        assert "usd_per_hour=1.250000" in rendered


async def test_providers_screen_shows_availability_loading_then_populated() -> None:
    class BlockingAvailabilitySource(StaticProvidersSource):
        def __init__(self) -> None:
            super().__init__(_providers_snapshot())
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
            del provider_id
            self.started.set()
            await self.release.wait()
            return availability()

    source = BlockingAvailabilitySource()
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=source,
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        app.screen.query_one("#providers-probe-id", Input).value = "vast"
        press = asyncio.create_task(pilot.press("v"))
        await asyncio.wait_for(source.started.wait(), timeout=HANG_GUARD_SECS)

        assert str(app.screen.query_one("#providers-probe-result", Static).content) == (
            "Loading provider availability/pricing."
        )

        source.release.set()
        await press
        await pilot.pause()
        assert "Availability/pricing populated." in str(
            app.screen.query_one("#providers-probe-result", Static).content
        )


async def test_providers_screen_renders_explicit_empty_availability() -> None:
    empty = replace(availability(), status="empty", items=())
    source = StaticProvidersSource(_providers_snapshot(), availability_results={"vast": empty})
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=source,
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        app.screen.query_one("#providers-probe-id", Input).value = "vast"
        await pilot.press("v")
        await pilot.pause()

        rendered = str(app.screen.query_one("#providers-probe-result", Static).content)
        assert "Availability/pricing empty." in rendered
        assert "No availability/pricing items." in rendered


async def test_providers_screen_retains_stale_availability_after_refresh_error() -> None:
    credential_canary = "availability-stale-credential-canary"

    class RefreshFailureSource(StaticProvidersSource):
        def __init__(self) -> None:
            super().__init__(_providers_snapshot())
            self.calls = 0

        async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
            del provider_id
            self.calls += 1
            if self.calls == 1:
                return availability()
            return replace(
                availability(),
                status="error",
                source_contract=None,
                items=(),
                error_code="provider_error",
            )

    source = RefreshFailureSource()
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=source,
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        app.screen.query_one("#providers-probe-id", Input).value = "vast"
        await pilot.press("v")
        await pilot.pause()
        await pilot.press("v")
        await pilot.pause()

        rendered = str(app.screen.query_one("#providers-probe-result", Static).content)
        assert "Availability/pricing stale; refresh unavailable." in rendered
        assert "offer-7" in rendered
        assert "usd_per_hour=1.250000" in rendered
        assert credential_canary not in rendered


async def test_providers_screen_first_availability_error_is_safe_unavailable() -> None:
    credential_canary = "availability-first-error-credential-canary"

    class BrokenAvailabilitySource(StaticProvidersSource):
        async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
            del provider_id
            return replace(
                availability(),
                status="unavailable",
                source_contract=None,
                items=(),
                error_code="credential_unavailable",
            )

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=BrokenAvailabilitySource(_providers_snapshot()),
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        app.screen.query_one("#providers-probe-id", Input).value = "vast"
        await pilot.press("v")
        await pilot.pause()

        rendered = str(app.screen.query_one("#providers-probe-result", Static).content)
        assert rendered == "Provider availability unavailable."
        assert credential_canary not in rendered


async def test_providers_screen_availability_exception_is_safe_unavailable() -> None:
    credential_canary = "availability-exception-credential-canary"

    class BrokenAvailabilitySource(StaticProvidersSource):
        async def provider_availability(self, provider_id: str) -> ProviderAvailabilityRead | None:
            del provider_id
            raise RuntimeError(credential_canary)

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=BrokenAvailabilitySource(_providers_snapshot()),
    )

    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        app.screen.query_one("#providers-probe-id", Input).value = "vast"
        await pilot.press("v")
        await pilot.pause()

        rendered = str(app.screen.query_one("#providers-probe-result", Static).content)
        assert rendered == "Provider availability unavailable."
        assert credential_canary not in rendered


async def test_providers_screen_redacts_probe_exceptions() -> None:
    credential_canary = "tui-provider-credential-canary"

    class BrokenProbeSource(StaticProvidersSource):
        async def probe_provider(self, provider_id: str) -> ProviderHealthRead | None:
            del provider_id
            raise RuntimeError(credential_canary)

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=BrokenProbeSource(_providers_snapshot()),
    )

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        app.screen.query_one("#providers-probe-id", Input).value = "vast"
        await pilot.press("g")
        await pilot.pause()

        result = str(app.screen.query_one("#providers-probe-result", Static).content)
        assert result == "Provider probe unavailable."
        assert credential_canary not in result


@pytest.mark.parametrize(
    ("count", "expected"),
    [(0, "0 registered providers"), (1, "1 registered provider"), (2, "2 registered providers")],
)
def test_providers_snapshot_summary_pluralizes(count: int, expected: str) -> None:
    snapshot = ProvidersSnapshot(
        entries=tuple(
            ProviderEntry(
                provider_id=f"provider-{index}",
                status="registered",
                pricing_model="tagged",
                armed=False,
                active_pod_id=None,
            )
            for index in range(count)
        )
    )

    assert snapshot.summary == expected


async def test_postgres_providers_source_reads_persisted_armed_facts() -> None:
    class FakeConnection:
        def __init__(self) -> None:
            self.query = ""
            self.limit: int | None = None

        async def fetch(self, query: str, limit: int) -> list[dict[str, object]]:
            self.query = query
            self.limit = limit
            return [
                {
                    "id": "provider-armed",
                    "name": "serve-armed",
                    "adapter_id": "runpod",
                    "credential_ref": "RUNPOD_API_KEY",
                    "health_status": "healthy",
                    "config": {
                        "active_pod_id": "pod-serve-1",
                        "active_lease_id": "lease-serve-1",
                        "cost": {"mode": "per_second"},
                    },
                },
                {
                    "id": "provider-idle",
                    "name": "serve-idle",
                    "adapter_id": "vast",
                    "credential_ref": "VAST_API_KEY",
                    "health_status": "unknown",
                    "config": {"cost": {}},
                },
            ]

    class FakeAcquire:
        def __init__(self, connection: FakeConnection) -> None:
            self.connection = connection

        async def __aenter__(self) -> FakeConnection:
            return self.connection

        async def __aexit__(self, *args: object) -> None:
            return None

    class FakePool:
        def __init__(self, connection: FakeConnection) -> None:
            self.connection = connection

        def acquire(self) -> FakeAcquire:
            return FakeAcquire(self.connection)

    connection = FakeConnection()
    pool = FakePool(connection)

    async def pool_factory() -> asyncpg.Pool:
        return cast(asyncpg.Pool, pool)

    source_type = getattr(providers, "PostgresProvidersSource", None)
    assert source_type is not None
    snapshot = await source_type(pool_factory=pool_factory).load_providers()

    assert "config" in connection.query
    assert connection.limit == 200
    assert snapshot.entries == (
        ProviderEntry(
            provider_id="provider-armed",
            status="healthy",
            pricing_model="per_second",
            armed=True,
            active_pod_id="pod-serve-1",
            adapter_id="runpod",
            credential_ref="RUNPOD_API_KEY",
        ),
        ProviderEntry(
            provider_id="provider-idle",
            status="unknown",
            pricing_model="tagged",
            armed=False,
            active_pod_id=None,
            adapter_id="vast",
            credential_ref="VAST_API_KEY",
        ),
    )


async def test_providers_empty_snapshot_and_keyboard_refresh_render_without_error() -> None:
    source = StaticProvidersSource(ProvidersSnapshot(entries=()))
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()), providers_source=source
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        assert (
            str(app.screen.query_one("#providers-summary", Static).content)
            == "0 registered providers"
        )
        assert "Loading" not in str(app.screen.query_one("#providers-table", Static).content)
        assert str(app.screen.query_one("#providers-error", Static).content) == ""
        await pilot.press("r")
        await pilot.pause()
        assert source.load_count == 2
        assert source.probe_count == source.availability_count == 0


async def test_provider_filter_is_case_insensitive_and_clear_restores_rows() -> None:
    source = StaticProvidersSource(_providers_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()), providers_source=source
    )
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        screen = cast(providers.ProvidersScreen, app.screen)
        screen.apply_filter(" VAST ")
        table = str(screen.query_one("#providers-table", Static).content)
        assert "vast" in table
        assert [line.split()[0] for line in table.splitlines()[2:]] == ["vast"]
        screen.apply_filter("no-matching-provider")
        table = str(screen.query_one("#providers-table", Static).content)
        assert "vast" not in table
        screen.apply_filter("")
        table = str(screen.query_one("#providers-table", Static).content)
        assert all(name in table for name in ("runpod", "vast", "together"))
        assert source.load_count == 1
        assert source.probe_count == source.availability_count == 0


async def test_provider_rows_survive_terminal_resize() -> None:
    source = StaticProvidersSource(_providers_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()), providers_source=source
    )
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.press("p")
        await pilot.pause()
        original = str(app.screen.query_one("#providers-table", Static).content)
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        assert str(app.screen.query_one("#providers-table", Static).content) == original
        await pilot.resize_terminal(140, 45)
        await pilot.pause()
        assert str(app.screen.query_one("#providers-table", Static).content) == original
        assert source.load_count == 1
