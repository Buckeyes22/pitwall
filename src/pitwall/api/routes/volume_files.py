"""Feature-local REST adapter for the bounded RP-04 volume-file service.

Registration is intentionally deferred to the serialized API integrator.  The
two mutation routes use the existing ``/v1/admin`` namespace so app middleware
applies the established administrator scope and secret check on registration.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.responses import Response

from pitwall.api.volume_file_schemas import (
    DataCenterId,
    DeleteObjectRequest,
    UploadObjectRequest,
    VolumeFileResultResponse,
    VolumeId,
)
from pitwall.runpod_files import (
    VolumeFileError,
    VolumeFileResult,
    VolumeFileService,
    build_configured_volume_file_service,
)


class _SafeVolumeFileValidationRoute(APIRoute):
    """Never reflect an invalid upload body through FastAPI's error input."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"error": "invalid_volume_file_request"},
                )

        return safe_handler


volume_file_router = APIRouter(route_class=_SafeVolumeFileValidationRoute)


def install_volume_file_error_handler(app: FastAPI) -> None:
    """Serialize VolumeFileError raised outside a route body (dependencies)."""

    async def _handler(_request: Request, exc: VolumeFileError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())

    app.add_exception_handler(VolumeFileError, _handler)  # type: ignore[arg-type]  # reason: Starlette types handlers as taking Exception; this handler takes the narrower VolumeFileError


async def _volume_file_service(request: Request) -> AsyncIterator[VolumeFileService]:
    service = getattr(request.app.state, "volume_file_service", None)
    if service is not None:
        if not isinstance(service, VolumeFileService):
            raise RuntimeError("app.state.volume_file_service has an invalid type")
        yield service
        return
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("database pool is required for volume-file mutation audit")
    configured = build_configured_volume_file_service(
        audit_pool=pool,
        audit_actor="rest:admin",
    )
    try:
        yield configured
    finally:
        await configured.aclose()


async def _result(call: Awaitable[VolumeFileResult]) -> dict[str, object] | JSONResponse:
    try:
        return (await call).to_dict()
    except VolumeFileError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())


@volume_file_router.get(
    "/v1/volumes/{volume_id}/objects",
    response_model=VolumeFileResultResponse,
)
async def list_volume_objects(
    volume_id: VolumeId,
    data_center_id: DataCenterId,
    prefix: Annotated[str, Query(max_length=1024)] = "",
    max_items: Annotated[int, Query(ge=1, le=500)] = 200,
    service: VolumeFileService = Depends(_volume_file_service),
) -> dict[str, object] | JSONResponse:
    """List one bounded object page through the shared service."""
    return await _result(
        service.list_objects(
            volume_id=volume_id,
            data_center_id=data_center_id,
            prefix=prefix,
            max_items=max_items,
        )
    )


@volume_file_router.get(
    "/v1/volumes/{volume_id}/objects/{object_key:path}",
    response_model=VolumeFileResultResponse,
)
async def read_volume_object_chunk(
    volume_id: VolumeId,
    object_key: str,
    data_center_id: DataCenterId,
    offset: Annotated[int, Query(ge=0)] = 0,
    max_bytes: Annotated[int, Query(ge=1, le=128 * 1024)] = 128 * 1024,
    service: VolumeFileService = Depends(_volume_file_service),
) -> dict[str, object] | JSONResponse:
    """Read one base64-framed bounded object chunk; no unbounded REST stream."""
    return await _result(
        service.read_object_chunk(
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=object_key,
            offset=offset,
            max_bytes=max_bytes,
        )
    )


@volume_file_router.post(
    "/v1/admin/volumes/{volume_id}/objects",
    response_model=VolumeFileResultResponse,
    responses={409: {"description": "overwrite confirmation is required"}},
)
async def upload_volume_object(
    volume_id: VolumeId,
    body: UploadObjectRequest,
    service: VolumeFileService = Depends(_volume_file_service),
) -> dict[str, object] | JSONResponse:
    """Write one bounded base64 object with explicit intent and idempotency key."""
    try:
        decoded = body.body_bytes()
    except ValueError:
        return JSONResponse(status_code=422, content={"error": "invalid_volume_file_request"})
    return await _result(
        service.upload_bytes(
            volume_id=volume_id,
            data_center_id=body.data_center_id,
            object_key=body.object_key,
            body=decoded,
            overwrite=body.confirm_overwrite,
            expected_sha256=body.expected_sha256,
            dry_run=body.dry_run,
            idempotency_key=body.idempotency_key,
        )
    )


@volume_file_router.delete(
    "/v1/admin/volumes/{volume_id}/objects/{object_key:path}",
    response_model=VolumeFileResultResponse,
    responses={409: {"description": "delete confirmation is required"}},
)
async def delete_volume_object(
    volume_id: VolumeId,
    object_key: str,
    body: DeleteObjectRequest,
    service: VolumeFileService = Depends(_volume_file_service),
) -> dict[str, object] | JSONResponse:
    """Delete one object after explicit destructive intent and confirmation."""
    return await _result(
        service.delete_object(
            volume_id=volume_id,
            data_center_id=body.data_center_id,
            object_key=object_key,
            confirm_delete=body.confirm_delete,
            dry_run=body.dry_run,
            idempotency_key=body.idempotency_key,
        )
    )


@volume_file_router.get("/v1/pods/{pod_id}/logs", response_model=VolumeFileResultResponse)
async def read_pod_logs(
    pod_id: str,
    max_lines: Annotated[int, Query(ge=1, le=200)] = 100,
    max_bytes: Annotated[int, Query(ge=1, le=128 * 1024)] = 64 * 1024,
    service: VolumeFileService = Depends(_volume_file_service),
) -> dict[str, object] | JSONResponse:
    """Read ordered, redacted, bounded pod diagnostics through the same service."""
    return await _result(
        service.read_pod_logs(pod_id=pod_id, max_lines=max_lines, max_bytes=max_bytes)
    )


__all__ = [
    "delete_volume_object",
    "install_volume_file_error_handler",
    "list_volume_objects",
    "read_pod_logs",
    "read_volume_object_chunk",
    "upload_volume_object",
    "volume_file_router",
]
