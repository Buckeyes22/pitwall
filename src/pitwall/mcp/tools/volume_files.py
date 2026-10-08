"""Thin MCP adapters for the shared bounded RunPod volume-file service."""

from __future__ import annotations

import base64
import inspect
from collections.abc import Awaitable, Callable
from typing import Literal

from pitwall.db import get_pool
from pitwall.mcp.error_adapter import adapt_error
from pitwall.runpod_files import (
    VolumeFileError,
    VolumeFileResult,
    VolumeFileService,
    VolumeFileValidationError,
    build_configured_volume_file_service,
)

_MCP_TRANSFER_BYTES = 128 * 1024
_MCP_TRANSFER_BASE64_CHARS = ((_MCP_TRANSFER_BYTES + 2) // 3) * 4


async def get_volume_file_service() -> VolumeFileService:
    """Build a credential-bound service at the provider boundary, not tool input."""
    return build_configured_volume_file_service(
        audit_pool=await get_pool(),
        audit_actor="mcp:admin",
    )


async def pitwall_volume_list_objects(
    volume_id: str,
    data_center_id: str,
    prefix: str = "",
    max_items: int = 200,
) -> dict[str, object]:
    """List one bounded page of network-volume objects."""
    return await _call(
        lambda service: service.list_objects(
            volume_id=volume_id,
            data_center_id=data_center_id,
            prefix=prefix,
            max_items=max_items,
        )
    )


async def pitwall_volume_read_chunk(
    volume_id: str,
    data_center_id: str,
    object_key: str,
    offset: int = 0,
    max_bytes: int = _MCP_TRANSFER_BYTES,
) -> dict[str, object]:
    """Read one base64-framed bounded object chunk; MCP never streams a bucket."""
    return await _call(
        lambda service: service.read_object_chunk(
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=object_key,
            offset=offset,
            max_bytes=max_bytes,
        )
    )


async def pitwall_volume_upload_object(
    volume_id: str,
    data_center_id: str,
    object_key: str,
    content_base64: str,
    *,
    intent: Literal["upload"],
    idempotency_key: str,
    confirm_overwrite: bool = False,
    expected_sha256: str | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Upload one bounded base64 body with explicit caller intent."""
    if intent != "upload":
        raise adapt_error(VolumeFileValidationError("intent must be upload"))
    try:
        body = _decode_mcp_body(content_base64)
    except VolumeFileError as exc:
        raise adapt_error(exc) from exc
    return await _call(
        lambda service: service.upload_bytes(
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=object_key,
            body=body,
            overwrite=confirm_overwrite,
            expected_sha256=expected_sha256,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
        )
    )


async def pitwall_volume_delete_object(
    volume_id: str,
    data_center_id: str,
    object_key: str,
    *,
    intent: Literal["delete"],
    idempotency_key: str,
    confirm_delete: bool = False,
    dry_run: bool = False,
) -> dict[str, object]:
    """Delete one object only with explicit destructive intent and confirmation."""
    if intent != "delete":
        raise adapt_error(VolumeFileValidationError("intent must be delete"))
    return await _call(
        lambda service: service.delete_object(
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=object_key,
            confirm_delete=confirm_delete,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
        )
    )


async def pitwall_pod_logs(
    pod_id: str,
    max_lines: int = 100,
    max_bytes: int = 64 * 1024,
) -> dict[str, object]:
    """Read ordered, redacted, bounded provider pod diagnostics."""
    return await _call(
        lambda service: service.read_pod_logs(
            pod_id=pod_id,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )
    )


async def _call(
    operation: Callable[[VolumeFileService], Awaitable[VolumeFileResult]],
) -> dict[str, object]:
    service: VolumeFileService | None = None
    try:
        resolved = get_volume_file_service()
        service = await resolved if inspect.isawaitable(resolved) else resolved
        return (await operation(service)).to_dict()
    except VolumeFileError as exc:
        raise adapt_error(exc) from exc
    finally:
        if service is not None:
            await service.aclose()


def _decode_mcp_body(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > _MCP_TRANSFER_BASE64_CHARS:
        raise VolumeFileValidationError("MCP upload body exceeds the bounded transfer frame")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as exc:
        raise VolumeFileValidationError("content_base64 must be valid base64") from exc


__all__ = [
    "get_volume_file_service",
    "pitwall_pod_logs",
    "pitwall_volume_delete_object",
    "pitwall_volume_list_objects",
    "pitwall_volume_read_chunk",
    "pitwall_volume_upload_object",
]
