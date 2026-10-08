"""Hermetic tests for the Textual resources screen."""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from decimal import Decimal
from typing import cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from textual.widgets import Button, Input, Select, Static

from pitwall.runpod_control_plane import (
    EndpointResource,
    EndpointScalingRequest,
    EndpointWorkersRequest,
    HubTemplateResource,
    MutationResult,
    PodResource,
    RegistryAuthResource,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    TemplateResource,
    VolumeResource,
)
from pitwall.runpod_market import RunpodMarketService
from pitwall.tui import PitwallApp
from pitwall.tui import resources as resources_module
from pitwall.tui.overview import OverviewSnapshot, StaticOverviewSource
from pitwall.tui.providers import ProvidersSnapshot, StaticProvidersSource
from pitwall.tui.resources import (
    RegistryAuthEntry,
    ResourceEndpointEntry,
    ResourceHubTemplateEntry,
    ResourceMutationDraft,
    ResourcePodEntry,
    ResourceQueryOperation,
    ResourcesSnapshot,
    ResourceTemplateEntry,
    ResourceVolumeEntry,
    RunPodResourcesSource,
    StaticResourcesSource,
    display_text,
    format_endpoint_table,
    format_hub_template_table,
    format_pod_table,
    format_registry_table,
    format_template_table,
    format_volume_table,
)
from pitwall.tui.runpod_market import StaticRunpodMarketSource

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
async def test_resource_action_buttons_are_not_clipped(size: tuple[int, int]) -> None:
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        resources_source=StaticResourcesSource(_resources_snapshot()),
    )
    async with app.run_test(size=size) as pilot:
        await pilot.press("e")
        await pilot.pause()
        for selector in ("#resources-query", "#resources-mutate"):
            button = app.screen.query_one(selector, Button)
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


def _resources_snapshot() -> ResourcesSnapshot:
    return ResourcesSnapshot(
        pods=(
            ResourcePodEntry(
                pod_id="pod_alpha",
                name="alpha-pod",
                status="RUNNING",
                gpu_type_id="NVIDIA L4",
            ),
        ),
        endpoints=(
            ResourceEndpointEntry(
                endpoint_id="ep_alpha",
                name="alpha-endpoint",
                workers_min=1,
                workers_max=3,
                template_id="tmpl_alpha",
                scaling="QUEUE_DELAY:4",
                gpu_pools=("AMPERE_24",),
            ),
            ResourceEndpointEntry(
                endpoint_id="ep_beta",
                name="beta-endpoint",
                workers_min=0,
                workers_max=1,
                template_id=None,
            ),
        ),
        templates=(
            ResourceTemplateEntry(
                template_id="tmpl_alpha",
                name="Alpha Template",
                image_name="ghcr.io/example/worker:abc123",
                is_serverless=True,
            ),
        ),
        hub_templates=(
            ResourceHubTemplateEntry(
                template_id="hub_alpha",
                name="Public Alpha",
                image_name="runpod/worker:stable",
                is_serverless=True,
            ),
        ),
        volumes=(
            ResourceVolumeEntry(
                volume_id="vol_alpha",
                name="weights-alpha",
                size_gb=80,
                data_center_id="US-KS-1",
            ),
        ),
        registry_auths=(
            RegistryAuthEntry(
                auth_id="auth_alpha",
                name="ghcr-pitwall",
            ),
        ),
        refreshed_at=_NOW,
    )


@pytest.mark.property
@given(st.text(max_size=32))
def test_display_text_returns_display_safe_non_empty_label(value: str) -> None:
    label = display_text(value)

    assert label
    assert label == label.strip()


def test_resources_snapshot_summary_is_stable() -> None:
    snapshot = _resources_snapshot()

    assert snapshot.summary == (
        "Resources: 1 pod | 2 endpoints | 1 account template | 1 Hub template | "
        "1 volume | 1 registry auth"
    )
    assert snapshot.refreshed_label == "2026-06-02 16:30 UTC"


def test_display_text_removes_control_characters() -> None:
    assert display_text("provider\n\x1bresource") == "provider resource"


def test_resources_screen_exposes_every_shared_service_operation() -> None:
    mutations = {value for _, value in resources_module._MUTATION_OPTIONS}
    queries = {value for _, value in resources_module._QUERY_OPTIONS}

    assert mutations == {
        "pod.create",
        "pod.update",
        "pod.action",
        "pod.terminate",
        "endpoint.create",
        "endpoint.update",
        "endpoint.delete",
        "template.create",
        "template.update",
        "template.delete",
        "volume.create",
        "volume.grow",
        "volume.delete",
        "registry_auth.create",
        "registry_auth.replace",
        "registry_auth.delete",
    }
    assert queries == {
        "pod.get",
        "endpoint.get",
        "template.get",
        "volume.get",
        "registry_auth.get",
        "hub_template.get",
        "hub_template.search",
    }


def test_resource_tables_render_all_resource_sections() -> None:
    snapshot = _resources_snapshot()

    pod_table = format_pod_table(snapshot.pods)
    endpoint_table = format_endpoint_table(snapshot.endpoints)
    template_table = format_template_table(snapshot.templates)
    hub_table = format_hub_template_table(snapshot.hub_templates)
    volume_table = format_volume_table(snapshot.volumes)
    registry_table = format_registry_table(snapshot.registry_auths)

    assert "Pod ID" in pod_table
    assert "pod_alpha" in pod_table
    assert "Endpoint ID" in endpoint_table
    assert "ep_alpha" in endpoint_table
    assert "1-3" in endpoint_table
    assert "QUEUE_DELAY:4" in endpoint_table
    assert "AMPERE_24" in endpoint_table
    assert "none" in endpoint_table
    assert "Template ID" in template_table
    assert "Alpha Template" in template_table
    assert "yes" in template_table
    assert "Hub ID" in hub_table
    assert "Public Alpha" in hub_table
    assert "Volume ID" in volume_table
    assert "80 GB" in volume_table
    assert "US-KS-1" in volume_table
    assert "Auth ID" in registry_table
    assert "ghcr-pitwall" in registry_table


class FakeControlPlaneService:
    def __init__(self) -> None:
        self.fail_endpoints = False

    async def list_pods(self) -> list[PodResource]:
        return [PodResource(id="pod_live", name="live-pod", status="RUNNING")]

    async def list_endpoints(self) -> list[EndpointResource]:
        if self.fail_endpoints:
            raise RuntimeError("provider timeout with token-not-shown")
        return [
            EndpointResource(
                id="ep_live",
                name="live-endpoint",
                workers=EndpointWorkersRequest(minimum=2, maximum=5),
                scaling=EndpointScalingRequest(type="REQUEST_COUNT", value=2),
                flashboot=True,
                template_id="tmpl_live",
                gpu_pools=("ADA_24",),
            )
        ]

    async def list_templates(self) -> list[TemplateResource]:
        return [
            TemplateResource(
                id="tmpl_live",
                name="Worker Live",
                image="ghcr.io/example/worker:live",
                disk_gb=20,
                volume_gb=0,
                serverless=True,
                public=False,
            )
        ]

    async def list_hub_templates(self, *, limit: int, offset: int) -> list[HubTemplateResource]:
        assert (limit, offset) == (50, 0)
        return [
            HubTemplateResource(
                id="hub_live",
                name="public-worker",
                display_name="Public Worker",
                image="runpod/worker:stable",
                serverless=True,
            )
        ]

    async def list_volumes(self) -> list[VolumeResource]:
        return [
            VolumeResource(
                id="vol_live",
                name="weights-live",
                size_gb=120,
                data_center_id="EU-RO-1",
            )
        ]

    async def list_registry_auths(self) -> list[RegistryAuthResource]:
        return [RegistryAuthResource(id="auth_live", name="ghcr-live")]


async def test_runpod_resources_source_maps_only_shared_service_results() -> None:
    service = FakeControlPlaneService()
    clock = [_NOW]

    def now() -> dt.datetime:
        clock[0] = clock[0] + dt.timedelta(minutes=5)
        return clock[0]

    source = RunPodResourcesSource(
        service=cast(RunPodControlPlaneService, service),
        now=now,
    )

    snapshot = await source.load_resources()

    assert snapshot.pods[0].as_row() == ("pod_live", "live-pod", "RUNNING", "none")
    assert snapshot.endpoints[0].as_row() == (
        "ep_live",
        "live-endpoint",
        "2-5",
        "REQUEST_COUNT:2",
        "ADA_24",
        "tmpl_live",
    )
    assert snapshot.templates[0].as_row() == (
        "tmpl_live",
        "Worker Live",
        "ghcr.io/example/worker:live",
        "yes",
    )
    assert snapshot.volumes[0].as_row() == ("vol_live", "weights-live", "120 GB", "EU-RO-1")
    assert snapshot.registry_auths[0].as_row() == ("auth_live", "ghcr-live")
    assert snapshot.hub_templates[0].as_row()[0] == "hub_live"
    assert snapshot.refreshed_at == _NOW + dt.timedelta(minutes=5)

    service.fail_endpoints = True
    stale = await source.load_resources()
    assert stale.endpoints == snapshot.endpoints
    assert stale.stale_sections == ("endpoints",)
    assert stale.unavailable_sections == ("endpoints",)
    assert stale.unavailable_errors == ("RuntimeError",)
    assert "token-not-shown" not in " ".join(stale.unavailable_errors)
    assert stale.stale_since == snapshot.refreshed_at
    again = await source.load_resources()
    assert again.stale_since == snapshot.refreshed_at  # carried forward, not reset


async def test_resources_error_line_names_failed_section_and_class() -> None:
    degraded = replace(
        _resources_snapshot(),
        unavailable_sections=("endpoints",),
        unavailable_errors=("RuntimeError",),
    )
    market_service = RunpodMarketService(None, None, None)
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=StaticResourcesSource(degraded),
        runpod_market_source=StaticRunpodMarketSource(await market_service.read()),
    )
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        rendered = str(app.screen.query_one("#resources-error", Static).content)

    assert rendered == "Provider error · unavailable: endpoints (RuntimeError)"


async def test_pitwall_app_switches_to_resources_screen() -> None:
    market_service = RunpodMarketService(None, None, None)
    market_source = StaticRunpodMarketSource(await market_service.read())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=StaticResourcesSource(_resources_snapshot()),
        runpod_market_source=market_source,
    )

    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.pause()
        await pilot.press("e")
        await pilot.pause()

        assert app.screen.name == "resources"
        assert str(app.screen.query_one("#resources-title", Static).content) == "Resources"
        assert str(app.screen.query_one("#runpod-market-state", Static).content) == "unavailable"

        await pilot.press("r")
        await pilot.pause()
        assert market_source.calls == [False, True]


async def test_resources_screen_renders_snapshot() -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("e")
        await pilot.pause()

        assert source.load_count == 1
        assert "2 endpoints" in str(app.screen.query_one("#resources-summary", Static).content)
        assert "pod_alpha" in str(app.screen.query_one("#pods-table", Static).content)
        assert "ep_alpha" in str(app.screen.query_one("#endpoints-table", Static).content)
        assert "Alpha Template" in str(app.screen.query_one("#templates-table", Static).content)
        assert "Public Alpha" in str(app.screen.query_one("#hub-templates-table", Static).content)
        assert "weights-alpha" in str(app.screen.query_one("#volumes-table", Static).content)
        assert "ghcr-pitwall" in str(app.screen.query_one("#registry-table", Static).content)
        assert "Last refreshed: 2026-06-02 16:30 UTC" in str(
            app.screen.query_one("#resources-refreshed", Static).content
        )


async def test_resources_screen_refresh_reloads_source() -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()

        assert source.load_count == 2


async def test_resources_screen_filters_every_resource_section() -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        app.screen.apply_filter("alpha")

        assert "ep_alpha" in str(app.screen.query_one("#endpoints-table", Static).content)
        assert "ep_beta" not in str(app.screen.query_one("#endpoints-table", Static).content)
        assert "Public Alpha" in str(app.screen.query_one("#hub-templates-table", Static).content)


@pytest.mark.parametrize("query", ["alpha", "ALPHA", "ep_", "80 GB", "ghcr-pitwall"])
async def test_resources_filter_matches_within_every_section_row(query: str) -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        app.screen.apply_filter(query)
        await pilot.pause()

        snapshot = _resources_snapshot()
        expected = {
            "pods-table": tuple(
                item
                for item in snapshot.pods
                if query.casefold() in " ".join(item.as_row()).casefold()
            ),
            "endpoints-table": tuple(
                item
                for item in snapshot.endpoints
                if query.casefold() in " ".join(item.as_row()).casefold()
            ),
            "templates-table": tuple(
                item
                for item in snapshot.templates
                if query.casefold() in " ".join(item.as_row()).casefold()
            ),
            "hub-templates-table": tuple(
                item
                for item in snapshot.hub_templates
                if query.casefold() in " ".join(item.as_row()).casefold()
            ),
            "volumes-table": tuple(
                item
                for item in snapshot.volumes
                if query.casefold() in " ".join(item.as_row()).casefold()
            ),
            "registry-table": tuple(
                item
                for item in snapshot.registry_auths
                if query.casefold() in " ".join(item.as_row()).casefold()
            ),
        }
        assert sum(len(rows) for rows in expected.values()) > 0, query
        expected_rendered = {
            "pods-table": format_pod_table(expected["pods-table"]),
            "endpoints-table": format_endpoint_table(expected["endpoints-table"]),
            "templates-table": format_template_table(expected["templates-table"]),
            "hub-templates-table": format_hub_template_table(expected["hub-templates-table"]),
            "volumes-table": format_volume_table(expected["volumes-table"]),
            "registry-table": format_registry_table(expected["registry-table"]),
        }
        for widget_id, rendered in expected_rendered.items():
            assert str(app.screen.query_one(f"#{widget_id}", Static).content) == rendered, widget_id


class RecordingQuerySource(StaticResourcesSource):
    async def query_resource(
        self, operation: ResourceQueryOperation, value: str
    ) -> dict[str, object]:
        self.last_query = (operation, value)
        return await super().query_resource(operation, value)


async def test_resources_query_dialog_lookup_roundtrip_renders_result() -> None:
    source = RecordingQuerySource(
        _resources_snapshot(),
        query_result={"id": "pod_alpha", "name": "alpha-pod", "status": "RUNNING"},
    )
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#resources-query")
        await pilot.pause()
        app.screen.query_one("#resource-query-operation", Select).value = "pod.get"
        app.screen.query_one("#resource-query-value", Input).value = "pod_alpha"
        await pilot.click("#resource-query-run")
        await pilot.pause()

        assert source.calls == [("query", "pod.get")]
        assert source.last_query == ("pod.get", "pod_alpha")
        rendered = str(app.screen.query_one("#resources-operation-result", Static).content)
        assert '"id": "pod_alpha"' in rendered
        assert '"status": "RUNNING"' in rendered
        assert str(app.screen.query_one("#resources-error", Static).content) == ""


async def test_resources_query_dialog_hub_search_roundtrip_renders_matches() -> None:
    source = RecordingQuerySource(
        _resources_snapshot(),
        query_result={
            "hub_templates": [
                {"id": "hub_alpha", "display_name": "Public Alpha"},
                {"id": "hub_beta", "display_name": "Public Beta"},
            ]
        },
    )
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#resources-query")
        await pilot.pause()
        app.screen.query_one("#resource-query-operation", Select).value = "hub_template.search"
        app.screen.query_one("#resource-query-value", Input).value = "public"
        await pilot.click("#resource-query-run")
        await pilot.pause()

        assert source.calls == [("query", "hub_template.search")]
        assert source.last_query == ("hub_template.search", "public")
        rendered = str(app.screen.query_one("#resources-operation-result", Static).content)
        assert "hub_alpha" in rendered
        assert "hub_beta" in rendered


async def test_resources_failed_query_clears_prior_result() -> None:
    class SecondQueryFails(StaticResourcesSource):
        def __init__(self) -> None:
            super().__init__(_resources_snapshot())
            self.query_count = 0

        async def query_resource(
            self, operation: ResourceQueryOperation, value: str
        ) -> dict[str, object]:
            self.calls.append(("query", operation))
            self.query_count += 1
            if self.query_count == 2:
                raise RuntimeError("second lookup failed")
            return {"id": value, "name": "first-ok"}

    source = SecondQueryFails()
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        for value in ("pod_one", "pod_two"):
            await pilot.click("#resources-query")
            await pilot.pause()
            app.screen.query_one("#resource-query-value", Input).value = value
            await pilot.click("#resource-query-run")
            await pilot.pause()

        assert source.calls == [("query", "pod.get"), ("query", "pod.get")]
        assert "second lookup failed" in str(
            app.screen.query_one("#resources-error", Static).content
        )
        assert str(app.screen.query_one("#resources-operation-result", Static).content) == ""


async def test_resources_query_error_is_redacted_and_retry_succeeds() -> None:
    class RedactingQuerySource(StaticResourcesSource):
        def __init__(self) -> None:
            super().__init__(_resources_snapshot())
            self.fail_next_query = True

        async def query_resource(
            self, operation: ResourceQueryOperation, value: str
        ) -> dict[str, object]:
            self.calls.append(("query", operation))
            if self.fail_next_query:
                self.fail_next_query = False
                raise RuntimeError(
                    "GET https://user:hunter2@runpod.example/v1/pods failed: "
                    "api_key=rpa_live_shouldnotleak"
                )
            return {"id": value, "name": "retried-ok"}

    source = RedactingQuerySource()
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#resources-query")
        await pilot.pause()
        app.screen.query_one("#resource-query-operation", Select).value = "pod.get"
        app.screen.query_one("#resource-query-value", Input).value = "pod_alpha"
        await pilot.click("#resource-query-run")
        await pilot.pause()

        message = str(app.screen.query_one("#resources-error", Static).content)
        assert message.startswith("Resource read unavailable: ")
        assert "hunter2" not in message
        assert "rpa_live_shouldnotleak" not in message
        assert "[REDACTED]" in message
        assert str(app.screen.query_one("#resources-operation-result", Static).content) == ""

        await pilot.click("#resources-query")
        await pilot.pause()
        app.screen.query_one("#resource-query-operation", Select).value = "pod.get"
        app.screen.query_one("#resource-query-value", Input).value = "pod_beta"
        await pilot.click("#resource-query-run")
        await pilot.pause()

        assert source.calls == [("query", "pod.get"), ("query", "pod.get")]
        assert str(app.screen.query_one("#resources-error", Static).content) == ""
        assert "retried-ok" in str(
            app.screen.query_one("#resources-operation-result", Static).content
        )


async def test_resources_query_cancel_button_and_escape_make_zero_source_calls() -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()

        await pilot.click("#resources-query")
        await pilot.pause()
        app.screen.query_one("#resource-query-value", Input).value = "pod_alpha"
        await pilot.click("#resource-query-cancel")
        await pilot.pause()
        assert app.screen.name == "resources"

        await pilot.click("#resources-query")
        await pilot.pause()
        app.screen.query_one("#resource-query-value", Input).value = "pod_alpha"
        await pilot.press("escape")
        await pilot.pause()
        assert app.screen.name == "resources"

        assert source.calls == []
        assert source.load_count == 1
        assert str(app.screen.query_one("#resources-operation-result", Static).content) == ""
        assert str(app.screen.query_one("#resources-error", Static).content) == ""


async def _open_pod_create_preview(app: PitwallApp, pilot) -> None:
    await pilot.click("#resources-mutate")
    await pilot.pause()
    editor = app.screen
    editor.query_one(
        "#resource-mutation-json", Input
    ).value = '{"name":"new-pod","image":"example/image:1","gpu_type_ids":["NVIDIA L4"]}'
    await pilot.click("#resource-mutation-preview")
    await pilot.pause()


async def test_resources_wrong_type_confirmation_performs_zero_writes() -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await _open_pod_create_preview(app, pilot)
        assert source.calls == [("preview", "pod.create")]

        app.screen.query_one("#resource-confirm-text", Input).value = "wrong"
        await pilot.pause()
        assert app.screen.query_one("#resource-confirm-apply", Button).disabled
        await pilot.click("#resource-confirm-apply")
        await pilot.pause()
        assert source.calls == [("preview", "pod.create")]


async def test_resources_mutation_editor_cancel_performs_zero_writes() -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#resources-mutate")
        await pilot.pause()
        await pilot.click("#resource-mutation-cancel")
        await pilot.pause()

        assert source.calls == []
        assert source.load_count == 1


@pytest.mark.parametrize("dismiss", ["button", "escape"])
async def test_resources_confirmation_cancel_makes_zero_writes(dismiss: str) -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await _open_pod_create_preview(app, pilot)
        assert source.calls == [("preview", "pod.create")]

        if dismiss == "button":
            await pilot.click("#resource-confirm-cancel")
        else:
            await pilot.press("escape")
        await pilot.pause()

        assert app.screen.name == "resources"
        assert source.calls == [("preview", "pod.create")]
        assert source.load_count == 1
        assert str(app.screen.query_one("#resources-operation-result", Static).content) == ""
        assert str(app.screen.query_one("#resources-error", Static).content) == ""


async def test_resources_failed_preview_clears_prior_result() -> None:
    class SecondPreviewFails(StaticResourcesSource):
        def __init__(self) -> None:
            super().__init__(_resources_snapshot())
            self.preview_count = 0

        async def preview_mutation(self, draft: ResourceMutationDraft) -> MutationResult:
            self.calls.append(("preview", draft.operation))
            self.preview_count += 1
            if self.preview_count == 2:
                raise RuntimeError("second preview failed")
            return await super().preview_mutation(draft)

    source = SecondPreviewFails()
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await _open_pod_create_preview(app, pilot)
        app.screen.query_one("#resource-confirm-text", Input).value = "new-pod"
        await pilot.pause()
        await pilot.click("#resource-confirm-apply")
        await pilot.pause()
        assert '"changed": true' in str(
            app.screen.query_one("#resources-operation-result", Static).content
        )

        await pilot.click("#resources-mutate")
        await pilot.pause()
        app.screen.query_one(
            "#resource-mutation-json", Input
        ).value = '{"name":"second-pod","image":"example/image:1","gpu_type_ids":["NVIDIA L4"]}'
        await pilot.click("#resource-mutation-preview")
        await pilot.pause()

        assert "second preview failed" in str(
            app.screen.query_one("#resources-error", Static).content
        )
        assert str(app.screen.query_one("#resources-operation-result", Static).content) == ""


async def test_resources_failed_apply_clears_prior_result() -> None:
    class SecondApplyFails(StaticResourcesSource):
        def __init__(self) -> None:
            super().__init__(_resources_snapshot())
            self.apply_count = 0

        async def apply_mutation(self, draft: ResourceMutationDraft) -> MutationResult:
            self.calls.append(("apply", draft.operation))
            self.apply_count += 1
            if self.apply_count == 2:
                raise RuntimeError("second apply failed")
            return await super().apply_mutation(draft)

    source = SecondApplyFails()
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        for name in ("pod_one", "pod_two"):
            await pilot.click("#resources-mutate")
            await pilot.pause()
            app.screen.query_one(
                "#resource-mutation-json", Input
            ).value = f'{{"name":"{name}","image":"example/image:1","gpu_type_ids":["NVIDIA L4"]}}'
            await pilot.click("#resource-mutation-preview")
            await pilot.pause()
            app.screen.query_one("#resource-confirm-text", Input).value = name
            await pilot.pause()
            await pilot.click("#resource-confirm-apply")
            await pilot.pause()

        assert "second apply failed" in str(
            app.screen.query_one("#resources-error", Static).content
        )
        assert str(app.screen.query_one("#resources-operation-result", Static).content) == ""


async def test_resources_exact_confirmation_applies_then_refreshes() -> None:
    source = StaticResourcesSource(_resources_snapshot())
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await _open_pod_create_preview(app, pilot)

        app.screen.query_one("#resource-confirm-text", Input).value = "new-pod"
        await pilot.pause()
        assert not app.screen.query_one("#resource-confirm-apply", Button).disabled
        await pilot.click("#resource-confirm-apply")
        await pilot.pause()

        assert source.calls == [
            ("preview", "pod.create"),
            ("apply", "pod.create"),
        ]
        assert source.load_count == 2
        assert '"changed": true' in str(
            app.screen.query_one("#resources-operation-result", Static).content
        )


async def test_resources_already_gone_result_is_explicit_and_refreshes() -> None:
    source = StaticResourcesSource(
        _resources_snapshot(),
        apply_result=MutationResult(
            operation="pod.terminate",
            resource_type="pod",
            resource_id="pod_alpha",
            dry_run=False,
            changed=False,
            already_absent=True,
            effect="resource was already absent",
            idempotency_key="tui-scripted-already-gone",
        ),
    )
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#resources-mutate")
        await pilot.pause()
        app.screen.query_one("#resource-mutation-operation", Select).value = "pod.terminate"
        app.screen.query_one("#resource-mutation-json", Input).value = '{"resource_id":"pod_alpha"}'
        await pilot.click("#resource-mutation-preview")
        await pilot.pause()
        app.screen.query_one("#resource-confirm-text", Input).value = "pod_alpha"
        await pilot.pause()
        await pilot.click("#resource-confirm-apply")
        await pilot.pause()

        assert source.load_count == 2
        assert '"already_absent": true' in str(
            app.screen.query_one("#resources-operation-result", Static).content
        )


async def test_resources_partial_failure_refreshes_changed_provider_state() -> None:
    source = StaticResourcesSource(
        _resources_snapshot(),
        apply_error=RunPodControlPlaneError(
            "registry_replace_partial_failure",
            "old registry auth was deleted but replacement creation failed",
            operation="registry_auth.replace",
            resource_type="registry_auth",
            resource_id="auth_alpha",
            changed=True,
        ),
    )
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await pilot.click("#resources-mutate")
        await pilot.pause()
        app.screen.query_one("#resource-mutation-operation", Select).value = "registry_auth.replace"
        app.screen.query_one("#resource-mutation-json", Input).value = (
            '{"resource_id":"auth_alpha","name":"replacement",'
            '"username":"robot","password_env":"REGISTRY_PASSWORD"}'
        )
        await pilot.click("#resource-mutation-preview")
        await pilot.pause()
        app.screen.query_one("#resource-confirm-text", Input).value = "auth_alpha"
        await pilot.pause()
        await pilot.click("#resource-confirm-apply")
        await pilot.pause()

        assert source.load_count == 2
        assert source.calls == [
            ("preview", "registry_auth.replace"),
            ("apply", "registry_auth.replace"),
        ]
        assert "replacement creation failed" in str(
            app.screen.query_one("#resources-error", Static).content
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            RunPodControlPlaneError(
                "provider_error",
                "RunPod provider rejected the request",
                operation="pod.create",
                resource_type="pod",
                provider_status=403,
            ),
            "provider rejected",
        ),
        (
            RunPodControlPlaneError(
                "provider_timeout",
                "RunPod provider request timed out",
                operation="pod.create",
                resource_type="pod",
                retryable=True,
            ),
            "timed out",
        ),
    ],
)
async def test_resources_preview_provider_failures_stay_inline(
    error: Exception,
    expected: str,
) -> None:
    source = StaticResourcesSource(_resources_snapshot(), preview_error=error)
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=source,
    )

    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        await _open_pod_create_preview(app, pilot)

        message = str(app.screen.query_one("#resources-error", Static).content)
        assert "Mutation preview failed" in message
        assert expected in message
        assert source.calls == [("preview", "pod.create")]


async def test_resources_empty_state_is_explicit() -> None:
    empty = ResourcesSnapshot(
        endpoints=(),
        templates=(),
        volumes=(),
        registry_auths=(),
        refreshed_at=_NOW,
    )
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=StaticResourcesSource(empty),
    )

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()

        assert "empty · no pods" in str(app.screen.query_one("#pods-table", Static).content)
        assert "empty · no endpoints" in str(
            app.screen.query_one("#endpoints-table", Static).content
        )
        assert "empty · no Hub templates" in str(
            app.screen.query_one("#hub-templates-table", Static).content
        )


async def test_resources_stale_and_unavailable_sections_are_explicit() -> None:
    snapshot = _resources_snapshot()
    snapshot = ResourcesSnapshot(
        endpoints=snapshot.endpoints,
        templates=snapshot.templates,
        volumes=snapshot.volumes,
        registry_auths=snapshot.registry_auths,
        refreshed_at=snapshot.refreshed_at,
        pods=snapshot.pods,
        hub_templates=snapshot.hub_templates,
        stale_sections=("endpoints",),
        unavailable_sections=("endpoints", "volumes"),
    )
    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=StaticResourcesSource(snapshot),
    )

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()

        assert "stale: endpoints" in str(app.screen.query_one("#resources-summary", Static).content)
        assert "unavailable: endpoints, volumes" in str(
            app.screen.query_one("#resources-summary", Static).content
        )
        assert str(app.screen.query_one("#resources-error", Static).content) == (
            "Provider error · unavailable: endpoints (unknown), volumes (unknown)"
        )


async def test_failed_refresh_keeps_the_previous_snapshot() -> None:
    snapshot = _resources_snapshot()

    class FlakyResourcesSource:
        def __init__(self) -> None:
            self.calls = 0

        async def load_resources(self) -> ResourcesSnapshot:
            self.calls += 1
            if self.calls > 1:
                raise ValueError("provider went away")
            return snapshot

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=FlakyResourcesSource(),
    )
    widget_ids = (
        "resources-summary",
        "pods-table",
        "endpoints-table",
        "templates-table",
        "hub-templates-table",
        "volumes-table",
        "registry-table",
    )

    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("e")
        await pilot.pause()
        first = {w: str(app.screen.query_one(f"#{w}", Static).content) for w in widget_ids}
        assert first["resources-summary"] != "Unavailable"
        assert "alpha-pod" in first["pods-table"]

        await app.screen.run_action("refresh")
        await pilot.pause()

        for widget_id in widget_ids:
            assert str(app.screen.query_one(f"#{widget_id}", Static).content) == first[widget_id]
        assert str(app.screen.query_one("#resources-error", Static).content) == (
            "Resources unavailable: provider went away"
        )


async def test_resources_screen_reports_source_failure() -> None:
    class FailingResourcesSource:
        async def load_resources(self) -> ResourcesSnapshot:
            raise ValueError("malformed provider response")

    app = PitwallApp(
        overview_source=StaticOverviewSource(_overview_snapshot()),
        providers_source=StaticProvidersSource(ProvidersSnapshot(entries=())),
        resources_source=FailingResourcesSource(),
    )

    async with app.run_test(size=(120, 32)) as pilot:
        await pilot.press("e")
        await pilot.pause()

        assert str(app.screen.query_one("#resources-error", Static).content) == (
            "Resources unavailable: malformed provider response"
        )
        for widget_id in (
            "resources-summary",
            "pods-table",
            "endpoints-table",
            "templates-table",
            "hub-templates-table",
            "volumes-table",
            "registry-table",
        ):
            assert str(app.screen.query_one(f"#{widget_id}", Static).content) == "Unavailable"
