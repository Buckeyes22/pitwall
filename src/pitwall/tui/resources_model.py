"""Resources view data: typed entries, snapshots, the source protocol, and table formatting."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Literal, Protocol, cast

from pitwall.runpod_control_plane import (
    MutationResult,
)
from pitwall.tui.overview import as_utc

ResourceMutationOperation = Literal[
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
]
ResourceQueryOperation = Literal[
    "pod.get",
    "endpoint.get",
    "template.get",
    "volume.get",
    "registry_auth.get",
    "hub_template.get",
    "hub_template.search",
]


class ResourcesSource(Protocol):
    """Async source and guarded action boundary for the Resources screen."""

    async def load_resources(self) -> ResourcesSnapshot:
        """Return one current or explicitly stale/unavailable snapshot."""

    async def query_resource(
        self, operation: ResourceQueryOperation, value: str
    ) -> dict[str, object]:
        """Run one explicit read-only get/search operation."""

    async def preview_mutation(self, draft: ResourceMutationDraft) -> MutationResult:
        """Return a zero-write preview using the exact service method."""

    async def apply_mutation(self, draft: ResourceMutationDraft) -> MutationResult:
        """Apply the exact previewed mutation after type confirmation."""


@dataclass(frozen=True, slots=True)
class ResourcePodEntry:
    pod_id: str
    name: str
    status: str
    gpu_type_id: str | None = None

    def as_row(self) -> tuple[str, str, str, str]:
        return (
            display_text(self.pod_id),
            display_text(self.name),
            display_text(self.status),
            display_text(self.gpu_type_id, fallback="none"),
        )


@dataclass(frozen=True, slots=True)
class ResourceEndpointEntry:
    endpoint_id: str
    name: str
    workers_min: int
    workers_max: int
    template_id: str | None
    scaling: str = "unknown"
    gpu_pools: tuple[str, ...] = ()

    @property
    def workers_label(self) -> str:
        return f"{self.workers_min}-{self.workers_max}"

    @property
    def template_label(self) -> str:
        return display_text(self.template_id, fallback="none")

    def as_row(self) -> tuple[str, str, str, str, str, str]:
        return (
            display_text(self.endpoint_id),
            display_text(self.name),
            self.workers_label,
            display_text(self.scaling),
            ",".join(display_text(pool) for pool in self.gpu_pools) or "provider default",
            self.template_label,
        )


@dataclass(frozen=True, slots=True)
class ResourceTemplateEntry:
    template_id: str
    name: str
    image_name: str
    is_serverless: bool

    @property
    def serverless_label(self) -> str:
        return "yes" if self.is_serverless else "no"

    def as_row(self) -> tuple[str, str, str, str]:
        return (
            display_text(self.template_id),
            display_text(self.name),
            display_text(self.image_name),
            self.serverless_label,
        )


@dataclass(frozen=True, slots=True)
class ResourceHubTemplateEntry:
    template_id: str
    name: str
    image_name: str
    is_serverless: bool

    def as_row(self) -> tuple[str, str, str, str]:
        return (
            display_text(self.template_id),
            display_text(self.name),
            display_text(self.image_name),
            "yes" if self.is_serverless else "no",
        )


@dataclass(frozen=True, slots=True)
class ResourceVolumeEntry:
    volume_id: str
    name: str
    size_gb: int
    data_center_id: str

    @property
    def size_label(self) -> str:
        return f"{self.size_gb} GB"

    def as_row(self) -> tuple[str, str, str, str]:
        return (
            display_text(self.volume_id),
            display_text(self.name),
            self.size_label,
            display_text(self.data_center_id),
        )


@dataclass(frozen=True, slots=True)
class RegistryAuthEntry:
    auth_id: str
    name: str

    def as_row(self) -> tuple[str, str]:
        return (display_text(self.auth_id), display_text(self.name))


@dataclass(frozen=True, slots=True)
class ResourcesSnapshot:
    endpoints: tuple[ResourceEndpointEntry, ...]
    templates: tuple[ResourceTemplateEntry, ...]
    volumes: tuple[ResourceVolumeEntry, ...]
    registry_auths: tuple[RegistryAuthEntry, ...]
    refreshed_at: dt.datetime
    stale_since: dt.datetime | None = None
    pods: tuple[ResourcePodEntry, ...] = ()
    hub_templates: tuple[ResourceHubTemplateEntry, ...] = ()
    stale_sections: tuple[str, ...] = ()
    unavailable_sections: tuple[str, ...] = ()
    unavailable_errors: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        counts = (
            _count_label(len(self.pods), "pod"),
            _count_label(len(self.endpoints), "endpoint"),
            _count_label(len(self.templates), "account template"),
            _count_label(len(self.hub_templates), "Hub template"),
            _count_label(len(self.volumes), "volume"),
            _count_label(len(self.registry_auths), "registry auth"),
        )
        state = []
        if self.stale_sections:
            state.append(f"stale: {', '.join(self.stale_sections)}")
        if self.unavailable_sections:
            state.append(f"unavailable: {', '.join(self.unavailable_sections)}")
        suffix = f" | {'; '.join(state)}" if state else ""
        return f"Resources: {' | '.join(counts)}{suffix}"

    @property
    def refreshed_label(self) -> str:
        label = as_utc(self.refreshed_at).strftime("%Y-%m-%d %H:%M UTC")
        if self.stale_since is not None:
            label += f" (retained data from {self.stale_since.strftime('%H:%M:%S UTC')})"
        return label


@dataclass(frozen=True, slots=True)
class ResourceMutationDraft:
    operation: ResourceMutationOperation
    payload: dict[str, object]
    idempotency_key: str

    @property
    def confirmation_target(self) -> str:
        value = self.payload.get("resource_id") or self.payload.get("name")
        if not isinstance(value, str) or not value.strip():
            raise ValueError("mutation payload requires resource_id or name for confirmation")
        return value.strip()


class StaticResourcesSource:
    """Hermetic source with optional scripted query/mutation results."""

    def __init__(
        self,
        snapshot: ResourcesSnapshot,
        *,
        preview_result: MutationResult | None = None,
        apply_result: MutationResult | None = None,
        query_result: dict[str, object] | None = None,
        preview_error: Exception | None = None,
        apply_error: Exception | None = None,
    ) -> None:
        self._snapshot = snapshot
        self._preview_result = preview_result
        self._apply_result = apply_result
        self._query_result = query_result
        self._preview_error = preview_error
        self._apply_error = apply_error
        self.load_count = 0
        self.calls: list[tuple[str, str]] = []

    async def load_resources(self) -> ResourcesSnapshot:
        self.load_count += 1
        return self._snapshot

    async def query_resource(
        self, operation: ResourceQueryOperation, value: str
    ) -> dict[str, object]:
        self.calls.append(("query", operation))
        return self._query_result or {"operation": operation, "value": value}

    async def preview_mutation(self, draft: ResourceMutationDraft) -> MutationResult:
        self.calls.append(("preview", draft.operation))
        if self._preview_error is not None:
            raise self._preview_error
        if self._preview_result is None:
            return MutationResult(
                operation=draft.operation,
                resource_type=draft.operation.split(".", 1)[0],
                resource_id=cast(str | None, draft.payload.get("resource_id")),
                dry_run=True,
                changed=False,
                effect="scripted preview",
                irreversible=draft.operation.endswith(("delete", "terminate", "replace")),
                idempotency_key=draft.idempotency_key,
            )
        return self._preview_result

    async def apply_mutation(self, draft: ResourceMutationDraft) -> MutationResult:
        self.calls.append(("apply", draft.operation))
        if self._apply_error is not None:
            raise self._apply_error
        if self._apply_result is None:
            return MutationResult(
                operation=draft.operation,
                resource_type=draft.operation.split(".", 1)[0],
                resource_id=cast(str | None, draft.payload.get("resource_id")),
                dry_run=False,
                changed=True,
                effect="scripted apply",
                idempotency_key=draft.idempotency_key,
            )
        return self._apply_result


def display_text(value: object, *, fallback: str = "unknown") -> str:
    if value is None:
        return fallback
    printable = "".join(character if character.isprintable() else " " for character in str(value))
    label = " ".join(printable.split())
    return label or fallback


def format_pod_table(entries: tuple[ResourcePodEntry, ...]) -> str:
    return _resource_table(
        ("Pod ID", "Name", "Status", "GPU"), [entry.as_row() for entry in entries], "pods"
    )


def format_endpoint_table(entries: tuple[ResourceEndpointEntry, ...]) -> str:
    return _resource_table(
        ("Endpoint ID", "Name", "Workers", "Scaling", "GPU pools", "Template"),
        [entry.as_row() for entry in entries],
        "endpoints",
    )


def format_template_table(entries: tuple[ResourceTemplateEntry, ...]) -> str:
    return _resource_table(
        ("Template ID", "Name", "Image", "Serverless"),
        [entry.as_row() for entry in entries],
        "account templates",
    )


def format_hub_template_table(entries: tuple[ResourceHubTemplateEntry, ...]) -> str:
    return _resource_table(
        ("Hub ID", "Name", "Image", "Serverless"),
        [entry.as_row() for entry in entries],
        "Hub templates",
    )


def format_volume_table(entries: tuple[ResourceVolumeEntry, ...]) -> str:
    return _resource_table(
        ("Volume ID", "Name", "Size", "Datacenter"),
        [entry.as_row() for entry in entries],
        "volumes",
    )


def format_registry_table(entries: tuple[RegistryAuthEntry, ...]) -> str:
    return _resource_table(
        ("Auth ID", "Name"), [entry.as_row() for entry in entries], "registry auths"
    )


def _resource_table[Row: tuple[str, ...]](header: Row, rows: list[Row], empty_label: str) -> str:
    if not rows:
        return f"{_format_table([header])}\n(empty · no {empty_label})"
    return _format_table([header, *rows])


def _format_table[Row: tuple[str, ...]](rows: list[Row]) -> str:
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
    rendered: list[str] = []
    for index, row in enumerate(rows):
        rendered.append(_format_row(row, widths))
        if index == 0:
            rendered.append("  ".join("-" * width for width in widths))
    return "\n".join(rendered)


def _format_row(row: tuple[str, ...], widths: list[int]) -> str:
    return "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(row))


def _count_label(count: int, singular: str) -> str:
    return f"{count} {singular}{'' if count == 1 else 's'}"


def filtered_snapshot(snapshot: ResourcesSnapshot, query: str) -> ResourcesSnapshot:
    if not query:
        return snapshot

    def matches(row: tuple[str, ...]) -> bool:
        return query in " ".join(row).casefold()

    return replace(
        snapshot,
        pods=tuple(item for item in snapshot.pods if matches(item.as_row())),
        endpoints=tuple(item for item in snapshot.endpoints if matches(item.as_row())),
        templates=tuple(item for item in snapshot.templates if matches(item.as_row())),
        hub_templates=tuple(item for item in snapshot.hub_templates if matches(item.as_row())),
        volumes=tuple(item for item in snapshot.volumes if matches(item.as_row())),
        registry_auths=tuple(item for item in snapshot.registry_auths if matches(item.as_row())),
    )
