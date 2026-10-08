"""The live RunPod resources source: control-plane reads and confirmed mutations."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Awaitable, Callable
from typing import Literal, cast

from pydantic import BaseModel, ValidationError

from pitwall.runpod_control_plane import (
    EndpointCreateRequest,
    EndpointResource,
    EndpointUpdateRequest,
    HubTemplateResource,
    IdentifiedMutationRequest,
    MutationResult,
    PodActionRequest,
    PodCreateRequest,
    PodResource,
    PodUpdateRequest,
    RegistryAuthCreateRequest,
    RegistryAuthReplaceRequest,
    RegistryAuthResource,
    RunPodControlPlaneService,
    TemplateCreateRequest,
    TemplateResource,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
    VolumeResource,
)
from pitwall.tui.overview import as_utc, utc_now
from pitwall.tui.resources_model import (
    RegistryAuthEntry,
    ResourceEndpointEntry,
    ResourceHubTemplateEntry,
    ResourceMutationDraft,
    ResourceMutationOperation,
    ResourcePodEntry,
    ResourceQueryOperation,
    ResourcesSnapshot,
    ResourceTemplateEntry,
    ResourceVolumeEntry,
)

log = logging.getLogger(__name__)
_SECTION_NAMES = ("pods", "endpoints", "templates", "hub", "volumes", "registry_auths")


class RunPodResourcesSource:
    """Read and mutate resources only through ``RunPodControlPlaneService``."""

    def __init__(
        self,
        *,
        service: RunPodControlPlaneService | None = None,
        mutation_service_factory: Callable[[], Awaitable[RunPodControlPlaneService]] | None = None,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self._service = service or RunPodControlPlaneService()
        self._mutation_service_factory = mutation_service_factory
        self._now = now or utc_now
        self._last_snapshot: ResourcesSnapshot | None = None

    async def load_resources(self) -> ResourcesSnapshot:
        calls: tuple[Awaitable[object], ...] = (
            self._service.list_pods(),
            self._service.list_endpoints(),
            self._service.list_templates(),
            self._service.list_hub_templates(limit=50, offset=0),
            self._service.list_volumes(),
            self._service.list_registry_auths(),
        )
        results = await asyncio.gather(*calls, return_exceptions=True)
        unavailable = tuple(
            name
            for name, value in zip(_SECTION_NAMES, results, strict=True)
            if isinstance(value, BaseException)
        )
        errors = tuple(
            type(value).__name__ for value in results if isinstance(value, BaseException)
        )
        for name, value in zip(_SECTION_NAMES, results, strict=True):
            if isinstance(value, BaseException):
                log.warning("resources section %s unavailable (%s)", name, type(value).__name__)
        previous = self._last_snapshot
        stale = tuple(
            name
            for name in unavailable
            if previous is not None and bool(getattr(previous, _snapshot_attribute(name)))
        )
        stale_since = None
        if stale and previous is not None:
            stale_since = previous.stale_since or previous.refreshed_at

        snapshot = ResourcesSnapshot(
            pods=_entries_or_previous(
                results[0], previous, "pods", lambda item: _pod_entry(cast(PodResource, item))
            ),
            endpoints=_entries_or_previous(
                results[1],
                previous,
                "endpoints",
                lambda item: _endpoint_entry(cast(EndpointResource, item)),
            ),
            templates=_entries_or_previous(
                results[2],
                previous,
                "templates",
                lambda item: _template_entry(cast(TemplateResource, item)),
            ),
            hub_templates=_entries_or_previous(
                results[3],
                previous,
                "hub_templates",
                lambda item: _hub_template_entry(cast(HubTemplateResource, item)),
            ),
            volumes=_entries_or_previous(
                results[4],
                previous,
                "volumes",
                lambda item: _volume_entry(cast(VolumeResource, item)),
            ),
            registry_auths=_entries_or_previous(
                results[5],
                previous,
                "registry_auths",
                lambda item: _registry_auth_entry(cast(RegistryAuthResource, item)),
            ),
            refreshed_at=as_utc(self._now()),
            stale_since=stale_since,
            stale_sections=stale,
            unavailable_sections=unavailable,
            unavailable_errors=errors,
        )
        self._last_snapshot = snapshot
        return snapshot

    async def query_resource(
        self, operation: ResourceQueryOperation, value: str
    ) -> dict[str, object]:
        if operation == "pod.get":
            pod = await self._service.get_pod(value)
            return pod.model_dump(mode="json")
        elif operation == "endpoint.get":
            endpoint = await self._service.get_endpoint(value)
            return endpoint.model_dump(mode="json")
        elif operation == "template.get":
            template = await self._service.get_template(value)
            return template.model_dump(mode="json")
        elif operation == "volume.get":
            volume = await self._service.get_volume(value)
            return volume.model_dump(mode="json")
        elif operation == "registry_auth.get":
            registry_auth = await self._service.get_registry_auth(value)
            return registry_auth.model_dump(mode="json")
        elif operation == "hub_template.get":
            hub_template = await self._service.get_hub_template(value)
            return hub_template.model_dump(mode="json")
        else:
            matches = await self._service.search_hub_templates(value, limit=50)
            return {"hub_templates": [item.model_dump(mode="json") for item in matches]}

    async def preview_mutation(self, draft: ResourceMutationDraft) -> MutationResult:
        request = _mutation_request(draft, intent="preview")
        return await _invoke_mutation(self._service, draft.operation, request)

    async def apply_mutation(self, draft: ResourceMutationDraft) -> MutationResult:
        service = await self._mutation_service()
        request = _mutation_request(draft, intent="apply")
        return await _invoke_mutation(service, draft.operation, request)

    async def _mutation_service(self) -> RunPodControlPlaneService:
        if self._mutation_service_factory is not None:
            return await self._mutation_service_factory()
        if self._service.has_audit_store:
            return self._service
        from pitwall.db import get_pool

        return RunPodControlPlaneService(audit_pool=await get_pool(), actor="system")


def _pod_entry(value: PodResource) -> ResourcePodEntry:
    return ResourcePodEntry(value.id, value.name, value.status, value.gpu_type_id)


def _endpoint_entry(value: EndpointResource) -> ResourceEndpointEntry:
    return ResourceEndpointEntry(
        endpoint_id=value.id,
        name=value.name,
        workers_min=value.workers.minimum,
        workers_max=value.workers.maximum,
        template_id=value.template_id,
        scaling=f"{value.scaling.type}:{value.scaling.value:g}",
        gpu_pools=value.gpu_pools,
    )


def _template_entry(value: TemplateResource) -> ResourceTemplateEntry:
    return ResourceTemplateEntry(value.id, value.name, value.image, value.serverless)


def _hub_template_entry(value: HubTemplateResource) -> ResourceHubTemplateEntry:
    return ResourceHubTemplateEntry(
        value.id, value.display_name or value.name, value.image, value.serverless
    )


def _volume_entry(value: VolumeResource) -> ResourceVolumeEntry:
    return ResourceVolumeEntry(value.id, value.name, value.size_gb, value.data_center_id)


def _registry_auth_entry(value: RegistryAuthResource) -> RegistryAuthEntry:
    return RegistryAuthEntry(value.id, value.name)


def _snapshot_attribute(section: str) -> str:
    return "hub_templates" if section == "hub" else section


def _entries_or_previous[Entry](
    result: object,
    previous: ResourcesSnapshot | None,
    attribute: str,
    converter: Callable[[object], Entry],
) -> tuple[Entry, ...]:
    if isinstance(result, BaseException):
        if previous is None:
            return ()
        return cast(tuple[Entry, ...], getattr(previous, attribute))
    if not isinstance(result, list):
        return ()
    return tuple(converter(item) for item in result)


def _mutation_request(
    draft: ResourceMutationDraft, *, intent: Literal["preview", "apply"]
) -> BaseModel:
    data = {**draft.payload, "intent": intent, "idempotency_key": draft.idempotency_key}
    model_by_operation: dict[ResourceMutationOperation, type[BaseModel]] = {
        "pod.create": PodCreateRequest,
        "pod.update": PodUpdateRequest,
        "pod.action": PodActionRequest,
        "pod.terminate": IdentifiedMutationRequest,
        "endpoint.create": EndpointCreateRequest,
        "endpoint.update": EndpointUpdateRequest,
        "endpoint.delete": IdentifiedMutationRequest,
        "template.create": TemplateCreateRequest,
        "template.update": TemplateUpdateRequest,
        "template.delete": IdentifiedMutationRequest,
        "volume.create": VolumeCreateRequest,
        "volume.grow": VolumeGrowRequest,
        "volume.delete": IdentifiedMutationRequest,
        "registry_auth.create": RegistryAuthCreateRequest,
        "registry_auth.replace": RegistryAuthReplaceRequest,
        "registry_auth.delete": IdentifiedMutationRequest,
    }
    try:
        return model_by_operation[draft.operation].model_validate(data)
    except ValidationError as exc:
        fields = []
        for error in exc.errors(include_input=False, include_url=False):
            path = ".".join(str(item) for item in error["loc"]) or "request"
            fields.append(f"{path}: {error['msg']}")
        raise ValueError("; ".join(fields)[:1024]) from exc


async def _invoke_mutation(
    service: RunPodControlPlaneService,
    operation: ResourceMutationOperation,
    request: BaseModel,
) -> MutationResult:
    if operation == "pod.create":
        return await service.create_pod(cast(PodCreateRequest, request))
    if operation == "pod.update":
        return await service.update_pod(cast(PodUpdateRequest, request))
    if operation == "pod.action":
        return await service.action_pod(cast(PodActionRequest, request))
    if operation == "pod.terminate":
        return await service.terminate_pod(cast(IdentifiedMutationRequest, request))
    if operation == "endpoint.create":
        return await service.create_endpoint(cast(EndpointCreateRequest, request))
    if operation == "endpoint.update":
        return await service.update_endpoint(cast(EndpointUpdateRequest, request))
    if operation == "endpoint.delete":
        return await service.delete_endpoint(cast(IdentifiedMutationRequest, request))
    if operation == "template.create":
        return await service.create_template(cast(TemplateCreateRequest, request))
    if operation == "template.update":
        return await service.update_template(cast(TemplateUpdateRequest, request))
    if operation == "template.delete":
        return await service.delete_template(cast(IdentifiedMutationRequest, request))
    if operation == "volume.create":
        return await service.create_volume(cast(VolumeCreateRequest, request))
    if operation == "volume.grow":
        return await service.grow_volume(cast(VolumeGrowRequest, request))
    if operation == "volume.delete":
        return await service.delete_volume(cast(IdentifiedMutationRequest, request))
    if operation == "registry_auth.create":
        return await service.create_registry_auth(cast(RegistryAuthCreateRequest, request))
    if operation == "registry_auth.replace":
        return await service.replace_registry_auth(cast(RegistryAuthReplaceRequest, request))
    return await service.delete_registry_auth(cast(IdentifiedMutationRequest, request))
