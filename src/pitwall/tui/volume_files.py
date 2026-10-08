"""Operations-panel widget and injectable source for bounded RP-04 actions.

The widget contains no provider client import or transfer logic.  It owns only
operator interaction and delegates every read, preview, confirmation, and write
to the same ``VolumeFileService`` methods used by REST, MCP, and CLI.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import uuid
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Static

from pitwall.runpod_files import (
    ProgressCallback,
    VolumeFileProgress,
    VolumeFileResult,
    VolumeFileService,
)

ServiceFactory = Callable[[], VolumeFileService | Awaitable[VolumeFileService]]
OperationAction = Literal["upload", "download", "delete"]


class VolumeFilesOperationsSource(Protocol):
    """Feature-local TUI source that exposes only shared service operations."""

    async def list_objects(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        prefix: str,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult: ...

    async def read_pod_logs(
        self,
        *,
        pod_id: str,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult: ...

    async def upload_file(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        local_root: Path,
        local_path: str,
        overwrite: bool,
        dry_run: bool,
        idempotency_key: str | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult: ...

    async def download_to_path(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        local_root: Path,
        local_path: str,
        overwrite: bool,
        dry_run: bool,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult: ...

    async def delete_object(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        confirm_delete: bool,
        dry_run: bool,
        idempotency_key: str | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult: ...


class ServiceVolumeFilesOperationsSource:
    """TUI source that constructs and closes the common shared service per action."""

    def __init__(self, service_factory: ServiceFactory) -> None:
        self._service_factory = service_factory

    async def list_objects(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        prefix: str,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        return await self._call(
            lambda service: service.list_objects(
                volume_id=volume_id,
                data_center_id=data_center_id,
                prefix=prefix,
                cancel_event=cancel_event,
                progress=progress,
            )
        )

    async def read_pod_logs(
        self,
        *,
        pod_id: str,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        return await self._call(
            lambda service: service.read_pod_logs(
                pod_id=pod_id,
                cancel_event=cancel_event,
                progress=progress,
            )
        )

    async def upload_file(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        local_root: Path,
        local_path: str,
        overwrite: bool,
        dry_run: bool,
        idempotency_key: str | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        return await self._call(
            lambda service: service.upload_file(
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=object_key,
                local_root=local_root,
                local_path=local_path,
                overwrite=overwrite,
                dry_run=dry_run,
                idempotency_key=idempotency_key,
                cancel_event=cancel_event,
                progress=progress,
            )
        )

    async def download_to_path(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        local_root: Path,
        local_path: str,
        overwrite: bool,
        dry_run: bool,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        return await self._call(
            lambda service: service.download_to_path(
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=object_key,
                local_root=local_root,
                local_path=local_path,
                overwrite=overwrite,
                dry_run=dry_run,
                cancel_event=cancel_event,
                progress=progress,
            )
        )

    async def delete_object(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        confirm_delete: bool,
        dry_run: bool,
        idempotency_key: str | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        return await self._call(
            lambda service: service.delete_object(
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=object_key,
                confirm_delete=confirm_delete,
                dry_run=dry_run,
                idempotency_key=idempotency_key,
                cancel_event=cancel_event,
                progress=progress,
            )
        )

    async def _call(
        self,
        operation: Callable[[VolumeFileService], Awaitable[VolumeFileResult]],
    ) -> VolumeFileResult:
        pending_service = self._service_factory()
        service = await pending_service if inspect.isawaitable(pending_service) else pending_service
        try:
            return await operation(service)
        finally:
            await service.aclose()


@dataclass(frozen=True, slots=True)
class VolumeFilesSnapshot:
    """Small, sanitized state projection rendered by the Operations panel."""

    listing: VolumeFileResult | None
    logs: VolumeFileResult | None
    refreshed_at: dt.datetime

    @property
    def summary(self) -> str:
        object_count = len(self.listing.objects) if self.listing is not None else 0
        log_count = len(self.logs.logs) if self.logs is not None else 0
        return f"Volume files: {object_count} objects | {log_count} log lines"


class StaticVolumeFilesOperationsSource:
    """Hermetic source for panel/Pilot tests and local operator demos."""

    def __init__(
        self,
        *,
        listing: VolumeFileResult | None = None,
        logs: VolumeFileResult | None = None,
        mutation_result: VolumeFileResult | None = None,
    ) -> None:
        self.listing = listing or VolumeFileResult(operation="list", status="completed")
        self.logs = logs or VolumeFileResult(operation="logs", status="completed")
        self.mutation_result = mutation_result or VolumeFileResult(
            operation="upload", status="dry_run"
        )
        self.calls: list[tuple[str, bool]] = []

    async def list_objects(self, **_kwargs: object) -> VolumeFileResult:
        self.calls.append(("list", False))
        return self.listing

    async def read_pod_logs(self, **_kwargs: object) -> VolumeFileResult:
        self.calls.append(("logs", False))
        return self.logs

    async def upload_file(self, *, dry_run: bool, **_kwargs: object) -> VolumeFileResult:
        self.calls.append(("upload", dry_run))
        return self.mutation_result

    async def download_to_path(self, *, dry_run: bool, **_kwargs: object) -> VolumeFileResult:
        self.calls.append(("download", dry_run))
        return self.mutation_result

    async def delete_object(self, *, dry_run: bool, **_kwargs: object) -> VolumeFileResult:
        self.calls.append(("delete", dry_run))
        return self.mutation_result


@dataclass(frozen=True, slots=True)
class _PendingAction:
    action: OperationAction
    volume_id: str
    data_center_id: str
    object_key: str
    idempotency_key: str
    overwrite: bool = False
    local_root: Path | None = None
    local_path: str | None = None


class ConfirmVolumeFileModal(ModalScreen[bool]):
    """Type the exact target key before a previewed file mutation can run."""

    def __init__(self, pending: _PendingAction, preview: VolumeFileResult) -> None:
        super().__init__()
        self._pending = pending
        self._preview = preview

    def compose(self) -> ComposeResult:
        with Vertical(id="volume-files-confirm-modal"):
            yield Static(f"Confirm {self._pending.action}")
            yield Static(f"Provider: {self._preview.provider}")
            yield Static(f"Target: {self._preview.target or self._pending.object_key}")
            yield Static(f"Effect: {self._preview.effect or 'not available'}")
            yield Static(f"Estimated ceiling: {self._preview.estimated_ceiling or 'not available'}")
            yield Static(
                "Irreversible consequences: "
                + (
                    self._preview.consequences
                    if self._preview.irreversible and self._preview.consequences
                    else "None identified by the bounded preview."
                )
            )
            yield Static("Type the exact object key to apply the previewed operation.")
            yield Input(id="volume-files-confirm-text")
            with Horizontal():
                yield Button("Cancel", id="volume-files-confirm-cancel")
                yield Button(
                    "Apply", id="volume-files-confirm-apply", disabled=True, variant="error"
                )

    async def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#volume-files-confirm-apply", Button).disabled = (
            event.value != self._pending.object_key
        )

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "volume-files-confirm-cancel":
            self.dismiss(False)
        elif event.button.id == "volume-files-confirm-apply" and not event.button.disabled:
            self.dismiss(True)


class VolumeFilesOperationsPanel(VerticalScroll):
    """Attachable Operations panel: reads and previews first, typed confirmation for writes."""

    DEFAULT_CSS = """
    VolumeFilesOperationsPanel {
        height: auto;
        padding: 1;
        border: solid $primary;
    }
    #volume-files-controls, #volume-files-actions { height: auto; }
    #volume-files-result { height: auto; margin-top: 1; }
    #volume-files-error { color: $error; }
    """

    def __init__(self, source: VolumeFilesOperationsSource) -> None:
        super().__init__(id="volume-files-operations")
        self._source = source
        self._cancel_event: asyncio.Event | None = None
        self._active_task: asyncio.Task[None] | None = None
        self._pending: _PendingAction | None = None
        self._preview_result: VolumeFileResult | None = None
        self._last_result: VolumeFileResult | None = None
        self._active_mutation = False

    def compose(self) -> ComposeResult:
        yield Static("Volume files and bounded pod diagnostics", id="volume-files-title")
        yield Static(
            "Read-only until a dry-run preview and typed confirmation.", id="volume-files-summary"
        )
        with Vertical(id="volume-files-controls"):
            yield Input(placeholder="Volume ID", id="volume-files-volume-id")
            yield Input(
                placeholder="Data center (for example US-KS-2)", id="volume-files-data-center"
            )
            yield Input(placeholder="Relative object key / prefix", id="volume-files-object-key")
            yield Input(placeholder="Relative local path", id="volume-files-local-path")
            yield Input(value=".", id="volume-files-root")
            yield Input(placeholder="Pod ID for logs", id="volume-files-pod-id")
            yield Checkbox(
                "Allow replacement if the object/local target already exists",
                value=False,
                id="volume-files-overwrite",
            )
        with Horizontal(id="volume-files-actions"):
            yield Button("List", id="volume-files-list")
            yield Button("Logs", id="volume-files-logs")
            yield Button("Preview upload", id="volume-files-preview-upload")
            yield Button("Preview download", id="volume-files-preview-download")
            yield Button("Preview delete", id="volume-files-preview-delete", variant="warning")
            yield Button("Apply preview", id="volume-files-apply", disabled=True, variant="error")
            yield Button("Cancel", id="volume-files-cancel", disabled=True)
        yield Static("Ready", id="volume-files-result")
        yield Static("", id="volume-files-error")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        action = event.button.id
        if action == "volume-files-cancel":
            self._cancel_active()
            return
        if action == "volume-files-apply":
            self._open_confirmation()
            return
        if action == "volume-files-list":
            self._start(self._list())
        elif action == "volume-files-logs":
            self._start(self._logs())
        elif action == "volume-files-preview-upload":
            self._start(self._preview("upload"))
        elif action == "volume-files-preview-download":
            self._start(self._preview("download"))
        elif action == "volume-files-preview-delete":
            self._start(self._preview("delete"))

    def _start(
        self,
        operation: Coroutine[object, object, None],
        *,
        mutation: bool = False,
    ) -> None:
        self._cancel_active()
        self._cancel_event = asyncio.Event()
        self._active_mutation = mutation
        self.query_one("#volume-files-error", Static).update("")
        self.query_one("#volume-files-result", Static).update("Loading bounded operation…")
        self.query_one("#volume-files-cancel", Button).disabled = False
        self._active_task = asyncio.create_task(operation)

    async def _list(self) -> None:
        await self._execute_read(
            lambda cancel: self._source.list_objects(
                volume_id=self._value("volume-id"),
                data_center_id=self._value("data-center"),
                prefix=self._value("object-key"),
                cancel_event=cancel,
                progress=self._show_progress,
            )
        )

    async def _logs(self) -> None:
        await self._execute_read(
            lambda cancel: self._source.read_pod_logs(
                pod_id=self._value("pod-id"),
                cancel_event=cancel,
                progress=self._show_progress,
            )
        )

    async def _preview(self, action: OperationAction) -> None:
        pending = self._pending_from_inputs(action)
        if pending is None:
            return
        try:
            if action == "upload":
                result = await self._source.upload_file(
                    volume_id=pending.volume_id,
                    data_center_id=pending.data_center_id,
                    object_key=pending.object_key,
                    local_root=pending.local_root or Path("."),
                    local_path=pending.local_path or "",
                    overwrite=pending.overwrite,
                    dry_run=True,
                    idempotency_key=pending.idempotency_key,
                    cancel_event=self._cancel_event,
                    progress=self._show_progress,
                )
            elif action == "download":
                result = await self._source.download_to_path(
                    volume_id=pending.volume_id,
                    data_center_id=pending.data_center_id,
                    object_key=pending.object_key,
                    local_root=pending.local_root or Path("."),
                    local_path=pending.local_path or "",
                    overwrite=pending.overwrite,
                    dry_run=True,
                    cancel_event=self._cancel_event,
                    progress=self._show_progress,
                )
            else:
                result = await self._source.delete_object(
                    volume_id=pending.volume_id,
                    data_center_id=pending.data_center_id,
                    object_key=pending.object_key,
                    confirm_delete=True,
                    dry_run=True,
                    idempotency_key=pending.idempotency_key,
                    cancel_event=self._cancel_event,
                    progress=self._show_progress,
                )
        except asyncio.CancelledError:
            self._show_cancelled()
            return
        except Exception:  # reason: TUI boundary renders a redacted operation error
            self._show_safe_error()
            return
        self._pending = pending
        self._preview_result = result
        self.query_one("#volume-files-apply", Button).disabled = False
        self._render_result(result, prefix=f"{action.title()} preview")
        self._finish()

    def _open_confirmation(self) -> None:
        pending = self._pending
        preview = self._preview_result
        if pending is None or preview is None:
            return
        self.app.push_screen(
            ConfirmVolumeFileModal(pending, preview),
            callback=lambda confirmed: self._after_confirmation(pending, bool(confirmed)),
        )

    def _after_confirmation(self, pending: _PendingAction, confirmed: bool) -> None:
        if confirmed:
            self._start(self._apply(pending), mutation=True)

    async def _apply(self, pending: _PendingAction) -> None:
        try:
            if pending.action == "upload":
                result = await self._source.upload_file(
                    volume_id=pending.volume_id,
                    data_center_id=pending.data_center_id,
                    object_key=pending.object_key,
                    local_root=pending.local_root or Path("."),
                    local_path=pending.local_path or "",
                    overwrite=pending.overwrite,
                    dry_run=False,
                    idempotency_key=pending.idempotency_key,
                    cancel_event=self._cancel_event,
                    progress=self._show_progress,
                )
            elif pending.action == "download":
                result = await self._source.download_to_path(
                    volume_id=pending.volume_id,
                    data_center_id=pending.data_center_id,
                    object_key=pending.object_key,
                    local_root=pending.local_root or Path("."),
                    local_path=pending.local_path or "",
                    overwrite=pending.overwrite,
                    dry_run=False,
                    cancel_event=self._cancel_event,
                    progress=self._show_progress,
                )
            else:
                result = await self._source.delete_object(
                    volume_id=pending.volume_id,
                    data_center_id=pending.data_center_id,
                    object_key=pending.object_key,
                    confirm_delete=True,
                    dry_run=False,
                    idempotency_key=pending.idempotency_key,
                    cancel_event=self._cancel_event,
                    progress=self._show_progress,
                )
        except asyncio.CancelledError:
            self._show_cancelled()
            return
        except Exception:  # reason: TUI boundary renders a redacted operation error
            self._show_safe_error()
            return
        self._pending = None
        self._preview_result = None
        self.query_one("#volume-files-apply", Button).disabled = True
        self._render_result(result, prefix=f"{pending.action.title()} complete")
        self._finish()

    async def _execute_read(
        self,
        operation: Callable[[asyncio.Event | None], Awaitable[VolumeFileResult]],
    ) -> None:
        try:
            resolved = await operation(self._cancel_event)
        except asyncio.CancelledError:
            self._show_cancelled()
            return
        except Exception:  # reason: TUI boundary renders a redacted operation error
            self._show_safe_error()
            return
        self._render_result(resolved)
        self._finish()

    def _pending_from_inputs(self, action: OperationAction) -> _PendingAction | None:
        volume_id = self._value("volume-id")
        data_center_id = self._value("data-center")
        object_key = self._value("object-key")
        if not volume_id or not data_center_id or not object_key:
            self.query_one("#volume-files-error", Static).update(
                "Volume, data center, and object key are required."
            )
            self._finish()
            return None
        if action == "delete":
            return _PendingAction(
                action=action,
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=object_key,
                idempotency_key=uuid.uuid4().hex,
            )
        local_path = self._value("local-path")
        if not local_path:
            self.query_one("#volume-files-error", Static).update(
                "A relative local path is required."
            )
            self._finish()
            return None
        return _PendingAction(
            action=action,
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=object_key,
            idempotency_key=uuid.uuid4().hex,
            overwrite=self.query_one("#volume-files-overwrite", Checkbox).value,
            local_root=Path(self._value("root") or "."),
            local_path=local_path,
        )

    def _render_result(self, result: VolumeFileResult, *, prefix: str | None = None) -> None:
        self._last_result = result
        lines = _result_lines(result, prefix=prefix)
        self.query_one("#volume-files-result", Static).update("\n".join(lines))

    def _show_progress(self, event: VolumeFileProgress) -> None:
        self.query_one("#volume-files-result", Static).update(_progress_text(event))

    def _is_current_operation(self) -> bool:
        return self._active_task is asyncio.current_task()

    def _show_cancelled(self) -> None:
        if not self._is_current_operation():
            return
        message = "Operation cancelled before a result was returned."
        if self._active_mutation:
            message = (
                "Mutation cancellation is ambiguous: the write may have completed. "
                "Inspect provider/local state and durable audit before retrying the retained "
                "preview, which preserves its original idempotency key where supported."
            )
        self.query_one("#volume-files-result", Static).update(message)
        self._finish()

    def _show_safe_error(self) -> None:
        if not self._is_current_operation():
            return
        if self._last_result is None:
            self.query_one("#volume-files-result", Static).update(
                "Unavailable: no current bounded provider result is available."
            )
        else:
            lines = [
                "Stale: refresh failed; showing the last known bounded result.",
                *_result_lines(self._last_result),
            ]
            self.query_one("#volume-files-result", Static).update("\n".join(lines))
        self.query_one("#volume-files-error", Static).update("Volume-file operation unavailable.")
        self._finish()

    def _finish(self) -> None:
        if not self._is_current_operation():
            return
        self._active_task = None
        self._cancel_event = None
        self._active_mutation = False
        self.query_one("#volume-files-cancel", Button).disabled = True

    def _cancel_active(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
        if self._active_task is not None and not self._active_task.done():
            self._active_task.cancel()

    def _value(self, suffix: str) -> str:
        return self.query_one(f"#volume-files-{suffix}", Input).value.strip()


def _result_lines(result: VolumeFileResult, *, prefix: str | None = None) -> list[str]:
    lines = [prefix or f"{result.operation.title()} result", f"Status: {result.status}"]
    if result.operation == "list" and not result.objects:
        lines.append("Empty: no objects matched the bounded request.")
    elif result.operation == "logs" and not result.logs:
        lines.append("Empty: no pod log lines were returned.")
    if result.objects:
        lines.append("Objects: " + ", ".join(item.key for item in result.objects))
    if result.logs:
        lines.extend(f"{line.timestamp or '-'} {line.text}" for line in result.logs)
    if result.truncated:
        lines.append("Output truncated by the configured limit.")
    if result.progress:
        lines.append(_progress_text(result.progress[-1]))
    if result.replayed:
        lines.append("Replayed: no duplicate provider mutation was sent.")
    return lines


def _progress_text(event: VolumeFileProgress) -> str:
    total = "unknown" if event.total_bytes is None else str(event.total_bytes)
    return (
        f"Progress {event.sequence}: {event.operation} {event.phase} "
        f"({event.bytes_completed}/{total} bytes)"
    )


__all__ = [
    "ConfirmVolumeFileModal",
    "ServiceVolumeFilesOperationsSource",
    "StaticVolumeFilesOperationsSource",
    "VolumeFilesOperationsPanel",
    "VolumeFilesOperationsSource",
    "VolumeFilesSnapshot",
]
