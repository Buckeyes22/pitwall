"""Bounded operator service for RunPod network-volume files and pod logs.

This module is the only RP-04 business layer used by REST, MCP, CLI, and
Textual adapters.  It intentionally supports existing S3 object operations and
the already-used provider pod-log endpoint only; it is not a remote shell or a
general file-transfer framework.
"""

from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import logging
import os
import re
import secrets
import stat
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial, wraps
from pathlib import Path, PurePath
from typing import Any, Concatenate, Literal, Protocol, TypeVar, cast

import asyncpg

from pitwall.db.repository import insert_audit
from pitwall.runpod_client.mounts import (
    NetworkVolumeClient,
    S3ObjectNotFound,
    S3ObjectPage,
    S3ObjectPreconditionFailed,
)
from pitwall.runpod_client.pod_logs import BoundedPodLogClient, BoundedPodLogPayload
from pitwall.runpod_credentials import (
    DEFAULT_RUNPOD_REST_URL,
    RUNPOD_API_KEY_ENV,
    resolve_runpod_api_key,
)
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendInspectionLimits,
    PreSpendInspectionService,
    PreSpendPolicyMode,
)
from pitwall.security.redaction import redact_text

log = logging.getLogger("pitwall.runpod_files")

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DATA_CENTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
_ENV_REF_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_CHECKSUM_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_LIST_ITEMS = 500
_MAX_TRANSFER_BYTES = 8 * 1024 * 1024
_MAX_CHUNK_BYTES = 256 * 1024
_MAX_LOG_BYTES = 128 * 1024
_MAX_LOG_LINES = 200
_MAX_TIMEOUT_S = 60.0
_TEMP_PREFIX = ".pitwall-volume-"

Operation = Literal["list", "upload", "download", "delete", "logs"]
ResultStatus = Literal["completed", "dry_run", "cancelled"]
AuditAction = Literal["create", "update", "delete"]
AuditActor = Literal["rest:admin", "mcp:admin", "mcp", "system"]
MutationRecovery = Literal["conditional_create", "manual"]
T = TypeVar("T")
ProgressCallback = Callable[["VolumeFileProgress"], Awaitable[None] | None]


class VolumeFileError(RuntimeError):
    """Safe base error that transports can serialize without provider details."""

    error_code = "volume_file_error"
    status_code = 500

    def __str__(self) -> str:
        """Keep generic adapter messages free of provider/local-path detail."""
        return self.error_code

    def to_response_body(self) -> dict[str, object]:
        return {"error": self.error_code}


class VolumeFileValidationError(VolumeFileError):
    error_code = "invalid_volume_file_request"
    status_code = 422


class VolumeFileLimitExceeded(VolumeFileError):
    error_code = "volume_file_limit_exceeded"
    status_code = 413


class VolumeFileConfirmationRequired(VolumeFileError):
    error_code = "volume_file_confirmation_required"
    status_code = 409


class VolumeFileNotFound(VolumeFileError):
    error_code = "volume_file_not_found"
    status_code = 404


class VolumeFileChecksumMismatch(VolumeFileError):
    error_code = "volume_file_checksum_mismatch"
    status_code = 422


class VolumeFileProviderError(VolumeFileError):
    error_code = "volume_file_provider_error"
    status_code = 502


class VolumeFileTimeout(VolumeFileError):
    error_code = "volume_file_timeout"
    status_code = 504


class VolumeFileConfigurationError(VolumeFileError):
    error_code = "volume_file_not_configured"
    status_code = 503


class VolumeFileAuditUnavailable(VolumeFileError):
    """A mutation was stopped because no durable audit sink was configured."""

    error_code = "volume_file_audit_unavailable"
    status_code = 503


class VolumeFileAuditError(VolumeFileError):
    """A provider/local mutation completed but its durable audit write failed."""

    error_code = "volume_file_audit_failed_after_change"
    status_code = 503
    changed = True

    def to_response_body(self) -> dict[str, object]:
        return {"error": self.error_code, "changed": True}


class VolumeFileIdempotencyConflict(VolumeFileError):
    """An idempotency key was previously committed for a different request."""

    error_code = "volume_file_idempotency_conflict"
    status_code = 409


class VolumeFilePreconditionConflict(VolumeFileError):
    """A create-only provider write found an object at the selected key."""

    error_code = "volume_file_precondition_conflict"
    status_code = 409


class VolumeFileMutationAmbiguous(VolumeFileError):
    """A prior provider mutation may have landed and cannot be repeated safely."""

    error_code = "volume_file_mutation_outcome_ambiguous"
    status_code = 409


class VolumeFilePreSpendRejected(VolumeFileError):
    """A bounded request was rejected before provider or local mutation I/O."""

    error_code = "pre_spend_payload_rejected"
    status_code = 422


class _CancellationRequested(Exception):
    """Internal signal used to turn explicit cancellation into a stable result."""


@dataclass(frozen=True, slots=True)
class VolumeFileLimits:
    """Hard upper bounds shared by every RP-04 operation and adapter."""

    max_list_items: int = 200
    max_transfer_bytes: int = 4 * 1024 * 1024
    max_chunk_bytes: int = 128 * 1024
    max_log_bytes: int = 64 * 1024
    max_log_lines: int = 100
    timeout_s: float = 30.0

    def __post_init__(self) -> None:
        _bounded_int("max_list_items", self.max_list_items, 1, _MAX_LIST_ITEMS)
        _bounded_int("max_transfer_bytes", self.max_transfer_bytes, 1, _MAX_TRANSFER_BYTES)
        _bounded_int("max_chunk_bytes", self.max_chunk_bytes, 1, _MAX_CHUNK_BYTES)
        _bounded_int("max_log_bytes", self.max_log_bytes, 1, _MAX_LOG_BYTES)
        _bounded_int("max_log_lines", self.max_log_lines, 1, _MAX_LOG_LINES)
        if self.max_chunk_bytes > self.max_transfer_bytes:
            raise ValueError("max_chunk_bytes cannot exceed max_transfer_bytes")
        if isinstance(self.timeout_s, bool) or not isinstance(self.timeout_s, (int, float)):
            raise ValueError("timeout_s must be a number")
        if not 0 < self.timeout_s <= _MAX_TIMEOUT_S:
            raise ValueError(f"timeout_s must be between 0 and {_MAX_TIMEOUT_S:g}")

    def to_dict(self) -> dict[str, int | float]:
        return {
            "max_list_items": self.max_list_items,
            "max_transfer_bytes": self.max_transfer_bytes,
            "max_chunk_bytes": self.max_chunk_bytes,
            "max_log_bytes": self.max_log_bytes,
            "max_log_lines": self.max_log_lines,
            "timeout_s": self.timeout_s,
        }


_UPLOAD_INSPECTION_METADATA_BYTES = 65_536
_UPLOAD_INSPECTION_TIMEOUT_MS = 5_000


def _default_upload_inspection_service(limits: VolumeFileLimits) -> PreSpendInspectionService:
    """Size the default inspector so every transfer within limits can be scanned."""
    from pitwall.config import get_settings

    return PreSpendInspectionService(
        mode=PreSpendPolicyMode(get_settings().pitwall_pre_spend_mode),
        limits=PreSpendInspectionLimits(
            max_input_bytes=limits.max_transfer_bytes + _UPLOAD_INSPECTION_METADATA_BYTES,
            timeout_ms=_UPLOAD_INSPECTION_TIMEOUT_MS,
        ),
    )


@dataclass(frozen=True, slots=True)
class S3CredentialReferences:
    """Names of the S3-specific environment variables, never their values."""

    access_key_env: str = "RUNPOD_S3_ACCESS_KEY"
    secret_key_env: str = "RUNPOD_S3_SECRET_KEY"

    def __post_init__(self) -> None:
        if not _ENV_REF_RE.fullmatch(self.access_key_env):
            raise ValueError("access_key_env must be an uppercase environment-variable reference")
        if not _ENV_REF_RE.fullmatch(self.secret_key_env):
            raise ValueError("secret_key_env must be an uppercase environment-variable reference")
        if self.access_key_env == self.secret_key_env:
            raise ValueError("S3 access and secret credential references must be distinct")
        if {self.access_key_env, self.secret_key_env} & {RUNPOD_API_KEY_ENV}:
            raise ValueError("S3 credential references must not reuse the control-plane token")

    def to_dict(self) -> dict[str, str]:
        return {
            "access_key_env": self.access_key_env,
            "secret_key_env": self.secret_key_env,
        }


@dataclass(frozen=True, slots=True)
class VolumeObject:
    key: str
    size: int
    last_modified: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {"key": self.key, "size": self.size, "last_modified": self.last_modified}


@dataclass(frozen=True, slots=True)
class VolumeObjectPage:
    objects: tuple[VolumeObject, ...]
    truncated: bool


@dataclass(frozen=True, slots=True)
class PodLogLine:
    sequence: int
    text: str
    timestamp: str | None = None

    def to_dict(self) -> dict[str, int | str | None]:
        return {"sequence": self.sequence, "timestamp": self.timestamp, "text": self.text}


@dataclass(frozen=True, slots=True)
class VolumeFileProgress:
    sequence: int
    operation: Operation
    phase: str
    bytes_completed: int
    total_bytes: int | None

    def to_dict(self) -> dict[str, int | str | None]:
        return {
            "sequence": self.sequence,
            "operation": self.operation,
            "phase": self.phase,
            "bytes_completed": self.bytes_completed,
            "total_bytes": self.total_bytes,
        }


@dataclass(frozen=True, slots=True)
class VolumeFileResult:
    """One deterministic result model shared by all RP-04 surfaces."""

    operation: Operation
    status: ResultStatus
    volume_id: str | None = None
    data_center_id: str | None = None
    object_key: str | None = None
    pod_id: str | None = None
    bytes_transferred: int = 0
    checksum_sha256: str | None = None
    objects: tuple[VolumeObject, ...] = ()
    content_base64: str | None = None
    logs: tuple[PodLogLine, ...] = ()
    truncated: bool = False
    progress: tuple[VolumeFileProgress, ...] = ()
    provider: Literal["runpod"] = "runpod"
    target: str | None = None
    effect: str | None = None
    estimated_ceiling: str | None = None
    irreversible: bool = False
    consequences: str | None = None
    replayed: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation,
            "status": self.status,
            "volume_id": self.volume_id,
            "data_center_id": self.data_center_id,
            "object_key": self.object_key,
            "pod_id": self.pod_id,
            "bytes_transferred": self.bytes_transferred,
            "checksum_sha256": self.checksum_sha256,
            "objects": [entry.to_dict() for entry in self.objects],
            "content_base64": self.content_base64,
            "logs": [line.to_dict() for line in self.logs],
            "truncated": self.truncated,
            "progress": [event.to_dict() for event in self.progress],
            "provider": self.provider,
            "target": self.target,
            "effect": self.effect,
            "estimated_ceiling": self.estimated_ceiling,
            "irreversible": self.irreversible,
            "consequences": self.consequences,
            "replayed": self.replayed,
        }


@dataclass(frozen=True, slots=True)
class VolumeFileMutationAudit:
    """Credential-free durable record metadata for one bounded mutation."""

    operation: Literal["upload", "download", "delete"]
    action: AuditAction
    volume_id: str
    data_center_id: str
    object_key: str
    request_hash: str
    recovery: MutationRecovery
    idempotency_key: str | None = None


class VolumeFileMutationJournal(Protocol):
    """Durable mutation audit used by the shared service."""

    async def run_idempotent(
        self,
        audit: VolumeFileMutationAudit,
        validate: Callable[[], Awaitable[None]],
        apply: Callable[[bool], Awaitable[VolumeFileResult]],
    ) -> VolumeFileResult:
        """Serialize, durably prepare, apply, and complete one exact-key mutation."""

    async def record(
        self,
        audit: VolumeFileMutationAudit,
        result: VolumeFileResult,
    ) -> None:
        """Durably record a non-replayable mutation after it completes."""


class PostgresVolumeFileMutationJournal:
    """RP-04 journal backed by the existing append-only config audit table."""

    _KIND = "runpod_volume_file_mutation"

    def __init__(self, pool: asyncpg.Pool, *, actor: AuditActor) -> None:
        self._pool = pool
        self._actor = actor

    async def run_idempotent(
        self,
        audit: VolumeFileMutationAudit,
        validate: Callable[[], Awaitable[None]],
        apply: Callable[[bool], Awaitable[VolumeFileResult]],
    ) -> VolumeFileResult:
        if audit.operation not in {"upload", "delete"}:
            raise VolumeFileValidationError(
                "durable replay is limited to exact-key provider mutations"
            )
        key = audit.idempotency_key
        if key is None:
            raise VolumeFileValidationError("idempotency_key is required for provider mutations")
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    "SELECT pg_advisory_lock(hashtextextended($1, 0))",
                    f"{self._KIND}:{key}",
                )
                try:
                    async with conn.transaction():
                        payload = await self._load(conn, key)
                    retry_started = False
                    if payload is not None:
                        if payload.get("request_hash") != audit.request_hash:
                            raise VolumeFileIdempotencyConflict("idempotency key conflict")
                        state = payload.get("state")
                        if state == "completed":
                            return replace(_result_from_audit(payload.get("result")), replayed=True)
                        if state == "conflict":
                            raise VolumeFilePreconditionConflict(
                                "create-only upload previously reached a provider conflict"
                            )
                        if state != "started":
                            raise VolumeFileAuditUnavailable("malformed volume-file audit state")
                        recovery = payload.get("recovery")
                        if recovery != audit.recovery:
                            raise VolumeFileAuditUnavailable(
                                "malformed volume-file audit recovery policy"
                            )
                        if recovery == "manual":
                            raise VolumeFileMutationAmbiguous(
                                "the prior provider mutation has an unresolved outcome"
                            )
                        retry_started = True
                    else:
                        # Validate provider preconditions while the session lock is
                        # held but before started evidence is written. No transaction
                        # is open during this provider read.
                        await validate()
                        async with conn.transaction():
                            await self._insert(conn, audit, state="started", result=None)

                    # The session lock serializes equal keys across this provider I/O,
                    # while the preceding transaction is already committed.
                    try:
                        result = await apply(retry_started)
                    except VolumeFilePreconditionConflict:
                        async with conn.transaction():
                            await self._insert(conn, audit, state="conflict", result=None)
                        raise
                    try:
                        async with conn.transaction():
                            await self._insert(conn, audit, state="completed", result=result)
                    except (
                        Exception
                    ) as exc:  # reason: completion must be durable after provider change
                        log.warning(
                            "RunPod volume-file durable completion failed (%s)",
                            type(exc).__name__,
                        )
                        raise VolumeFileAuditError(
                            "durable audit failed after provider change"
                        ) from exc
                    return result
                finally:
                    unlock = asyncio.create_task(
                        conn.execute(
                            "SELECT pg_advisory_unlock(hashtextextended($1, 0))",
                            f"{self._KIND}:{key}",
                        )
                    )
                    try:
                        await asyncio.shield(unlock)
                    except asyncio.CancelledError:
                        try:
                            await unlock
                        except (
                            Exception
                        ) as exc:  # reason: cancellation still requires best-effort unlock
                            log.warning(
                                "RunPod volume-file advisory unlock failed (%s)",
                                type(exc).__name__,
                            )
                        raise
                    except (
                        Exception
                    ) as exc:  # reason: unlock failure is logged without masking outcome
                        log.warning(
                            "RunPod volume-file advisory unlock failed (%s)",
                            type(exc).__name__,
                        )
        except VolumeFileError, _CancellationRequested:
            raise
        except Exception as exc:  # reason: database detail may contain operator input
            log.warning("RunPod volume-file durable journal failed (%s)", type(exc).__name__)
            raise VolumeFileAuditUnavailable("durable mutation audit is unavailable") from exc

    async def record(
        self,
        audit: VolumeFileMutationAudit,
        result: VolumeFileResult,
    ) -> None:
        if audit.operation != "download":
            raise VolumeFileValidationError("non-replayable audit is limited to local downloads")
        try:
            await self._insert(None, audit, state="completed", result=result)
        except VolumeFileError:
            raise
        except Exception as exc:  # reason: database detail may contain operator input
            log.warning("RunPod volume-file durable journal failed (%s)", type(exc).__name__)
            raise VolumeFileAuditError("durable audit failed after local change") from exc

    async def _load(self, conn: asyncpg.Connection, key: str) -> Mapping[str, object] | None:
        row = await conn.fetchrow(
            """
            SELECT new_value
            FROM pitwall.config_audit
            WHERE entity_type = 'volume'
              AND new_value ->> 'kind' = $1
              AND new_value ->> 'idempotency_key' = $2
            ORDER BY id DESC
            LIMIT 1
            """,
            self._KIND,
            key,
        )
        if row is None:
            return None
        payload = row["new_value"]
        if not isinstance(payload, Mapping):
            raise VolumeFileAuditUnavailable("malformed volume-file audit row")
        return cast(Mapping[str, object], payload)

    async def _insert(
        self,
        conn: asyncpg.Connection | None,
        audit: VolumeFileMutationAudit,
        *,
        state: Literal["started", "completed", "conflict"],
        result: VolumeFileResult | None,
    ) -> None:
        payload: dict[str, Any] = {
            "kind": self._KIND,
            "state": state,
            "provider": "runpod",
            "operation": audit.operation,
            "data_center_id": audit.data_center_id,
            "object_key": audit.object_key,
            "request_hash": audit.request_hash,
            "recovery": audit.recovery,
            "idempotency_key": audit.idempotency_key,
            "result": _audit_result(result) if result is not None else None,
        }
        await insert_audit(
            self._pool,
            actor=self._actor,
            action=audit.action,
            entity_type="volume",
            entity_id=audit.volume_id,
            new_value=cast(Any, payload),
            change_reason=f"bounded RunPod volume-file {audit.operation} {state}",
            conn=conn,
        )


@dataclass(frozen=True, slots=True)
class _SafeLocalTarget:
    """A relative target anchored to an already-open safe parent directory."""

    parent_fd: int
    name: str


class VolumeObjectStore(Protocol):
    """The strict object-store seam used by the shared service."""

    async def list_objects(
        self,
        volume_id: str,
        data_center_id: str,
        *,
        prefix: str,
        max_items: int,
    ) -> VolumeObjectPage:
        """Return one bounded page."""

    async def put_object(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
        body: bytes,
        *,
        create_only: bool,
    ) -> None:
        """Write one already-bounded object body."""

    async def get_object_range(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
        *,
        offset: int,
        max_bytes: int,
    ) -> bytes:
        """Read one bounded object range."""

    async def delete_object(self, volume_id: str, data_center_id: str, key: str) -> None:
        """Delete one object."""


class PodLogReader(Protocol):
    """Bounded provider log-reader seam used by the shared service."""

    async def read(self, pod_id: str, *, max_lines: int, max_bytes: int) -> BoundedPodLogPayload:
        """Return a bounded raw payload."""


class NetworkVolumeObjectStore:
    """Adapter from the existing RunPod S3 client to ``VolumeObjectStore``."""

    def __init__(self, client: NetworkVolumeClient) -> None:
        self._client = client

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_objects(
        self,
        volume_id: str,
        data_center_id: str,
        *,
        prefix: str,
        max_items: int,
    ) -> VolumeObjectPage:
        page: S3ObjectPage = await self._client.list_objects_page(
            volume_id,
            data_center_id,
            prefix=prefix,
            max_items=max_items,
        )
        return VolumeObjectPage(
            objects=tuple(
                sorted(
                    (
                        VolumeObject(
                            key=entry.key,
                            size=entry.size,
                            last_modified=entry.last_modified,
                        )
                        for entry in page.objects
                    ),
                    key=lambda entry: entry.key,
                )
            ),
            truncated=page.truncated,
        )

    async def put_object(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
        body: bytes,
        *,
        create_only: bool,
    ) -> None:
        try:
            await self._client.put_object(
                volume_id,
                data_center_id,
                key,
                body,
                create_only=create_only,
            )
        except S3ObjectPreconditionFailed as exc:
            raise VolumeFilePreconditionConflict(
                "create-only upload found an existing object"
            ) from exc

    async def get_object_range(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
        *,
        offset: int,
        max_bytes: int,
    ) -> bytes:
        return await self._client.get_object_range(
            volume_id,
            data_center_id,
            key,
            offset=offset,
            max_bytes=max_bytes,
        )

    async def delete_object(self, volume_id: str, data_center_id: str, key: str) -> None:
        await self._client.delete_object(volume_id, data_center_id, key)


def _bounded_operation[S: _VolumeFileBase, **P](
    method: Callable[Concatenate[S, P], Awaitable[VolumeFileResult]],
) -> Callable[Concatenate[S, P], Awaitable[VolumeFileResult]]:
    """Apply the shared timeout to the complete operator operation, not a call alone."""

    @wraps(method)
    async def wrapped(
        self: S,
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> VolumeFileResult:
        try:
            async with asyncio.timeout(self._limits.timeout_s):
                return await method(self, *args, **kwargs)
        except TimeoutError as exc:
            log.warning("bounded RunPod operation timed out")
            raise VolumeFileTimeout("volume-file operation timed out") from exc

    return wrapped


class _MissingObjectStore:
    """Stands in when S3 credentials are absent: pod logs work, object calls refuse."""

    def _refuse(self) -> VolumeFileConfigurationError:
        return VolumeFileConfigurationError("S3 credential references are not configured")

    async def list_objects(
        self, volume_id: str, data_center_id: str, *, prefix: str, max_items: int
    ) -> VolumeObjectPage:
        raise self._refuse()

    async def put_object(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
        body: bytes,
        *,
        create_only: bool,
    ) -> None:
        raise self._refuse()

    async def get_object_range(
        self, volume_id: str, data_center_id: str, key: str, *, offset: int, max_bytes: int
    ) -> bytes:
        raise self._refuse()

    async def delete_object(self, volume_id: str, data_center_id: str, key: str) -> None:
        raise self._refuse()


def _requires_object_store[S: _VolumeFileBase, **P](
    method: Callable[Concatenate[S, P], Awaitable[VolumeFileResult]],
) -> Callable[Concatenate[S, P], Awaitable[VolumeFileResult]]:
    """Refuse an object operation before validation or journaling when S3 is unconfigured."""

    @wraps(method)
    async def wrapped(
        self: S,
        /,
        *args: P.args,
        **kwargs: P.kwargs,
    ) -> VolumeFileResult:
        if isinstance(self._object_store, _MissingObjectStore):
            raise self._object_store._refuse()
        return await method(self, *args, **kwargs)

    return wrapped


class _VolumeFileBase:
    """Shared state and bounded-call helpers for the volume file operations."""

    def __init__(
        self,
        object_store: VolumeObjectStore,
        pod_logs: PodLogReader,
        *,
        limits: VolumeFileLimits | None = None,
        inspection_service: PreSpendInspectionService | None = None,
        mutation_journal: VolumeFileMutationJournal | None = None,
    ) -> None:
        self._object_store = object_store
        self._pod_logs = pod_logs
        self._limits = limits or VolumeFileLimits()
        self._inspection_service = inspection_service or _default_upload_inspection_service(
            self._limits
        )
        self._mutation_journal = mutation_journal

    @property
    def limits(self) -> VolumeFileLimits:
        return self._limits

    async def aclose(self) -> None:
        """Close optional provider clients held by a configured service."""
        await _maybe_aclose(self._object_store)
        await _maybe_aclose(self._pod_logs)

    def _limit(self, name: str, requested: int | None, default: int) -> int:
        value = default if requested is None else requested
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise VolumeFileValidationError(f"{name} must be a positive integer")
        if value > default:
            raise VolumeFileLimitExceeded(f"{name} exceeds the configured limit")
        return value

    def _require_mutation_journal(self) -> VolumeFileMutationJournal:
        journal = self._mutation_journal
        if journal is None:
            raise VolumeFileAuditUnavailable("durable mutation audit is not configured")
        return journal

    def _inspect_request(self, payload: object, *, dry_run: bool = False) -> None:
        """Inspect once before provider/local mutation I/O without reflecting input."""
        inspect = self._inspection_service.preview if dry_run else self._inspection_service.inspect
        decision = inspect(payload)
        # File bytes and object identities have no schema-preserving rewrite
        # contract.  A redact decision therefore blocks instead of silently
        # changing content, checksums, or the selected provider object.
        if decision.decision != PreSpendDecision.ALLOW:
            raise VolumeFilePreSpendRejected("pre-spend payload rejected")

    async def _find_object(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
    ) -> VolumeObject | None:
        page = await self._provider_call(
            "object lookup",
            lambda: self._object_store.list_objects(
                volume_id,
                data_center_id,
                prefix=key,
                max_items=1,
            ),
        )
        # The exact key sorts first among every key that starts with it, so a
        # single-item prefix listing is a complete existence check.
        first = page.objects[0] if page.objects else None
        if first is not None and first.key == key:
            return first
        return None

    async def _provider_call(self, operation: str, call: Callable[[], Awaitable[T]]) -> T:
        try:
            async with asyncio.timeout(self._limits.timeout_s):
                return await call()
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            log.warning("bounded RunPod %s timed out", operation)
            raise VolumeFileTimeout("provider request timed out") from exc
        except VolumeFileError:
            raise
        except S3ObjectNotFound as exc:
            raise VolumeFileNotFound("object does not exist") from exc
        except Exception as exc:  # reason: provider detail may contain credentials or payloads
            log.warning("bounded RunPod %s failed (%s)", operation, type(exc).__name__)
            raise VolumeFileProviderError("provider request failed") from exc

    def _check_cancel(self, cancel_event: asyncio.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise _CancellationRequested

    async def _emit(
        self,
        events: list[VolumeFileProgress],
        callback: ProgressCallback | None,
        operation: Operation,
        phase: str,
        completed: int,
        total: int | None,
    ) -> None:
        event = VolumeFileProgress(
            sequence=len(events) + 1,
            operation=operation,
            phase=phase,
            bytes_completed=completed,
            total_bytes=total,
        )
        events.append(event)
        if callback is not None:
            result = callback(event)
            if result is not None:
                await result

    def _cancelled_result(
        self,
        operation: Operation,
        *,
        volume_id: str | None = None,
        data_center_id: str | None = None,
        object_key: str | None = None,
        pod_id: str | None = None,
        progress: list[VolumeFileProgress],
    ) -> VolumeFileResult:
        return VolumeFileResult(
            operation=operation,
            status="cancelled",
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=object_key,
            pod_id=pod_id,
            progress=tuple(progress),
        )


class _VolumeObjectListing(_VolumeFileBase):
    """Listing: bounded object pages and pod-log observation."""

    @_bounded_operation
    @_requires_object_store
    async def list_objects(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        prefix: str = "",
        max_items: int | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        """List one bounded, deterministic object page."""
        _validate_volume_context(volume_id, data_center_id)
        normalized_prefix = _validate_prefix(prefix)
        limit = self._limit("max_items", max_items, self._limits.max_list_items)
        self._inspect_request(
            {
                "volume_id": volume_id,
                "data_center_id": data_center_id,
                "prefix": normalized_prefix,
                "max_items": limit,
            }
        )
        events: list[VolumeFileProgress] = []
        try:
            self._check_cancel(cancel_event)
            await self._emit(events, progress, "list", "requesting", 0, None)
            page = await self._provider_call(
                "list",
                lambda: self._object_store.list_objects(
                    volume_id,
                    data_center_id,
                    prefix=normalized_prefix,
                    max_items=limit,
                ),
            )
            self._check_cancel(cancel_event)
        except _CancellationRequested:
            return self._cancelled_result(
                "list", volume_id=volume_id, data_center_id=data_center_id, progress=events
            )
        await self._emit(events, progress, "list", "completed", 0, None)
        return VolumeFileResult(
            operation="list",
            status="completed",
            volume_id=volume_id,
            data_center_id=data_center_id,
            objects=tuple(sorted(page.objects, key=lambda entry: entry.key)),
            truncated=page.truncated,
            progress=tuple(events),
        )

    @_bounded_operation
    async def read_pod_logs(
        self,
        *,
        pod_id: str,
        max_lines: int | None = None,
        max_bytes: int | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        """Read redacted ordered pod diagnostics under strict line/byte caps."""
        if not _ID_RE.fullmatch(pod_id):
            raise VolumeFileValidationError("pod_id is invalid")
        lines_limit = self._limit("max_lines", max_lines, self._limits.max_log_lines)
        bytes_limit = self._limit("max_bytes", max_bytes, self._limits.max_log_bytes)
        self._inspect_request(
            {
                "pod_id": pod_id,
                "max_lines": lines_limit,
                "max_bytes": bytes_limit,
            }
        )
        events: list[VolumeFileProgress] = []
        try:
            self._check_cancel(cancel_event)
            await self._emit(events, progress, "logs", "requesting", 0, bytes_limit)
            payload = await self._provider_call(
                "logs",
                lambda: self._pod_logs.read(
                    pod_id,
                    max_lines=lines_limit,
                    max_bytes=bytes_limit,
                ),
            )
            self._check_cancel(cancel_event)
        except _CancellationRequested:
            return self._cancelled_result("logs", pod_id=pod_id, progress=events)
        if payload.bytes_read > bytes_limit:
            raise VolumeFileProviderError("provider returned oversized log data")
        lines, line_truncated = _parse_log_lines(payload.text, max_lines=lines_limit)
        await self._emit(
            events, progress, "logs", "completed", payload.bytes_read, payload.bytes_read
        )
        return VolumeFileResult(
            operation="logs",
            status="completed",
            pod_id=pod_id,
            bytes_transferred=payload.bytes_read,
            logs=lines,
            truncated=payload.truncated or line_truncated,
            progress=tuple(events),
        )


class _VolumeObjectReading(_VolumeObjectListing):
    """Reading: bounded object ranges and exact-match verification."""

    @_bounded_operation
    @_requires_object_store
    async def read_object_chunk(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        offset: int = 0,
        max_bytes: int | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        """Read one bounded object chunk for REST, MCP, and TUI framing."""
        _validate_volume_context(volume_id, data_center_id)
        key = _validate_object_key(object_key)
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise VolumeFileValidationError("offset must be a non-negative integer")
        limit = self._limit("max_bytes", max_bytes, self._limits.max_chunk_bytes)
        self._inspect_request(
            {
                "volume_id": volume_id,
                "data_center_id": data_center_id,
                "object_key": key,
                "offset": offset,
                "max_bytes": limit,
            }
        )
        events: list[VolumeFileProgress] = []
        try:
            self._check_cancel(cancel_event)
            await self._emit(events, progress, "download", "requesting", 0, limit)
            data = await self._provider_call(
                "download",
                lambda: self._object_store.get_object_range(
                    volume_id,
                    data_center_id,
                    key,
                    offset=offset,
                    max_bytes=limit,
                ),
            )
            self._check_cancel(cancel_event)
        except _CancellationRequested:
            return self._cancelled_result(
                "download",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                progress=events,
            )
        if len(data) > limit:
            raise VolumeFileProviderError("provider returned an oversized object range")
        checksum = hashlib.sha256(data).hexdigest()
        await self._emit(events, progress, "download", "completed", len(data), len(data))
        return VolumeFileResult(
            operation="download",
            status="completed",
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=key,
            bytes_transferred=len(data),
            checksum_sha256=checksum,
            content_base64=base64.b64encode(data).decode("ascii"),
            progress=tuple(events),
        )

    async def _object_matches_exactly(
        self,
        volume_id: str,
        data_center_id: str,
        key: str,
        *,
        byte_count: int,
        checksum_sha256: str,
    ) -> bool:
        """Bound a recovery read while proving both object length and digest."""
        digest = hashlib.sha256()
        received = 0
        crossed_expected_end = False
        if byte_count == 0:
            entry = await self._find_object(volume_id, data_center_id, key)
            return entry is not None and entry.size == 0 and digest.hexdigest() == checksum_sha256
        while received < byte_count:
            remaining = byte_count - received
            if self._limits.max_chunk_bytes > 1 and remaining == self._limits.max_chunk_bytes:
                requested = self._limits.max_chunk_bytes - 1
            elif remaining < self._limits.max_chunk_bytes:
                requested = remaining + 1
                crossed_expected_end = True
            else:
                requested = self._limits.max_chunk_bytes
            data = await self._provider_call(
                "ambiguous upload verification",
                partial(
                    _get_object_range,
                    self._object_store,
                    volume_id,
                    data_center_id,
                    key,
                    offset=received,
                    max_bytes=requested,
                ),
            )
            if not data or len(data) > remaining:
                return False
            digest.update(data)
            received += len(data)
        if not crossed_expected_end:
            # A one-byte configured chunk cannot overlap the expected EOF. The
            # post-read bounded metadata lookup supplies the exact-size proof.
            entry = await self._find_object(volume_id, data_center_id, key)
            if entry is None or entry.size != byte_count:
                return False
        return digest.hexdigest() == checksum_sha256


class _VolumeObjectMutations(_VolumeObjectReading):
    """Mutation journal: idempotent, audited provider writes and deletes."""

    @_bounded_operation
    @_requires_object_store
    async def upload_bytes(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        body: bytes,
        overwrite: bool = False,
        expected_sha256: str | None = None,
        dry_run: bool = False,
        idempotency_key: str | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
        _events: list[VolumeFileProgress] | None = None,
    ) -> VolumeFileResult:
        """Upload one bounded body after safe overwrite and checksum checks."""
        _validate_volume_context(volume_id, data_center_id)
        key = _validate_object_key(object_key)
        _validate_idempotency_key(idempotency_key)
        if not isinstance(body, bytes):
            raise VolumeFileValidationError("upload body must be bytes")
        if len(body) > self._limits.max_transfer_bytes:
            raise VolumeFileLimitExceeded("upload exceeds the configured transfer limit")
        expected = _normalize_checksum(expected_sha256)
        checksum = hashlib.sha256(body).hexdigest()
        if expected is not None and checksum != expected:
            raise VolumeFileChecksumMismatch("upload body checksum does not match expected_sha256")

        try:
            inspected_content: object = body.decode("utf-8")
        except UnicodeDecodeError:
            inspected_content = body
        await asyncio.to_thread(
            self._inspect_request,
            {
                "volume_id": volume_id,
                "data_center_id": data_center_id,
                "object_key": key,
                "content": inspected_content,
                "overwrite": overwrite,
                "expected_sha256": expected,
                "idempotency_key": idempotency_key,
            },
            dry_run=dry_run,
        )

        events = _events if _events is not None else []
        try:
            self._check_cancel(cancel_event)
        except _CancellationRequested:
            return self._cancelled_result(
                "upload",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                progress=events,
            )

        if dry_run:
            await self._emit(events, progress, "upload", "checking", 0, len(body))
            existing = await self._find_object(volume_id, data_center_id, key)
            if existing is not None and not overwrite:
                raise VolumeFileConfirmationRequired("overwrite requires explicit confirmation")
            try:
                self._check_cancel(cancel_event)
            except _CancellationRequested:
                return self._cancelled_result(
                    "upload",
                    volume_id=volume_id,
                    data_center_id=data_center_id,
                    object_key=key,
                    progress=events,
                )
            await self._emit(events, progress, "upload", "dry_run", 0, len(body))
            return VolumeFileResult(
                operation="upload",
                status="dry_run",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                checksum_sha256=checksum,
                progress=tuple(events),
                target=_provider_target(volume_id, data_center_id, key),
                effect=(
                    "replace the existing object" if existing is not None else "create an object"
                ),
                estimated_ceiling=f"at most {len(body)} bytes and one provider write",
                irreversible=existing is not None,
                consequences=(
                    "Existing provider object bytes will be permanently replaced."
                    if existing is not None
                    else "A new provider object will be stored and may incur storage charges."
                ),
            )

        journal = self._require_mutation_journal()
        if idempotency_key is None:
            raise VolumeFileValidationError("idempotency_key is required for provider mutations")
        audit = VolumeFileMutationAudit(
            operation="upload",
            action="update" if overwrite else "create",
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=key,
            request_hash=_request_hash(
                {
                    "operation": "upload",
                    "volume_id": volume_id,
                    "data_center_id": data_center_id,
                    "object_key": key,
                    "checksum_sha256": checksum,
                    "bytes": len(body),
                    "overwrite": overwrite,
                    "expected_sha256": expected,
                }
            ),
            recovery="manual" if overwrite else "conditional_create",
            idempotency_key=idempotency_key,
        )
        existing_present = overwrite

        async def validate() -> None:
            nonlocal existing_present
            await self._emit(events, progress, "upload", "checking", 0, len(body))
            existing = await self._find_object(volume_id, data_center_id, key)
            if existing is not None and not overwrite:
                raise VolumeFileConfirmationRequired("overwrite requires explicit confirmation")
            self._check_cancel(cancel_event)
            existing_present = existing is not None

        async def apply(retry_started: bool) -> VolumeFileResult:
            nonlocal existing_present
            if retry_started:
                if overwrite:
                    # The first unconditional Put may have landed before its
                    # completion audit failed. A later external generation must
                    # not be replaced merely because the request hash is equal.
                    raise VolumeFileMutationAmbiguous(
                        "an overwrite upload has an unresolved provider outcome"
                    )
                # Only the exact request hash reaches this path. Conservatively
                # retain create semantics in the conditional recovery attempt.
                existing_present = False
            await self._emit(events, progress, "upload", "writing", 0, len(body))
            verified_ambiguous_create = False
            try:
                await self._provider_call(
                    "upload",
                    lambda: self._object_store.put_object(
                        volume_id,
                        data_center_id,
                        key,
                        body,
                        create_only=not overwrite,
                    ),
                )
            except VolumeFilePreconditionConflict as precondition:
                # A new create-only request must never treat someone else's object
                # as its own. A durable started row means the exact conditional Put
                # may already have landed, so recover only after bounded byte-count
                # and digest verification of the current object.
                if not retry_started or overwrite:
                    raise
                await self._emit(events, progress, "upload", "verifying", 0, len(body))
                try:
                    matches = await self._object_matches_exactly(
                        volume_id,
                        data_center_id,
                        key,
                        byte_count=len(body),
                        checksum_sha256=checksum,
                    )
                except VolumeFileError as exc:
                    raise VolumeFilePreconditionConflict(
                        "ambiguous create-only upload could not be verified"
                    ) from exc
                if not matches:
                    raise VolumeFilePreconditionConflict(
                        "ambiguous create-only upload conflicts with existing bytes"
                    ) from precondition
                verified_ambiguous_create = True
            # S3 PutObject is one bounded provider call. Once dispatched, cancellation
            # cannot tell whether the provider committed, so report its completed
            # outcome rather than falsely claiming a rollback.
            await self._emit(events, progress, "upload", "completed", len(body), len(body))
            return VolumeFileResult(
                operation="upload",
                status="completed",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                bytes_transferred=len(body),
                checksum_sha256=checksum,
                progress=tuple(events),
                target=_provider_target(volume_id, data_center_id, key),
                effect=(
                    "replaced the existing object"
                    if existing_present
                    else (
                        "verified the requested object after an ambiguous create"
                        if verified_ambiguous_create
                        else "created an object"
                    )
                ),
                estimated_ceiling=(
                    f"{len(body)} bytes, one conditional provider write, and bounded verification"
                    if verified_ambiguous_create
                    else f"{len(body)} bytes and one provider write"
                ),
                irreversible=existing_present,
                consequences=(
                    "Existing provider object bytes were permanently replaced."
                    if existing_present
                    else "A new provider object was stored and may incur storage charges."
                ),
            )

        try:
            return await journal.run_idempotent(audit, validate, apply)
        except _CancellationRequested:
            return self._cancelled_result(
                "upload",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                progress=events,
            )

    @_bounded_operation
    @_requires_object_store
    async def delete_object(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        confirm_delete: bool = False,
        dry_run: bool = False,
        idempotency_key: str | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        """Delete one object only after explicit confirmation; dry-run never writes."""
        _validate_volume_context(volume_id, data_center_id)
        key = _validate_object_key(object_key)
        _validate_idempotency_key(idempotency_key)
        if not confirm_delete:
            raise VolumeFileConfirmationRequired("delete requires explicit confirmation")
        self._inspect_request(
            {
                "volume_id": volume_id,
                "data_center_id": data_center_id,
                "object_key": key,
                "idempotency_key": idempotency_key,
            },
            dry_run=dry_run,
        )
        events: list[VolumeFileProgress] = []
        try:
            self._check_cancel(cancel_event)
        except _CancellationRequested:
            return self._cancelled_result(
                "delete",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                progress=events,
            )
        if dry_run:
            await self._emit(events, progress, "delete", "dry_run", 0, None)
            return VolumeFileResult(
                operation="delete",
                status="dry_run",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                progress=tuple(events),
                target=_provider_target(volume_id, data_center_id, key),
                effect="permanently delete the provider object",
                estimated_ceiling="one provider delete request",
                irreversible=True,
                consequences="The provider object and its stored bytes cannot be recovered by Pitwall.",
            )
        journal = self._require_mutation_journal()
        if idempotency_key is None:
            raise VolumeFileValidationError("idempotency_key is required for provider mutations")
        audit = VolumeFileMutationAudit(
            operation="delete",
            action="delete",
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=key,
            request_hash=_request_hash(
                {
                    "operation": "delete",
                    "volume_id": volume_id,
                    "data_center_id": data_center_id,
                    "object_key": key,
                }
            ),
            recovery="manual",
            idempotency_key=idempotency_key,
        )

        async def validate() -> None:
            return None

        async def apply(retry_started: bool) -> VolumeFileResult:
            if retry_started:
                # A successful earlier delete is indistinguishable from a failed
                # call followed by another actor recreating the key. Repeating it
                # could delete that new generation.
                raise VolumeFileMutationAmbiguous("a delete has an unresolved provider outcome")
            await self._emit(events, progress, "delete", "deleting", 0, None)
            await self._provider_call(
                "delete",
                lambda: self._object_store.delete_object(volume_id, data_center_id, key),
            )
            await self._emit(events, progress, "delete", "completed", 0, None)
            return VolumeFileResult(
                operation="delete",
                status="completed",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                progress=tuple(events),
                target=_provider_target(volume_id, data_center_id, key),
                effect="permanently deleted the provider object",
                estimated_ceiling="one provider delete request",
                irreversible=True,
                consequences="The provider object and its stored bytes cannot be recovered by Pitwall.",
            )

        return await journal.run_idempotent(audit, validate, apply)


class _VolumeLocalTransfers(_VolumeObjectMutations):
    """Publishing: local-file uploads and atomic local publication of downloads."""

    @_bounded_operation
    @_requires_object_store
    async def upload_file(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        local_root: Path,
        local_path: str,
        overwrite: bool = False,
        expected_sha256: str | None = None,
        dry_run: bool = False,
        idempotency_key: str | None = None,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        """Read a safe local regular file in bounded chunks and upload it."""
        _validate_volume_context(volume_id, data_center_id)
        key = _validate_object_key(object_key)
        _validate_idempotency_key(idempotency_key)
        events: list[VolumeFileProgress] = []
        try:
            source_fd = await asyncio.to_thread(_open_regular_readonly, local_root, local_path)
            body = await self._read_local_file(
                source_fd,
                operation="upload",
                events=events,
                progress=progress,
                cancel_event=cancel_event,
            )
        except _CancellationRequested:
            return self._cancelled_result(
                "upload",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                progress=events,
            )
        result = await self.upload_bytes(
            volume_id=volume_id,
            data_center_id=data_center_id,
            object_key=key,
            body=body,
            overwrite=overwrite,
            expected_sha256=expected_sha256,
            dry_run=dry_run,
            idempotency_key=idempotency_key,
            cancel_event=cancel_event,
            progress=progress,
            _events=events,
        )
        return result

    @_bounded_operation
    @_requires_object_store
    async def download_to_path(
        self,
        *,
        volume_id: str,
        data_center_id: str,
        object_key: str,
        local_root: Path,
        local_path: str,
        overwrite: bool = False,
        expected_sha256: str | None = None,
        dry_run: bool = False,
        cancel_event: asyncio.Event | None = None,
        progress: ProgressCallback | None = None,
    ) -> VolumeFileResult:
        """Download a bounded object into a safe temp file, then atomically publish it."""
        _validate_volume_context(volume_id, data_center_id)
        key = _validate_object_key(object_key)
        expected = _normalize_checksum(expected_sha256)
        self._inspect_request(
            {
                "volume_id": volume_id,
                "data_center_id": data_center_id,
                "object_key": key,
                "local_path": local_path,
                "overwrite": overwrite,
                "expected_sha256": expected,
            },
            dry_run=dry_run,
        )
        events: list[VolumeFileProgress] = []
        target = await asyncio.to_thread(
            _open_safe_destination, local_root, local_path, overwrite=overwrite
        )
        try:
            try:
                self._check_cancel(cancel_event)
                await self._emit(events, progress, "download", "checking", 0, None)
                entry = await self._find_object(volume_id, data_center_id, key)
                if entry is None:
                    raise VolumeFileNotFound("object does not exist")
                if entry.size > self._limits.max_transfer_bytes:
                    raise VolumeFileLimitExceeded("download exceeds the configured transfer limit")
                self._check_cancel(cancel_event)
            except _CancellationRequested:
                return self._cancelled_result(
                    "download",
                    volume_id=volume_id,
                    data_center_id=data_center_id,
                    object_key=key,
                    progress=events,
                )

            if dry_run:
                await self._emit(events, progress, "download", "dry_run", 0, entry.size)
                return VolumeFileResult(
                    operation="download",
                    status="dry_run",
                    volume_id=volume_id,
                    data_center_id=data_center_id,
                    object_key=key,
                    progress=tuple(events),
                    target=f"{_provider_target(volume_id, data_center_id, key)} -> local:{local_path}",
                    effect="download the provider object to the selected local file",
                    estimated_ceiling=(
                        f"at most {entry.size} bytes in bounded provider reads and one local write"
                    ),
                    irreversible=overwrite,
                    consequences=(
                        "The selected local file will be permanently replaced if it exists."
                        if overwrite
                        else "A new local file will be created; an existing file will not be changed."
                    ),
                )

            journal = self._require_mutation_journal()
            temp_name: str | None = None
            fd: int | None = None
            received = 0
            digest = hashlib.sha256()
            try:
                active_fd, active_temp_name = await asyncio.to_thread(_open_temp_file, target)
                fd = active_fd
                temp_name = active_temp_name
                while received < entry.size:
                    self._check_cancel(cancel_event)
                    requested = min(self._limits.max_chunk_bytes, entry.size - received)
                    data = await self._provider_call(
                        "download",
                        partial(
                            _get_object_range,
                            self._object_store,
                            volume_id,
                            data_center_id,
                            key,
                            offset=received,
                            max_bytes=requested,
                        ),
                    )
                    if not data:
                        raise VolumeFileProviderError("provider ended object download early")
                    if len(data) > requested:
                        raise VolumeFileProviderError("provider returned an oversized object range")
                    self._check_cancel(cancel_event)
                    await asyncio.to_thread(_write_all, active_fd, data)
                    digest.update(data)
                    received += len(data)
                    await self._emit(events, progress, "download", "writing", received, entry.size)
                await asyncio.to_thread(os.fsync, active_fd)
                await asyncio.to_thread(os.close, active_fd)
                fd = None
                checksum = digest.hexdigest()
                if expected is not None and checksum != expected:
                    raise VolumeFileChecksumMismatch(
                        "download checksum does not match expected_sha256"
                    )
                if overwrite:
                    await asyncio.to_thread(_replace_temp, active_temp_name, target)
                else:
                    await asyncio.to_thread(_publish_no_overwrite, active_temp_name, target)
                temp_name = None
            except _CancellationRequested:
                return self._cancelled_result(
                    "download",
                    volume_id=volume_id,
                    data_center_id=data_center_id,
                    object_key=key,
                    progress=events,
                )
            finally:
                if fd is not None:
                    await asyncio.to_thread(os.close, fd)
                if temp_name is not None:
                    await asyncio.to_thread(_unlink_if_exists, temp_name, target.parent_fd)

            await self._emit(events, progress, "download", "completed", received, entry.size)
            result = VolumeFileResult(
                operation="download",
                status="completed",
                volume_id=volume_id,
                data_center_id=data_center_id,
                object_key=key,
                bytes_transferred=received,
                checksum_sha256=checksum,
                progress=tuple(events),
                target=f"{_provider_target(volume_id, data_center_id, key)} -> local:{local_path}",
                effect="downloaded the provider object to the selected local file",
                estimated_ceiling=f"{received} bytes in bounded provider reads and one local write",
                irreversible=overwrite,
                consequences=(
                    "The selected local file was permanently replaced if it existed."
                    if overwrite
                    else "A new local file was created without replacing an existing file."
                ),
            )
            await journal.record(
                VolumeFileMutationAudit(
                    operation="download",
                    action="update" if overwrite else "create",
                    volume_id=volume_id,
                    data_center_id=data_center_id,
                    object_key=key,
                    request_hash=_request_hash(
                        {
                            "operation": "download",
                            "volume_id": volume_id,
                            "data_center_id": data_center_id,
                            "object_key": key,
                            "checksum_sha256": checksum,
                            "bytes": received,
                            "overwrite": overwrite,
                        }
                    ),
                    recovery="manual",
                ),
                result,
            )
            return result
        finally:
            await asyncio.to_thread(os.close, target.parent_fd)

    async def _read_local_file(
        self,
        fd: int,
        *,
        operation: Operation,
        events: list[VolumeFileProgress],
        progress: ProgressCallback | None,
        cancel_event: asyncio.Event | None,
    ) -> bytes:
        try:
            file_size = await asyncio.to_thread(_regular_file_size, fd)
            if file_size > self._limits.max_transfer_bytes:
                raise VolumeFileLimitExceeded("local file exceeds the configured transfer limit")
            chunks: list[bytes] = []
            completed = 0
            while completed < file_size:
                self._check_cancel(cancel_event)
                chunk = await asyncio.to_thread(os.read, fd, self._limits.max_chunk_bytes)
                if not chunk:
                    raise VolumeFileValidationError("local file changed while it was being read")
                chunks.append(chunk)
                completed += len(chunk)
                await self._emit(events, progress, operation, "reading", completed, file_size)
            self._check_cancel(cancel_event)
            return b"".join(chunks)
        finally:
            await asyncio.to_thread(os.close, fd)


class VolumeFileService(_VolumeLocalTransfers):
    """Shared bounded service for object storage and pod-log observation."""


def build_configured_volume_file_service(
    *,
    limits: VolumeFileLimits | None = None,
    s3_credentials: S3CredentialReferences | None = None,
    environ: Mapping[str, str] | None = None,
    rest_base_url: str | None = None,
    audit_pool: asyncpg.Pool | None = None,
    audit_actor: AuditActor = "system",
    mutation_journal: VolumeFileMutationJournal | None = None,
) -> VolumeFileService:
    """Resolve secret values only at the provider-client construction boundary."""
    configured_limits = limits or VolumeFileLimits()
    references = s3_credentials or S3CredentialReferences()
    environment = os.environ if environ is None else environ
    access_key = environment.get(references.access_key_env)
    secret_key = environment.get(references.secret_key_env)
    api_key, _key_source = resolve_runpod_api_key(environment)
    if not api_key:
        raise VolumeFileConfigurationError("pod-log credential reference is not configured")
    base_url = rest_base_url or environment.get("RUNPOD_REST_API_URL", DEFAULT_RUNPOD_REST_URL)
    object_store: VolumeObjectStore
    if not access_key or not secret_key:
        # Pod logs need only the RunPod key; object operations refuse until S3 is configured.
        object_store = _MissingObjectStore()
    elif api_key in {access_key, secret_key}:
        raise VolumeFileConfigurationError(
            "S3 credentials must not reuse the control-plane credential value"
        )
    else:
        object_store = NetworkVolumeObjectStore(
            NetworkVolumeClient(
                # S3 is the only path used by this adapter.  Passing explicit
                # values prevents the legacy AWS environment fallback from silently
                # sharing unrelated credentials.
                api_key="",
                s3_access_key=access_key,
                s3_secret_key=secret_key,
                timeout_s=configured_limits.timeout_s,
                resolve_env_credentials=False,
            )
        )
    pod_logs = BoundedPodLogClient(
        api_key=api_key,
        rest_base_url=base_url,
        timeout_s=configured_limits.timeout_s,
    )
    if audit_pool is not None and mutation_journal is not None:
        raise ValueError("configure audit_pool or mutation_journal, not both")
    journal = mutation_journal
    if journal is None and audit_pool is not None:
        journal = PostgresVolumeFileMutationJournal(audit_pool, actor=audit_actor)
    return VolumeFileService(
        object_store,
        pod_logs,
        limits=configured_limits,
        mutation_journal=journal,
    )


def _provider_target(volume_id: str, data_center_id: str, object_key: str) -> str:
    return f"runpod:{data_center_id}/{volume_id}/{object_key}"


def _request_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _audit_result(result: VolumeFileResult) -> dict[str, object]:
    """Keep replay evidence complete without persisting local paths or content."""
    payload = result.to_dict()
    payload["content_base64"] = None
    if result.volume_id and result.data_center_id and result.object_key:
        payload["target"] = _provider_target(
            result.volume_id,
            result.data_center_id,
            result.object_key,
        )
    return payload


def _result_from_audit(value: object) -> VolumeFileResult:
    if not isinstance(value, Mapping):
        raise VolumeFileAuditUnavailable("malformed volume-file audit result")
    operation_value = value.get("operation")
    status_value = value.get("status")
    if operation_value not in {"upload", "delete"} or status_value != "completed":
        raise VolumeFileAuditUnavailable("malformed volume-file audit result")
    progress_value = value.get("progress", [])
    if not isinstance(progress_value, list):
        raise VolumeFileAuditUnavailable("malformed volume-file audit progress")
    progress: list[VolumeFileProgress] = []
    for raw in progress_value:
        if not isinstance(raw, Mapping):
            raise VolumeFileAuditUnavailable("malformed volume-file audit progress")
        progress_operation = raw.get("operation")
        if progress_operation not in {"list", "upload", "download", "delete", "logs"}:
            raise VolumeFileAuditUnavailable("malformed volume-file audit progress")
        progress.append(
            VolumeFileProgress(
                sequence=_audit_int(raw.get("sequence")),
                operation=cast(Operation, progress_operation),
                phase=_audit_str(raw.get("phase")),
                bytes_completed=_audit_int(raw.get("bytes_completed")),
                total_bytes=_audit_optional_int(raw.get("total_bytes")),
            )
        )
    return VolumeFileResult(
        operation=cast(Operation, operation_value),
        status="completed",
        volume_id=_audit_optional_str(value.get("volume_id")),
        data_center_id=_audit_optional_str(value.get("data_center_id")),
        object_key=_audit_optional_str(value.get("object_key")),
        bytes_transferred=_audit_int(value.get("bytes_transferred")),
        checksum_sha256=_audit_optional_str(value.get("checksum_sha256")),
        progress=tuple(progress),
        target=_audit_optional_str(value.get("target")),
        effect=_audit_optional_str(value.get("effect")),
        estimated_ceiling=_audit_optional_str(value.get("estimated_ceiling")),
        irreversible=_audit_bool(value.get("irreversible")),
        consequences=_audit_optional_str(value.get("consequences")),
    )


def _audit_str(value: object) -> str:
    if not isinstance(value, str):
        raise VolumeFileAuditUnavailable("malformed volume-file audit value")
    return value


def _audit_optional_str(value: object) -> str | None:
    if value is None:
        return None
    return _audit_str(value)


def _audit_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VolumeFileAuditUnavailable("malformed volume-file audit value")
    return value


def _audit_optional_int(value: object) -> int | None:
    if value is None:
        return None
    return _audit_int(value)


def _audit_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise VolumeFileAuditUnavailable("malformed volume-file audit value")
    return value


def _bounded_int(name: str, value: object, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _validate_volume_context(volume_id: str, data_center_id: str) -> None:
    if not _ID_RE.fullmatch(volume_id):
        raise VolumeFileValidationError("volume_id is invalid")
    if not _DATA_CENTER_RE.fullmatch(data_center_id):
        raise VolumeFileValidationError("data_center_id is invalid")


def _validate_object_key(value: str) -> str:
    return _validate_key(value, allow_empty=False, allow_trailing_slash=False)


def _validate_prefix(value: str) -> str:
    return _validate_key(value, allow_empty=True, allow_trailing_slash=True)


def _validate_key(value: str, *, allow_empty: bool, allow_trailing_slash: bool) -> str:
    if not isinstance(value, str) or "\x00" in value or "\\" in value:
        raise VolumeFileValidationError("object key is invalid")
    if len(value.encode("utf-8")) > 1024:
        raise VolumeFileValidationError("object key is too long")
    if not value:
        if allow_empty:
            return value
        raise VolumeFileValidationError("object key is required")
    if value.startswith("/") or value.startswith("~"):
        raise VolumeFileValidationError("object key must be relative")
    parts = value.split("/")
    if allow_trailing_slash and parts[-1] == "":
        parts = parts[:-1]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise VolumeFileValidationError("object key contains an unsafe path component")
    if any(
        any(ord(character) < 32 or ord(character) == 127 for character in part) for part in parts
    ):
        raise VolumeFileValidationError("object key contains control characters")
    return value


def _validate_idempotency_key(value: str | None) -> None:
    if value is None:
        return
    if not _ID_RE.fullmatch(value):
        raise VolumeFileValidationError("idempotency_key is invalid")


def _normalize_checksum(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _CHECKSUM_RE.fullmatch(value.lower()):
        raise VolumeFileValidationError("expected_sha256 must be a lowercase hexadecimal SHA-256")
    return value.lower()


def _local_relative_parts(relative: str) -> tuple[str, ...]:
    if not isinstance(relative, str) or not relative or "\x00" in relative or "\\" in relative:
        raise VolumeFileValidationError("local path is invalid")
    raw = PurePath(relative)
    if raw.is_absolute() or any(part in {"", ".", ".."} for part in raw.parts):
        raise VolumeFileValidationError("local path must be a relative non-traversal path")
    return raw.parts


def _directory_open_flags() -> int:
    nofollow = getattr(os, "O_NOFOLLOW", None)
    directory = getattr(os, "O_DIRECTORY", None)
    if not isinstance(nofollow, int) or not isinstance(directory, int):
        raise VolumeFileValidationError(
            "safe local file operations are unavailable on this platform"
        )
    return os.O_RDONLY | nofollow | directory


def _nofollow_file_flags(base_flags: int) -> int:
    return base_flags | (_directory_open_flags() & ~os.O_DIRECTORY)


def _open_safe_parent(root: Path, relative: str) -> _SafeLocalTarget:
    """Open every path component beneath ``root`` without following symlinks.

    Keeping the final parent descriptor open makes subsequent source reads and
    temporary-file publication immune to a rename or symlink substitution of a
    textual parent path after validation.
    """
    parts = _local_relative_parts(relative)
    current_fd: int | None = None
    try:
        current_fd = os.open(root, _directory_open_flags())
    except OSError as exc:
        raise VolumeFileValidationError("local root must be a non-symlink directory") from exc
    try:
        for part in parts[:-1]:
            next_fd = os.open(part, _directory_open_flags(), dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
    except OSError as exc:
        if current_fd is not None:
            os.close(current_fd)
        raise VolumeFileValidationError("local path contains an unsafe directory") from exc
    assert current_fd is not None
    return _SafeLocalTarget(parent_fd=current_fd, name=parts[-1])


def _open_regular_readonly(root: Path, relative: str) -> int:
    target = _open_safe_parent(root, relative)
    try:
        # O_NONBLOCK ensures a raced-in FIFO/device cannot stall an operator
        # action before fstat rejects the non-regular descriptor below.
        fd = os.open(
            target.name,
            _nofollow_file_flags(os.O_RDONLY | os.O_NONBLOCK),
            dir_fd=target.parent_fd,
        )
        try:
            _regular_file_size(fd)
            return fd
        except Exception:  # reason: close the descriptor after any validation failure
            os.close(fd)
            raise
    except OSError as exc:
        raise VolumeFileValidationError("local source cannot be opened safely") from exc
    finally:
        os.close(target.parent_fd)


def _open_safe_destination(root: Path, relative: str, *, overwrite: bool) -> _SafeLocalTarget:
    target = _open_safe_parent(root, relative)
    try:
        try:
            file_stat = os.stat(target.name, dir_fd=target.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return target
        if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
            raise VolumeFileValidationError("local destination must be a regular non-symlink file")
        if not overwrite:
            raise VolumeFileConfirmationRequired(
                "destination overwrite requires explicit confirmation"
            )
        return target
    except Exception:  # reason: close the parent descriptor after any validation failure
        os.close(target.parent_fd)
        raise


def _regular_file_size(fd: int) -> int:
    file_stat = os.fstat(fd)
    if not stat.S_ISREG(file_stat.st_mode):
        raise VolumeFileValidationError("local source must be a regular file")
    return file_stat.st_size


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("could not write download temp file")
        view = view[written:]


def _open_temp_file(target: _SafeLocalTarget) -> tuple[int, str]:
    flags = _nofollow_file_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    for _ in range(16):
        name = f"{_TEMP_PREFIX}{secrets.token_hex(16)}.part"
        try:
            fd = os.open(name, flags, 0o600, dir_fd=target.parent_fd)
        except FileExistsError:
            continue
        except OSError as exc:
            raise VolumeFileValidationError("local download destination is unavailable") from exc
        try:
            os.fchmod(fd, 0o600)
        except OSError as exc:
            os.close(fd)
            raise VolumeFileValidationError("local download destination is unavailable") from exc
        return fd, name
    raise VolumeFileValidationError("could not allocate a safe local download temp file")


def _replace_temp(temp_name: str, target: _SafeLocalTarget) -> None:
    try:
        os.replace(
            temp_name,
            target.name,
            src_dir_fd=target.parent_fd,
            dst_dir_fd=target.parent_fd,
        )
    except OSError as exc:
        raise VolumeFileValidationError("local download destination is unavailable") from exc


def _publish_no_overwrite(temp_name: str, target: _SafeLocalTarget) -> None:
    try:
        os.link(
            temp_name,
            target.name,
            src_dir_fd=target.parent_fd,
            dst_dir_fd=target.parent_fd,
            follow_symlinks=False,
        )
    except FileExistsError as exc:
        raise VolumeFileConfirmationRequired(
            "destination overwrite requires explicit confirmation"
        ) from exc
    except OSError as exc:
        if exc.errno not in {
            errno.EPERM,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
            errno.EXDEV,
            errno.EMLINK,
        }:
            raise VolumeFileValidationError("local download destination is unavailable") from exc
        # Filesystem without hard links: check-then-replace is the best
        # available no-overwrite publish. The window between the stat and the
        # replace is documented in docs/operator/runpod-volume-files.md.
        try:
            os.stat(target.name, dir_fd=target.parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            _replace_temp(temp_name, target)
            return
        except OSError as stat_exc:
            raise VolumeFileValidationError(
                "local download destination is unavailable"
            ) from stat_exc
        raise VolumeFileConfirmationRequired(
            "destination overwrite requires explicit confirmation"
        ) from exc
    try:
        os.unlink(temp_name, dir_fd=target.parent_fd)
    except OSError:
        log.warning("published download left a temporary file behind")


def _unlink_if_exists(name: str, parent_fd: int) -> None:
    try:
        os.unlink(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    except OSError:
        log.warning("bounded RunPod local partial cleanup failed")


async def _maybe_aclose(value: object) -> None:
    closer = getattr(value, "aclose", None)
    if callable(closer):
        result = closer()
        if result is not None:
            await result


async def _get_object_range(
    store: VolumeObjectStore,
    volume_id: str,
    data_center_id: str,
    key: str,
    *,
    offset: int,
    max_bytes: int,
) -> bytes:
    return await store.get_object_range(
        volume_id,
        data_center_id,
        key,
        offset=offset,
        max_bytes=max_bytes,
    )


def _parse_log_lines(text: str, *, max_lines: int) -> tuple[tuple[PodLogLine, ...], bool]:
    records: list[object]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        records = list(redact_text(text).splitlines())
    else:
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            candidate = _first_present(parsed, ("logs", "data"))
            if isinstance(candidate, list):
                records = candidate
            elif isinstance(candidate, str):
                records = list(candidate.splitlines())
            else:
                records = [redact_text(text)]
        elif isinstance(parsed, str):
            records = list(parsed.splitlines())
        else:
            records = [redact_text(text)]
    truncated = len(records) > max_lines
    retained = records[-max_lines:]
    lines = tuple(_log_line(index, item) for index, item in enumerate(retained, start=1))
    return lines, truncated


_ABSENT = object()


def _first_present(mapping: Mapping[str, object], keys: tuple[str, ...]) -> object:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return _ABSENT


def _log_line(sequence: int, item: object) -> PodLogLine:
    if isinstance(item, Mapping):
        timestamp_value = _first_present(item, ("timestamp", "time", "createdAt"))
        message_value = _first_present(item, ("message", "log", "text"))
        timestamp = (
            redact_text(str(timestamp_value))
            if timestamp_value is not _ABSENT and timestamp_value is not None
            else None
        )
        message = (
            str(message_value)
            if message_value is not _ABSENT and message_value is not None
            else json.dumps(item, sort_keys=True)
        )
        return PodLogLine(sequence=sequence, timestamp=timestamp, text=redact_text(message))
    return PodLogLine(sequence=sequence, text=redact_text(str(item)))


__all__ = [
    "NetworkVolumeObjectStore",
    "PodLogLine",
    "PodLogReader",
    "PostgresVolumeFileMutationJournal",
    "S3CredentialReferences",
    "VolumeFileAuditError",
    "VolumeFileAuditUnavailable",
    "VolumeFileChecksumMismatch",
    "VolumeFileConfigurationError",
    "VolumeFileConfirmationRequired",
    "VolumeFileError",
    "VolumeFileIdempotencyConflict",
    "VolumeFileLimitExceeded",
    "VolumeFileLimits",
    "VolumeFileMutationAudit",
    "VolumeFileMutationJournal",
    "VolumeFileNotFound",
    "VolumeFilePreSpendRejected",
    "VolumeFileProgress",
    "VolumeFileProviderError",
    "VolumeFileResult",
    "VolumeFileService",
    "VolumeFileTimeout",
    "VolumeFileValidationError",
    "VolumeObject",
    "VolumeObjectPage",
    "VolumeObjectStore",
    "build_configured_volume_file_service",
]
