"""RunPod volume mount path constants and network volume CRUD + S3 client.

L10: RunPod Pods mount network volumes at ``/workspace`` while Serverless
workers use ``/runpod-volume``. Keep the difference behind provider-type
constants so capability and workload callers do not carry mount paths.
"""

from __future__ import annotations

import asyncio
import builtins
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

import httpx
from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from pitwall.core.enums import ProviderType
from pitwall.runpod_client.pods import RunPodError, RunPodRestError
from pitwall.runpod_client.retry import rest_retry_policy, send_with_retry
from pitwall.runpod_credentials import DEFAULT_RUNPOD_REST_URL, resolve_runpod_api_key
from pitwall.security.redaction import redact_text

log = logging.getLogger("pitwall.runpod_client.mounts")

POD_VOLUME_MOUNT_PATH = "/workspace"
SERVERLESS_VOLUME_MOUNT_PATH = "/runpod-volume"
_S3_DATA_CENTER_ID_RE = re.compile(r"(?!(?:\d+)$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)")

POD_MOUNT_PATH = POD_VOLUME_MOUNT_PATH
SERVERLESS_MOUNT_PATH = SERVERLESS_VOLUME_MOUNT_PATH

POD_PROVIDER_TYPES = frozenset({ProviderType.POD_LEASE})
SERVERLESS_PROVIDER_TYPES = frozenset(
    {
        ProviderType.SERVERLESS_QUEUE,
        ProviderType.SERVERLESS_LB,
        ProviderType.PUBLIC_ENDPOINT,
    }
)

PROVIDER_TYPE_VOLUME_MOUNT_PATHS: Mapping[ProviderType, str] = MappingProxyType(
    {
        ProviderType.POD_LEASE: POD_VOLUME_MOUNT_PATH,
        ProviderType.SERVERLESS_QUEUE: SERVERLESS_VOLUME_MOUNT_PATH,
        ProviderType.SERVERLESS_LB: SERVERLESS_VOLUME_MOUNT_PATH,
        ProviderType.PUBLIC_ENDPOINT: SERVERLESS_VOLUME_MOUNT_PATH,
    }
)
PROVIDER_TYPE_MOUNT_PATHS = PROVIDER_TYPE_VOLUME_MOUNT_PATHS
# Provider types that run on RunPod and therefore have a volume mount path.
# openai_gateway providers are plain HTTP upstreams with no RunPod volume.
RUNPOD_PROVIDER_TYPES = frozenset(PROVIDER_TYPE_VOLUME_MOUNT_PATHS)


def provider_type_volume_mount_path(provider_type: ProviderType | str) -> str:
    """Return the canonical RunPod volume mount path for a provider type."""

    try:
        resolved_provider_type = ProviderType(provider_type)
    except ValueError as exc:
        raise ValueError(f"unknown provider_type {provider_type!r}") from exc
    if resolved_provider_type not in PROVIDER_TYPE_VOLUME_MOUNT_PATHS:
        raise ValueError(
            f"provider_type {resolved_provider_type.value!r} has no RunPod volume mount"
        )
    return PROVIDER_TYPE_VOLUME_MOUNT_PATHS[resolved_provider_type]


mount_path_for_provider_type = provider_type_volume_mount_path


def _s3_data_center_id(dc: str) -> str:
    if not _S3_DATA_CENTER_ID_RE.fullmatch(dc):
        raise RunPodError(f"invalid RunPod data center ID for S3 endpoint: {dc!r}")
    return dc.lower()


class NetworkVolume(BaseModel):
    """RunPod network volume REST representation."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str
    size: int
    data_center_id: str = Field(
        validation_alias=AliasChoices("dataCenter", "dataCenterId"),
        serialization_alias="dataCenter",
    )
    type: str | None = None


class V2CreateNetworkVolumeRequest(BaseModel):
    """Strict REST v2 network-volume create body."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    size: int = Field(ge=10, le=4096)
    data_center: str = Field(alias="dataCenter", min_length=1)


class V2UpdateNetworkVolumeRequest(BaseModel):
    """Strict REST v2 network-volume update body used by Pitwall."""

    model_config = ConfigDict(extra="forbid")

    size: int = Field(ge=10, le=4096)


class S3Object(BaseModel):
    """S3 object metadata inside a RunPod network volume."""

    model_config = ConfigDict(populate_by_name=True)

    key: str
    size: int
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class S3ObjectPage:
    """A deliberately bounded S3 listing page.

    ``NetworkVolumeClient.list_objects`` remains the compatibility method for
    existing callers. New operator-facing transfers must use this page API so
    a listing cannot grow without bound in process memory.
    """

    objects: tuple[S3Object, ...]
    truncated: bool


class S3ObjectPreconditionFailed(RunPodError):
    """An S3 conditional object write was refused without changing the object."""


_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(?:\d+|\*)")


def _honors_range_offset(response: Mapping[str, Any], offset: int) -> bool:
    """True for a ``206`` whose ``Content-Range`` starts at ``offset``."""
    metadata = response.get("ResponseMetadata")
    status = metadata.get("HTTPStatusCode") if isinstance(metadata, Mapping) else None
    content_range = response.get("ContentRange")
    if status != 206 or not isinstance(content_range, str):
        return False
    match = _CONTENT_RANGE.fullmatch(content_range.strip())
    return match is not None and int(match.group(1)) == offset


class S3ObjectNotFound(RunPodError):
    """The requested S3 object key does not exist."""


class NetworkVolumeClient:
    """Async client for RunPod network volume REST CRUD + S3 file access.

    REST operations target ``https://api.runpod.io/v2`` (configurable via
    ``rest_base_url``). S3 operations target the datacenter-specific endpoint
    ``https://s3api-<dc>.runpod.io`` using per-volume bucket semantics
    (bucket = network volume ID).

    Credentials:
        * REST: ``RUNPOD_API_KEY`` env var or ``api_key`` constructor arg.
        * S3: ``RUNPOD_S3_ACCESS_KEY`` / ``RUNPOD_S3_SECRET_KEY`` env vars,
          falling back to ``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY``,
          or explicit ``s3_access_key`` / ``s3_secret_key`` args.
    """

    #: RunPod answers a delete of a missing volume with 500, not 404.
    _ALREADY_GONE_MARKERS = ("nonexistent network volume", "not found")

    def __init__(
        self,
        *,
        api_key: str | None = None,
        rest_base_url: str = DEFAULT_RUNPOD_REST_URL,
        s3_access_key: str | None = None,
        s3_secret_key: str | None = None,
        timeout_s: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve_env_credentials: bool = True,
    ) -> None:
        self._api_key = api_key or (
            resolve_runpod_api_key(os.environ)[0] or "" if resolve_env_credentials else ""
        )
        self._rest_base_url = rest_base_url.rstrip("/")
        self._s3_access_key = s3_access_key or (
            os.environ.get("RUNPOD_S3_ACCESS_KEY", "") or os.environ.get("AWS_ACCESS_KEY_ID", "")
            if resolve_env_credentials
            else ""
        )
        self._s3_secret_key = s3_secret_key or (
            os.environ.get("RUNPOD_S3_SECRET_KEY", "")
            or os.environ.get("AWS_SECRET_ACCESS_KEY", "")
            if resolve_env_credentials
            else ""
        )
        self._timeout_s = timeout_s
        self._transport = transport
        self._rest_client = httpx.AsyncClient(
            base_url=self._rest_base_url,
            timeout=timeout_s,
            headers={"Authorization": f"Bearer {self._api_key}"},
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._rest_client.aclose()

    async def _rest_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        response = await send_with_retry(
            lambda: self._rest_client.request(method, path, json=json_body),
            rest_retry_policy(method),
        )
        if response.status_code == 204:
            return {}
        if response.status_code >= 400:
            raise RunPodRestError(
                method,
                path,
                response.status_code,
                redact_text(response.text[:4096], secrets=(self._api_key,)),
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as exc:
            raise RunPodError(f"{method} {path} returned a non-JSON response") from exc

    async def create(self, name: str, size_gb: int, dc: str) -> NetworkVolume:
        """Create a new network volume and return its representation."""
        payload = V2CreateNetworkVolumeRequest(
            name=name,
            size=size_gb,
            dataCenter=dc,
        ).model_dump(by_alias=True)
        data = await self._rest_request("POST", "/network-volumes", json_body=payload)
        if not isinstance(data, dict):
            raise RunPodError(
                f"create network volume returned unexpected type: {type(data).__name__}"
            )
        return NetworkVolume.model_validate(data)

    async def get(self, volume_id: str) -> NetworkVolume:
        """Fetch a single network volume by ID."""
        data = await self._rest_request("GET", f"/network-volumes/{volume_id}")
        if not isinstance(data, dict):
            raise RunPodError(f"get network volume returned unexpected type: {type(data).__name__}")
        return NetworkVolume.model_validate(data)

    async def list(self) -> builtins.list[NetworkVolume]:
        """Return all network volumes owned by the account."""
        data = await self._rest_request("GET", "/network-volumes")
        if not isinstance(data, dict) or not isinstance(data.get("networkVolumes"), list):
            raise RunPodError(
                "list network volumes returned an invalid REST v2 networkVolumes envelope"
            )
        return [NetworkVolume.model_validate(item) for item in data["networkVolumes"]]

    async def update(self, volume_id: str, size_gb: int) -> NetworkVolume:
        """Grow a network volume. Shrinking is refused: it destroys data.

        The API accepts a smaller size and applies it, so this invariant has to be
        enforced here — the docstring promised it and nothing checked.
        """
        current = await self.get(volume_id)
        if size_gb <= current.size:
            raise ValueError(
                f"refusing to resize volume {volume_id!r} from {current.size} GB to "
                f"{size_gb} GB: a smaller or equal size destroys data"
            )
        data = await self._rest_request(
            "PATCH",
            f"/network-volumes/{volume_id}",
            json_body=V2UpdateNetworkVolumeRequest(size=size_gb).model_dump(),
        )
        if not isinstance(data, dict):
            raise RunPodError(
                f"update network volume returned unexpected type: {type(data).__name__}"
            )
        return NetworkVolume.model_validate(data)

    async def delete(self, volume_id: str) -> None:
        """Delete a network volume. Idempotent: silent when it is already gone."""
        try:
            await self._rest_request("DELETE", f"/network-volumes/{volume_id}")
        except RunPodRestError as exc:
            body = (exc.body or "").lower()
            already_gone = exc.status_code == 404 or any(
                marker in body for marker in self._ALREADY_GONE_MARKERS
            )
            if already_gone:
                log.info("network volume %s was already gone", volume_id)
                return
            raise

    def _s3_endpoint(self, dc: str) -> str:
        return f"https://s3api-{_s3_data_center_id(dc)}.runpod.io"

    def _s3_client(self, dc: str) -> Any:
        data_center_id = _s3_data_center_id(dc)
        try:
            import boto3
            from botocore.config import Config
        except ModuleNotFoundError as exc:
            raise RunPodError("boto3 is required for RunPod network volume S3 access") from exc

        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            region_name=data_center_id,
        )
        return boto3.client(
            "s3",
            endpoint_url=f"https://s3api-{data_center_id}.runpod.io",
            aws_access_key_id=self._s3_access_key,
            aws_secret_access_key=self._s3_secret_key,
            region_name=data_center_id,
            config=config,
        )

    async def list_objects(
        self,
        volume_id: str,
        dc: str,
        *,
        prefix: str = "",
    ) -> builtins.list[S3Object]:
        """List objects inside a network volume via S3 ListObjectsV2."""

        def _list() -> builtins.list[S3Object]:
            client = self._s3_client(dc)
            paginator = client.get_paginator("list_objects_v2")
            objects: builtins.list[S3Object] = []
            for page in paginator.paginate(Bucket=volume_id, Prefix=prefix):
                for obj in page.get("Contents", []) or []:
                    objects.append(
                        S3Object(
                            key=obj["Key"],
                            size=obj["Size"],
                            last_modified=obj.get("LastModified", ""),
                        )
                    )
            return objects

        return await asyncio.to_thread(_list)

    async def list_objects_page(
        self,
        volume_id: str,
        dc: str,
        *,
        prefix: str = "",
        max_items: int,
    ) -> S3ObjectPage:
        """Return at most ``max_items`` object records and a truncation flag.

        The call makes exactly one ListObjectsV2 request.  It intentionally
        does not paginate: callers that need a larger inventory must make a
        new explicitly bounded operator request instead of accumulating an
        arbitrary bucket listing.
        """
        if isinstance(max_items, bool) or not isinstance(max_items, int):
            raise ValueError("max_items must be an integer")
        if not 1 <= max_items <= 999:
            raise ValueError("max_items must be between 1 and 999")

        def _list_page() -> S3ObjectPage:
            client = self._s3_client(dc)
            response = client.list_objects_v2(
                Bucket=volume_id,
                Prefix=prefix,
                MaxKeys=max_items + 1,
            )
            contents = response.get("Contents", []) or []
            if not isinstance(contents, list):
                raise RunPodError("S3 ListObjectsV2 returned an invalid Contents envelope")
            objects: list[S3Object] = []
            for obj in contents[:max_items]:
                if not isinstance(obj, dict):
                    raise RunPodError("S3 ListObjectsV2 returned an invalid object record")
                last_modified = obj.get("LastModified")
                objects.append(
                    S3Object(
                        key=obj["Key"],
                        size=obj["Size"],
                        last_modified=str(last_modified) if last_modified is not None else None,
                    )
                )
            return S3ObjectPage(
                objects=tuple(objects),
                truncated=bool(response.get("IsTruncated")) or len(contents) > max_items,
            )

        return await asyncio.to_thread(_list_page)

    async def put_object(
        self,
        volume_id: str,
        dc: str,
        key: str,
        body: bytes,
        *,
        create_only: bool = False,
    ) -> None:
        """Upload an object to a network volume via S3 PutObject."""

        def _put() -> None:
            client = self._s3_client(dc)
            request: dict[str, object] = {
                "Bucket": volume_id,
                "Key": key,
                "Body": body,
            }
            if create_only:
                request["IfNoneMatch"] = "*"
            try:
                client.put_object(**request)
            except Exception as exc:  # reason: optional botocore errors need lazy type inspection
                # botocore is an optional storage dependency, so keep its import
                # at the already-lazy S3 boundary.
                from botocore.exceptions import ClientError

                if isinstance(exc, ClientError):
                    metadata = exc.response.get("ResponseMetadata", {})
                    status = metadata.get("HTTPStatusCode")
                    code = exc.response.get("Error", {}).get("Code")
                    if status == 412 or code in {"PreconditionFailed", "412"}:
                        raise S3ObjectPreconditionFailed(
                            "S3 conditional PutObject precondition failed"
                        ) from exc
                raise

        await asyncio.to_thread(_put)

    async def get_object(
        self,
        volume_id: str,
        dc: str,
        key: str,
    ) -> bytes:
        """Download an object from a network volume via S3 GetObject."""

        def _get() -> bytes:
            client = self._s3_client(dc)
            response = client.get_object(Bucket=volume_id, Key=key)
            return cast(bytes, response["Body"].read())

        return await asyncio.to_thread(_get)

    async def get_object_range(
        self,
        volume_id: str,
        dc: str,
        key: str,
        *,
        offset: int,
        max_bytes: int,
    ) -> bytes:
        """Read one strictly bounded object range.

        S3-compatible providers may ignore a Range header and answer ``200`` with the
        whole object (RFC 9110 section 14.2).  A nonzero offset therefore requires
        ``206 Partial Content`` whose ``Content-Range`` starts at that offset
        (section 15.3.7); anything else is rejected.  Reading one byte beyond the
        requested range detects an over-long body without buffering a complete
        object, and rejects it before the result reaches a transport adapter.
        """
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")

        def _get_range() -> bytes:
            client = self._s3_client(dc)
            try:
                response = client.get_object(
                    Bucket=volume_id,
                    Key=key,
                    Range=f"bytes={offset}-{offset + max_bytes - 1}",
                )
            except Exception as exc:  # reason: optional botocore errors need lazy type inspection
                from botocore.exceptions import ClientError

                if isinstance(exc, ClientError):
                    metadata = exc.response.get("ResponseMetadata", {})
                    status = metadata.get("HTTPStatusCode")
                    code = exc.response.get("Error", {}).get("Code")
                    if status == 416 or code in {"InvalidRange", "416"}:
                        return b""
                    if status == 404 or code in {"NoSuchKey", "404"}:
                        raise S3ObjectNotFound("S3 object does not exist") from exc
                raise
            body = response.get("Body")
            if body is None or not hasattr(body, "read"):
                raise RunPodError("S3 GetObject returned no readable body")
            try:
                if offset > 0 and not _honors_range_offset(response, offset):
                    raise RunPodError("S3 GetObject did not honor the requested range")
                data = body.read(max_bytes + 1)
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
            if not isinstance(data, bytes):
                raise RunPodError("S3 GetObject returned a non-bytes body")
            if len(data) > max_bytes:
                raise RunPodError("S3 GetObject exceeded the requested bounded range")
            return data

        return await asyncio.to_thread(_get_range)

    async def delete_object(
        self,
        volume_id: str,
        dc: str,
        key: str,
    ) -> None:
        """Remove an object from a network volume via S3 DeleteObject."""

        def _delete() -> None:
            client = self._s3_client(dc)
            client.delete_object(Bucket=volume_id, Key=key)

        await asyncio.to_thread(_delete)


__all__ = [
    "POD_MOUNT_PATH",
    "POD_PROVIDER_TYPES",
    "POD_VOLUME_MOUNT_PATH",
    "PROVIDER_TYPE_MOUNT_PATHS",
    "PROVIDER_TYPE_VOLUME_MOUNT_PATHS",
    "SERVERLESS_MOUNT_PATH",
    "SERVERLESS_PROVIDER_TYPES",
    "SERVERLESS_VOLUME_MOUNT_PATH",
    "S3Object",
    "S3ObjectNotFound",
    "S3ObjectPage",
    "S3ObjectPreconditionFailed",
    "NetworkVolume",
    "NetworkVolumeClient",
    "mount_path_for_provider_type",
    "provider_type_volume_mount_path",
]
