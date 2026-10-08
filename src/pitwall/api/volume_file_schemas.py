"""Strict transport models for bounded RunPod volume-file operations."""

from __future__ import annotations

import base64
from typing import Annotated, Literal

from pydantic import Field

from pitwall.core.models import PitwallModel

_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_DATA_CENTER = r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$"
_SHA256 = r"^[0-9a-fA-F]{64}$"
_REST_TRANSFER_BYTES = 128 * 1024
_REST_TRANSFER_BASE64_CHARS = ((_REST_TRANSFER_BYTES + 2) // 3) * 4

VolumeId = Annotated[str, Field(pattern=_SAFE_ID)]
DataCenterId = Annotated[str, Field(pattern=_DATA_CENTER)]
ObjectKey = Annotated[str, Field(min_length=1, max_length=1024)]
IdempotencyKey = Annotated[str, Field(pattern=_SAFE_ID)]


class UploadObjectRequest(PitwallModel):
    """One bounded server-side object write; values never include credentials."""

    data_center_id: DataCenterId
    object_key: ObjectKey
    content_base64: Annotated[str, Field(min_length=1, max_length=_REST_TRANSFER_BASE64_CHARS)]
    expected_sha256: Annotated[str, Field(pattern=_SHA256)] | None = None
    confirm_overwrite: bool = False
    dry_run: bool = False
    intent: Literal["upload"]
    idempotency_key: IdempotencyKey

    def body_bytes(self) -> bytes:
        """Decode a syntactically valid bounded base64 request body."""
        try:
            return base64.b64decode(self.content_base64, validate=True)
        except ValueError as exc:
            raise ValueError("content_base64 must be valid base64") from exc


class DeleteObjectRequest(PitwallModel):
    """Explicit destructive intent for a server-side object deletion."""

    data_center_id: DataCenterId
    confirm_delete: Literal[True]
    dry_run: bool = False
    intent: Literal["delete"]
    idempotency_key: IdempotencyKey


class VolumeObjectResponse(PitwallModel):
    key: str
    size: Annotated[int, Field(ge=0)]
    last_modified: str | None = None


class PodLogLineResponse(PitwallModel):
    sequence: Annotated[int, Field(ge=1)]
    timestamp: str | None = None
    text: str


class VolumeFileProgressResponse(PitwallModel):
    sequence: Annotated[int, Field(ge=1)]
    operation: Literal["list", "upload", "download", "delete", "logs"]
    phase: str
    bytes_completed: Annotated[int, Field(ge=0)]
    total_bytes: Annotated[int, Field(ge=0)] | None = None


class VolumeFileResultResponse(PitwallModel):
    operation: Literal["list", "upload", "download", "delete", "logs"]
    status: Literal["completed", "dry_run", "cancelled"]
    volume_id: str | None = None
    data_center_id: str | None = None
    object_key: str | None = None
    pod_id: str | None = None
    bytes_transferred: Annotated[int, Field(ge=0)]
    checksum_sha256: str | None = None
    objects: list[VolumeObjectResponse]
    content_base64: str | None = None
    logs: list[PodLogLineResponse]
    truncated: bool
    progress: list[VolumeFileProgressResponse]
    provider: Literal["runpod"]
    target: str | None = None
    effect: str | None = None
    estimated_ceiling: str | None = None
    irreversible: bool
    consequences: str | None = None
    replayed: bool


__all__ = [
    "DataCenterId",
    "DeleteObjectRequest",
    "IdempotencyKey",
    "ObjectKey",
    "PodLogLineResponse",
    "UploadObjectRequest",
    "VolumeFileProgressResponse",
    "VolumeFileResultResponse",
    "VolumeId",
]
