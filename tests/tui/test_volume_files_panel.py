"""Pilot coverage for the feature-local Operations volume-file panel."""

from __future__ import annotations

import asyncio
import inspect
from typing import cast

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Checkbox, Input, Static

from pitwall.runpod_client.pod_logs import BoundedPodLogPayload
from pitwall.runpod_files import (
    PodLogLine,
    ProgressCallback,
    VolumeFileProgress,
    VolumeFileResult,
    VolumeFileService,
    VolumeObject,
    VolumeObjectPage,
    VolumeObjectStore,
)
from pitwall.tui import volume_files
from pitwall.tui.volume_files import (
    ServiceVolumeFilesOperationsSource,
    StaticVolumeFilesOperationsSource,
    VolumeFilesOperationsPanel,
)
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.anyio


class PanelApp(App[None]):
    def __init__(self, panel: VolumeFilesOperationsPanel) -> None:
        super().__init__()
        self.panel = panel

    def compose(self) -> ComposeResult:
        yield self.panel


def _listing() -> VolumeFileResult:
    return VolumeFileResult(
        operation="list",
        status="completed",
        objects=(VolumeObject(key="models/one.bin", size=3),),
    )


def _logs() -> VolumeFileResult:
    return VolumeFileResult(
        operation="logs",
        status="completed",
        logs=(PodLogLine(sequence=1, timestamp="2026-09-01T00:00:00Z", text="ready"),),
    )


def _set_common_inputs(panel: VolumeFilesOperationsPanel) -> None:
    panel.query_one("#volume-files-volume-id", Input).value = "vol-1"
    panel.query_one("#volume-files-data-center", Input).value = "US-KS-2"
    panel.query_one("#volume-files-object-key", Input).value = "models/one.bin"
    panel.query_one("#volume-files-local-path", Input).value = "safe.bin"


async def test_panel_loading_and_read_results_are_usable_and_source_backed() -> None:
    source = StaticVolumeFilesOperationsSource(listing=_listing(), logs=_logs())
    panel = VolumeFilesOperationsPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-pod-id", Input).value = "pod-1"
        panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        assert "models/one.bin" in str(panel.query_one("#volume-files-result", Static).content)

        panel.query_one("#volume-files-logs", Button).press()
        await pilot.pause()
        assert "ready" in str(panel.query_one("#volume-files-result", Static).content)

    assert source.calls == [("list", False), ("logs", False)]


async def test_panel_derives_empty_unavailable_and_stale_read_states_locally() -> None:
    empty_panel = VolumeFilesOperationsPanel(StaticVolumeFilesOperationsSource())
    empty_app = PanelApp(empty_panel)
    async with empty_app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(empty_panel)
        empty_panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        assert "Empty: no objects matched" in str(
            empty_panel.query_one("#volume-files-result", Static).content
        )

    class AlwaysUnavailable(StaticVolumeFilesOperationsSource):
        async def list_objects(self, **kwargs: object) -> VolumeFileResult:
            del kwargs
            raise RuntimeError("provider diagnostic must not be rendered")

    unavailable_panel = VolumeFilesOperationsPanel(AlwaysUnavailable())
    unavailable_app = PanelApp(unavailable_panel)
    async with unavailable_app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(unavailable_panel)
        unavailable_panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        assert "Unavailable: no current bounded provider result" in str(
            unavailable_panel.query_one("#volume-files-result", Static).content
        )

    class BecomesUnavailable(StaticVolumeFilesOperationsSource):
        attempts = 0

        async def list_objects(self, **kwargs: object) -> VolumeFileResult:
            del kwargs
            self.attempts += 1
            if self.attempts == 1:
                return _listing()
            raise RuntimeError("provider diagnostic must not be rendered")

    stale_panel = VolumeFilesOperationsPanel(BecomesUnavailable())
    stale_app = PanelApp(stale_panel)
    async with stale_app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(stale_panel)
        stale_panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        stale_panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        rendered = str(stale_panel.query_one("#volume-files-result", Static).content)
        assert "Stale: refresh failed" in rendered
        assert "models/one.bin" in rendered


async def test_panel_renders_service_progress_events() -> None:
    class ProgressSource(StaticVolumeFilesOperationsSource):
        async def list_objects(self, **kwargs: object) -> VolumeFileResult:
            callback = cast(ProgressCallback, kwargs["progress"])
            event = VolumeFileProgress(1, "list", "requesting", 0, None)
            result = callback(event)
            if inspect.isawaitable(result):
                await result
            return VolumeFileResult(operation="list", status="completed", progress=(event,))

    panel = VolumeFilesOperationsPanel(ProgressSource())
    app = PanelApp(panel)
    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        rendered = str(panel.query_one("#volume-files-result", Static).content)
        assert "Progress 1: list requesting (0/unknown bytes)" in rendered


async def test_panel_mutations_preview_before_typed_confirmation_and_cancel_is_zero_write() -> None:
    source = StaticVolumeFilesOperationsSource(
        mutation_result=VolumeFileResult(operation="delete", status="dry_run")
    )
    panel = VolumeFilesOperationsPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-preview-delete", Button).press()
        await pilot.pause()
        assert source.calls == [("delete", True)]
        assert not panel.query_one("#volume-files-apply", Button).disabled

        panel.query_one("#volume-files-apply", Button).press()
        await pilot.pause()
        await pilot.click("#volume-files-confirm-text")
        await pilot.press(*list("wrong"))
        await pilot.click("#volume-files-confirm-apply")
        await pilot.pause()

    assert source.calls == [("delete", True)]


async def test_panel_apply_after_exact_confirmation_invokes_the_same_source_once() -> None:
    source = StaticVolumeFilesOperationsSource(
        mutation_result=VolumeFileResult(operation="delete", status="completed")
    )
    panel = VolumeFilesOperationsPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-preview-delete", Button).press()
        await pilot.pause()
        panel.query_one("#volume-files-apply", Button).press()
        await pilot.pause()
        await pilot.click("#volume-files-confirm-text")
        await pilot.press(*list("models/one.bin"))
        await pilot.click("#volume-files-confirm-apply")
        await pilot.pause()

        assert "Delete complete" in str(panel.query_one("#volume-files-result", Static).content)

    assert source.calls == [("delete", True), ("delete", False)]


async def test_download_previews_then_downloads_once_after_exact_confirmation() -> None:
    source = StaticVolumeFilesOperationsSource(
        mutation_result=VolumeFileResult(operation="download", status="completed")
    )
    panel = VolumeFilesOperationsPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-preview-download", Button).press()
        await pilot.pause()
        assert source.calls == [("download", True)]
        panel.query_one("#volume-files-apply", Button).press()
        await pilot.pause()
        await pilot.click("#volume-files-confirm-text")
        await pilot.press(*list("models/one.bin"))
        await pilot.click("#volume-files-confirm-apply")
        await pilot.pause()

    assert source.calls == [("download", True), ("download", False)]


async def test_confirmation_cancel_button_dismisses_without_writing() -> None:
    source = StaticVolumeFilesOperationsSource(
        mutation_result=VolumeFileResult(operation="delete", status="dry_run")
    )
    panel = VolumeFilesOperationsPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-preview-delete", Button).press()
        await pilot.pause()
        panel.query_one("#volume-files-apply", Button).press()
        await pilot.pause()
        assert app.screen.query("#volume-files-confirm-cancel")
        await pilot.click("#volume-files-confirm-cancel")
        await pilot.pause()
        assert not app.screen.query("#volume-files-confirm-cancel")

    assert source.calls == [("delete", True)]


async def test_confirmation_names_target_effect_provider_ceiling_and_consequence() -> None:
    source = StaticVolumeFilesOperationsSource(
        mutation_result=VolumeFileResult(
            operation="delete",
            status="dry_run",
            target="runpod:US-KS-2/vol-1/models/one.bin",
            effect="permanently delete the provider object",
            estimated_ceiling="one provider delete request",
            irreversible=True,
            consequences="The stored bytes cannot be recovered by Pitwall.",
        )
    )
    panel = VolumeFilesOperationsPanel(source)
    app = PanelApp(panel)

    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-preview-delete", Button).press()
        await pilot.pause()
        panel.query_one("#volume-files-apply", Button).press()
        await pilot.pause()
        rendered = "\n".join(str(widget.content) for widget in app.screen.query(Static))

        assert "Provider: runpod" in rendered
        assert "Target: runpod:US-KS-2/vol-1/models/one.bin" in rendered
        assert "Effect: permanently delete the provider object" in rendered
        assert "Estimated ceiling: one provider delete request" in rendered
        assert "Irreversible consequences: The stored bytes cannot be recovered" in rendered


@pytest.mark.parametrize("overwrite", [False, True], ids=["create-only", "overwrite"])
async def test_upload_preview_and_apply_preserve_the_explicit_overwrite_choice(
    overwrite: bool,
) -> None:
    class CaptureSource(StaticVolumeFilesOperationsSource):
        def __init__(self) -> None:
            super().__init__()
            self.overwrite_values: list[bool] = []

        async def upload_file(
            self,
            *,
            dry_run: bool,
            overwrite: bool,
            **kwargs: object,
        ) -> VolumeFileResult:
            del kwargs
            self.calls.append(("upload", dry_run))
            self.overwrite_values.append(overwrite)
            return VolumeFileResult(
                operation="upload",
                status="dry_run" if dry_run else "completed",
                target="runpod:US-KS-2/vol-1/models/one.bin",
                effect="replace" if overwrite else "create only",
                estimated_ceiling="3 bytes and one provider write",
                irreversible=overwrite,
                consequences="existing bytes will be replaced" if overwrite else None,
            )

    source = CaptureSource()
    panel = VolumeFilesOperationsPanel(source)
    app = PanelApp(panel)
    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-overwrite", Checkbox).value = overwrite
        panel.query_one("#volume-files-preview-upload", Button).press()
        await pilot.pause()
        panel.query_one("#volume-files-apply", Button).press()
        await pilot.pause()
        await pilot.click("#volume-files-confirm-text")
        await pilot.press(*list("models/one.bin"))
        await pilot.click("#volume-files-confirm-apply")
        await pilot.pause()

    assert source.overwrite_values == [overwrite, overwrite]


async def test_in_flight_mutation_cancel_warns_that_completion_is_ambiguous() -> None:
    class SlowMutationSource(StaticVolumeFilesOperationsSource):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()

        async def delete_object(self, *, dry_run: bool, **kwargs: object) -> VolumeFileResult:
            del kwargs
            self.calls.append(("delete", dry_run))
            if dry_run:
                return VolumeFileResult(
                    operation="delete",
                    status="dry_run",
                    target="runpod:US-KS-2/vol-1/models/one.bin",
                    effect="delete",
                    estimated_ceiling="one provider delete request",
                    irreversible=True,
                    consequences="stored bytes cannot be recovered",
                )
            self.started.set()
            await asyncio.sleep(60)
            return VolumeFileResult(operation="delete", status="completed")

    source = SlowMutationSource()
    panel = VolumeFilesOperationsPanel(source)
    app = PanelApp(panel)
    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-preview-delete", Button).press()
        await pilot.pause()
        panel.query_one("#volume-files-apply", Button).press()
        await pilot.pause()
        await pilot.click("#volume-files-confirm-text")
        await pilot.press(*list("models/one.bin"))
        await pilot.click("#volume-files-confirm-apply")
        await asyncio.wait_for(source.started.wait(), timeout=HANG_GUARD_SECS)
        panel.query_one("#volume-files-cancel", Button).press()
        await pilot.pause()

        rendered = str(panel.query_one("#volume-files-result", Static).content)
        assert "Mutation cancellation is ambiguous" in rendered
        assert "Inspect provider/local state and durable audit" in rendered


async def test_panel_cancel_and_error_keep_the_operator_surface_alive_and_non_disclosing() -> None:
    class SlowSource(StaticVolumeFilesOperationsSource):
        async def list_objects(self, **kwargs: object) -> VolumeFileResult:
            del kwargs
            await asyncio.sleep(60)
            return _listing()

    panel = VolumeFilesOperationsPanel(SlowSource())
    app = PanelApp(panel)
    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        panel.query_one("#volume-files-cancel", Button).press()
        await pilot.pause()
        assert "Operation cancelled" in str(panel.query_one("#volume-files-result", Static).content)

    class FailingSource(StaticVolumeFilesOperationsSource):
        async def read_pod_logs(self, **kwargs: object) -> VolumeFileResult:
            del kwargs
            raise RuntimeError("Authorization: Bearer tui-secret-canary")

    failed_panel = VolumeFilesOperationsPanel(FailingSource())
    failed_app = PanelApp(failed_panel)
    async with failed_app.run_test(size=(140, 40)) as pilot:
        failed_panel.query_one("#volume-files-pod-id", Input).value = "pod-1"
        failed_panel.query_one("#volume-files-logs", Button).press()
        await pilot.pause()
        rendered = str(failed_panel.query_one("#volume-files-error", Static).content)
        assert rendered == "Volume-file operation unavailable."
        assert "tui-secret-canary" not in rendered


async def test_starting_a_second_action_keeps_its_cancel_button_and_message() -> None:
    class SlowSource(StaticVolumeFilesOperationsSource):
        async def list_objects(self, **kwargs: object) -> VolumeFileResult:
            del kwargs
            await asyncio.sleep(60)
            return _listing()

        async def read_pod_logs(self, **kwargs: object) -> VolumeFileResult:
            del kwargs
            await asyncio.sleep(60)
            return _logs()

    panel = VolumeFilesOperationsPanel(SlowSource())
    app = PanelApp(panel)
    async with app.run_test(size=(140, 40)) as pilot:
        _set_common_inputs(panel)
        panel.query_one("#volume-files-pod-id", Input).value = "pod-1"
        panel.query_one("#volume-files-list", Button).press()
        await pilot.pause()
        panel.query_one("#volume-files-logs", Button).press()
        await pilot.pause()
        await pilot.pause()

        cancel = panel.query_one("#volume-files-cancel", Button)
        result = str(panel.query_one("#volume-files-result", Static).content)
        assert cancel.disabled is False
        assert "Operation cancelled" not in result

        cancel.press()
        await pilot.pause()
        assert "Operation cancelled" in str(panel.query_one("#volume-files-result", Static).content)


async def test_service_source_calls_the_shared_service_not_a_provider_client() -> None:
    class Store:
        def __init__(self) -> None:
            self.calls = 0

        async def list_objects(
            self,
            _volume_id: str,
            _data_center_id: str,
            *,
            prefix: str,
            max_items: int,
        ) -> VolumeObjectPage:
            del prefix, max_items
            self.calls += 1
            return VolumeObjectPage((VolumeObject(key="models/one.bin", size=3),), False)

        async def put_object(self, **_kwargs: object) -> None:
            raise AssertionError("not used")

        async def get_object_range(self, **_kwargs: object) -> bytes:
            raise AssertionError("not used")

        async def delete_object(self, **_kwargs: object) -> None:
            raise AssertionError("not used")

    class Logs:
        async def read(self, **_kwargs: object) -> BoundedPodLogPayload:
            return BoundedPodLogPayload(text="", bytes_read=0, truncated=False)

    store = Store()
    service = VolumeFileService(store, Logs())
    source = ServiceVolumeFilesOperationsSource(lambda: service)

    result = await source.list_objects(volume_id="vol-1", data_center_id="US-KS-2", prefix="")

    assert result.objects[0].key == "models/one.bin"
    assert store.calls == 1
    source_text = inspect.getsource(volume_files)
    assert "NetworkVolumeClient" not in source_text


async def test_panel_guardrail_failure_is_pre_provider_and_non_disclosing() -> None:
    class Logs:
        def __init__(self) -> None:
            self.calls = 0

        async def read(self, **_kwargs: object) -> BoundedPodLogPayload:
            self.calls += 1
            return BoundedPodLogPayload(text="", bytes_read=0, truncated=False)

    secret = "sk-1234567890abcdef1234567890abcdef"
    logs = Logs()
    service = VolumeFileService(cast(VolumeObjectStore, object()), logs)
    panel = VolumeFilesOperationsPanel(ServiceVolumeFilesOperationsSource(lambda: service))
    app = PanelApp(panel)

    async with app.run_test(size=(140, 40)) as pilot:
        panel.query_one("#volume-files-pod-id", Input).value = secret
        panel.query_one("#volume-files-logs", Button).press()
        await pilot.pause()
        rendered = str(panel.query_one("#volume-files-error", Static).content)

    assert rendered == "Volume-file operation unavailable."
    assert secret not in rendered
    assert logs.calls == 0
