"""Isolated REST router for raw RunPod account resource administration.

The global FastAPI application registers this router with read scope for GETs
and existing server-admin plus secret authorization for mutations.
Broker-managed leases and serve-model remain the preferred paths for paid
compute.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.responses import JSONResponse, Response

from pitwall.api.exceptions import ApiErrorResponse
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
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    TemplateCreateRequest,
    TemplateResource,
    TemplateUpdateRequest,
    VolumeCreateRequest,
    VolumeGrowRequest,
    VolumeResource,
)


class _SafeValidationRoute(APIRoute):
    """Omit request values from feature-local FastAPI validation failures."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                details: list[str] = []
                for error in exc.errors():
                    path = ".".join(str(item) for item in error["loc"]) or "request"
                    details.append(f"{path}: {error['msg']}")
                return JSONResponse(
                    status_code=422,
                    content={
                        "error": "invalid_request",
                        "detail": "; ".join(details)[:1024],
                    },
                )

        return safe_handler


router = APIRouter(
    prefix="/v1/admin/runpod",
    tags=["runpod-resources"],
    route_class=_SafeValidationRoute,
)


def runpod_control_plane_service(request: Request) -> RunPodControlPlaneService:
    """Resolve an injected service or construct one over the app audit pool."""
    injected = getattr(request.app.state, "runpod_control_plane_service", None)
    if injected is not None:
        if not isinstance(injected, RunPodControlPlaneService):
            raise RuntimeError("app.state.runpod_control_plane_service has an invalid type")
        return injected
    return RunPodControlPlaneService(
        audit_pool=getattr(request.app.state, "pool", None),
        actor="rest:admin",
    )


Service = Annotated[RunPodControlPlaneService, Depends(runpod_control_plane_service)]


async def _result[Result](awaitable: Awaitable[Result]) -> Result:
    try:
        return await awaitable
    except RunPodControlPlaneError as exc:
        raise ApiErrorResponse(_status_for_error(exc), exc.to_dict()) from exc


def _status_for_error(exc: RunPodControlPlaneError) -> int:
    if exc.code == "resource_not_found":
        return 404
    if exc.code in {
        "idempotency_conflict",
        "mutation_in_progress",
        "mutation_outcome_ambiguous",
        "resource_name_conflict",
        "volume_grow_only",
    }:
        return 409
    if exc.code in {
        "credential_reference_unset",
        "invalid_request",
        "invalid_resource_id",
        "pre_spend_payload_rejected",
    }:
        return 422
    if exc.code == "provider_timeout":
        return 504
    if exc.code in {"audit_unavailable", "audit_write_failed"}:
        return 503
    return 502


def _path_matches(path_id: str, body_id: str) -> None:
    if path_id != body_id:
        raise ApiErrorResponse(
            422,
            {
                "error": "resource_id_mismatch",
                "detail": "path resource id must match body resource_id",
            },
        )


@router.get("/pods", response_model=list[PodResource])
async def list_pods(service: Service) -> list[PodResource]:
    return await _result(service.list_pods())


@router.get("/pods/{resource_id}", response_model=PodResource)
async def get_pod(resource_id: str, service: Service) -> PodResource:
    return await _result(service.get_pod(resource_id))


@router.post("/pods", response_model=MutationResult)
async def create_pod(body: PodCreateRequest, service: Service) -> MutationResult:
    return await _result(service.create_pod(body))


@router.patch("/pods/{resource_id}", response_model=MutationResult)
async def update_pod(resource_id: str, body: PodUpdateRequest, service: Service) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.update_pod(body))


@router.post("/pods/{resource_id}/action", response_model=MutationResult)
async def action_pod(resource_id: str, body: PodActionRequest, service: Service) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.action_pod(body))


@router.delete("/pods/{resource_id}", response_model=MutationResult)
async def terminate_pod(
    resource_id: str,
    body: Annotated[IdentifiedMutationRequest, Body()],
    service: Service,
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.terminate_pod(body))


@router.get("/endpoints", response_model=list[EndpointResource])
async def list_endpoints(service: Service) -> list[EndpointResource]:
    return await _result(service.list_endpoints())


@router.get("/endpoints/{resource_id}", response_model=EndpointResource)
async def get_endpoint(resource_id: str, service: Service) -> EndpointResource:
    return await _result(service.get_endpoint(resource_id))


@router.post("/endpoints", response_model=MutationResult)
async def create_endpoint(body: EndpointCreateRequest, service: Service) -> MutationResult:
    return await _result(service.create_endpoint(body))


@router.patch("/endpoints/{resource_id}", response_model=MutationResult)
async def update_endpoint(
    resource_id: str, body: EndpointUpdateRequest, service: Service
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.update_endpoint(body))


@router.delete("/endpoints/{resource_id}", response_model=MutationResult)
async def delete_endpoint(
    resource_id: str,
    body: Annotated[IdentifiedMutationRequest, Body()],
    service: Service,
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.delete_endpoint(body))


@router.get("/templates", response_model=list[TemplateResource])
async def list_templates(service: Service) -> list[TemplateResource]:
    return await _result(service.list_templates())


@router.get("/templates/{resource_id}", response_model=TemplateResource)
async def get_template(resource_id: str, service: Service) -> TemplateResource:
    return await _result(service.get_template(resource_id))


@router.post("/templates", response_model=MutationResult)
async def create_template(body: TemplateCreateRequest, service: Service) -> MutationResult:
    return await _result(service.create_template(body))


@router.patch("/templates/{resource_id}", response_model=MutationResult)
async def update_template(
    resource_id: str, body: TemplateUpdateRequest, service: Service
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.update_template(body))


@router.delete("/templates/{resource_id}", response_model=MutationResult)
async def delete_template_route(
    resource_id: str,
    body: Annotated[IdentifiedMutationRequest, Body()],
    service: Service,
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.delete_template(body))


@router.get("/volumes", response_model=list[VolumeResource])
async def list_volumes(service: Service) -> list[VolumeResource]:
    return await _result(service.list_volumes())


@router.get("/volumes/{resource_id}", response_model=VolumeResource)
async def get_volume(resource_id: str, service: Service) -> VolumeResource:
    return await _result(service.get_volume(resource_id))


@router.post("/volumes", response_model=MutationResult)
async def create_volume(body: VolumeCreateRequest, service: Service) -> MutationResult:
    return await _result(service.create_volume(body))


@router.patch("/volumes/{resource_id}", response_model=MutationResult)
async def grow_volume(
    resource_id: str, body: VolumeGrowRequest, service: Service
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.grow_volume(body))


@router.delete("/volumes/{resource_id}", response_model=MutationResult)
async def delete_volume(
    resource_id: str,
    body: Annotated[IdentifiedMutationRequest, Body()],
    service: Service,
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.delete_volume(body))


@router.get("/registry-auths", response_model=list[RegistryAuthResource])
async def list_registry_auths(service: Service) -> list[RegistryAuthResource]:
    return await _result(service.list_registry_auths())


@router.get("/registry-auths/{resource_id}", response_model=RegistryAuthResource)
async def get_registry_auth(resource_id: str, service: Service) -> RegistryAuthResource:
    return await _result(service.get_registry_auth(resource_id))


@router.post("/registry-auths", response_model=MutationResult)
async def create_registry_auth(body: RegistryAuthCreateRequest, service: Service) -> MutationResult:
    return await _result(service.create_registry_auth(body))


@router.post("/registry-auths/{resource_id}/replace", response_model=MutationResult)
async def replace_registry_auth(
    resource_id: str,
    body: RegistryAuthReplaceRequest,
    service: Service,
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.replace_registry_auth(body))


@router.delete("/registry-auths/{resource_id}", response_model=MutationResult)
async def delete_registry_auth(
    resource_id: str,
    body: Annotated[IdentifiedMutationRequest, Body()],
    service: Service,
) -> MutationResult:
    _path_matches(resource_id, body.resource_id)
    return await _result(service.delete_registry_auth(body))


@router.get("/hub/templates/search", response_model=list[HubTemplateResource])
async def search_hub_templates(
    service: Service,
    query: Annotated[str, Query(min_length=1, max_length=256)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[HubTemplateResource]:
    return await _result(service.search_hub_templates(query, limit=limit))


@router.get("/hub/templates", response_model=list[HubTemplateResource])
async def list_hub_templates_route(
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[HubTemplateResource]:
    return await _result(service.list_hub_templates(limit=limit, offset=offset))


@router.get("/hub/templates/{resource_id}", response_model=HubTemplateResource)
async def get_hub_template_route(resource_id: str, service: Service) -> HubTemplateResource:
    return await _result(service.get_hub_template(resource_id))


__all__ = ["router", "runpod_control_plane_service"]
