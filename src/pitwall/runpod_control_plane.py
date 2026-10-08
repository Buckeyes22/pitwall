"""Typed operator service for RunPod account control-plane resources.

This module is the one policy boundary above the strict RunPod clients.  It
keeps raw resource controls separate from broker-managed lease/serve flows,
validates identifiers and mutation intent before provider I/O, bounds calls,
redacts failures, records successful writes, and exposes sanitized results to
REST, MCP, CLI, and TUI adapters.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol, TypeVar, cast

import httpx
from pydantic import (
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from pitwall.core.models import CredentialReferenceName, PitwallModel
from pitwall.db.repository import insert_audit
from pitwall.runpod_client.gpu import validate_canonical_gpu_names
from pitwall.runpod_client.mounts import NetworkVolume, NetworkVolumeClient
from pitwall.runpod_client.pods import (
    RunPodError,
    RunPodRestError,
    create_pod_with_fallback,
    get_pod_strict,
    get_pods,
    reset_pod,
    restart_pod,
    start_pod,
    stop_pod,
    terminate_pod,
    update_pod,
)
from pitwall.runpod_client.registry import (
    ContainerRegistryAuth,
    RegistryAuthError,
    create_container_registry_auth,
    delete_container_registry_auth,
    get_container_registry_auth,
    list_container_registry_auths,
)
from pitwall.runpod_client.serverless import (
    Endpoint,
    EndpointScalingConfig,
    create_endpoint,
    delete_endpoint,
    get_endpoint,
    list_endpoints,
    resolve_gpu_selection_for_types,
    update_endpoint_scaling,
)
from pitwall.runpod_client.templates import (
    HubTemplate,
    Template,
    TemplateNotFoundError,
    create_template_rest,
    delete_template,
    get_hub_template,
    get_template,
    list_account_templates,
    list_hub_templates,
    non_secret_env_keys,
    update_template,
)
from pitwall.runpod_client.workloads import WorkloadConfig
from pitwall.runpod_credentials import resolve_runpod_api_key
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendInspectionService,
    get_pre_spend_inspection_service,
)
from pitwall.security.redaction import redact_text

_T = TypeVar("_T")
log = logging.getLogger(__name__)
RunPodActor = Literal["rest:admin", "mcp:admin", "mcp", "system"]
MutationIntent = Literal["preview", "apply"]
PodAction = Literal["start", "stop", "restart", "reset"]
EndpointType = Literal["QUEUE", "LOAD_BALANCER"]
MutationAction = Literal["create", "update", "delete"]

_RESOURCE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_IDEMPOTENCY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$"
_SAFE_NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,127}$"
_RESOURCE_ID_RE = re.compile(_RESOURCE_ID_PATTERN)
_SAFE_AUDIT_ACTORS = frozenset({"rest:admin", "mcp:admin", "mcp", "system"})

#: Longest ``timeout_s`` the service accepts, per operation, in seconds. ``default`` covers every
#: single-request operation (list, get, update, action, terminate, endpoint, template, volume, and
#: registry calls); 300 s is far above their normal latency. ``pod.create`` is ``None``: a create
#: can legitimately outlast any fixed ceiling because it walks GPU and cloud fallbacks, and
#: cancelling it mid-flight can orphan a paid pod. The backend's per-attempt timeouts bound it.
OPERATION_TIMEOUT_CEILING_S: Mapping[str, float | None] = {
    "default": 300.0,
    "pod.create": None,
}
_DEFAULT_TIMEOUT_CEILING_S = 300.0
_SERVICE_TIMEOUT: Any = object()

#: Settling margin before an unknown outcome is acted on: the default per-call ceiling (300 s)
#: plus 60 s. Onboarding's creates (templates, endpoints, volumes, registry auth) run under
#: that ceiling, so after it their outcome has settled and resume may release the key.
#: ``pod.create`` has no ceiling (``OPERATION_TIMEOUT_CEILING_S``): what keeps the
#: orphaned-workload reaper off a running pod create is the key's advisory lock, which the
#: create holds until it returns. For the reaper this is only a settling margin, checked on
#: both of its reads: it leaves a just-completed create to the tool's own lease insert and
#: gives a just-created pod time to appear in RunPod's list. A same-key replay can still
#: insert its lease late; the tool then returns a lease the reaper recorded first.
UNKNOWN_OUTCOME_GRACE_S = _DEFAULT_TIMEOUT_CEILING_S + 60.0

_JOURNAL_KIND = "runpod_control_plane_mutation"


def journal_lock_name(idempotency_key: str) -> str:
    """The advisory-lock name an applied mutation holds for its key while it runs."""
    return f"{_JOURNAL_KIND}:{idempotency_key}"


#: Environment variable every journaled pod create sets on the pod it creates, so a pod found
#: later can be proven to be that attempt's (``create_attempt_marker``).
CREATE_ATTEMPT_ENV = "PITWALL_CREATE_ATTEMPT"


def create_attempt_marker(idempotency_key: str) -> str:
    """The non-secret marker a pod create with *idempotency_key* puts in the pod's env.

    A digest of the key the journal row identifies, so the key itself never lands in the
    pod's environment. The orphaned-workload reaper adopts a found pod only when RunPod
    returns this value in the pod's ``env``.
    """
    digest = hashlib.sha256(b"pitwall-create-attempt-v1:" + idempotency_key.encode("utf-8"))
    return digest.hexdigest()[:32]


class RunPodControlPlaneError(RuntimeError):
    """Stable, redacted service failure safe for operator transports."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        operation: str,
        resource_type: str,
        resource_id: str | None = None,
        retryable: bool = False,
        provider_status: int | None = None,
        changed: bool = False,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.operation = operation
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.retryable = retryable
        self.provider_status = provider_status
        self.changed = changed

    def to_dict(self) -> dict[str, object]:
        return {
            "error": self.code,
            "detail": self.detail,
            "operation": self.operation,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "retryable": self.retryable,
            "provider_status": self.provider_status,
            "changed": self.changed,
        }


class MutationRequest(PitwallModel):
    """Explicit intent fields required by every direct resource mutation."""

    model_config = ConfigDict(hide_input_in_errors=True)

    intent: MutationIntent
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=_IDEMPOTENCY_PATTERN)

    @property
    def dry_run(self) -> bool:
        return self.intent == "preview"


class IdentifiedMutationRequest(MutationRequest):
    resource_id: str = Field(min_length=1, max_length=128, pattern=_RESOURCE_ID_PATTERN)


class PodCreateRequest(MutationRequest):
    name: str = Field(min_length=1, max_length=128, pattern=_SAFE_NAME_PATTERN)
    image: str = Field(min_length=1, max_length=512)
    gpu_type_ids: list[str] = Field(min_length=1, max_length=16)
    gpu_count: int = Field(default=1, ge=1, le=8)
    template_id: str | None = Field(default=None, pattern=_RESOURCE_ID_PATTERN)
    disk_gb: int = Field(default=50, ge=10, le=4096)
    cloud: Literal["ALL", "COMMUNITY", "SECURE"] = "ALL"
    data_center_id: str | None = Field(default=None, pattern=_RESOURCE_ID_PATTERN)
    network_volume_id: str | None = Field(default=None, pattern=_RESOURCE_ID_PATTERN)
    ports: list[str] = Field(default_factory=list, max_length=32)
    env: dict[str, str] = Field(default_factory=dict)
    args: list[str] = Field(default_factory=list, max_length=256)
    registry_auth_id: str | None = Field(default=None, pattern=_RESOURCE_ID_PATTERN)
    max_cost_per_hour: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=6)
    ttl_minutes: int = Field(
        ge=1,
        le=10080,
        description="Lease TTL in minutes; controls budget reservation and auto-teardown via the lease reconciler",
    )

    @field_validator("gpu_type_ids")
    @classmethod
    def _unique_gpu_types(cls, value: list[str]) -> list[str]:
        normalized = validate_canonical_gpu_names(value)
        if len(set(normalized)) != len(normalized):
            raise ValueError("gpu_type_ids must not contain duplicates")
        return normalized


class PodUpdateRequest(IdentifiedMutationRequest):
    env: dict[str, str] | None = None
    ports: list[str] | None = Field(default=None, max_length=32)
    registry_auth_id: str | None = Field(default=None, pattern=_RESOURCE_ID_PATTERN)

    @model_validator(mode="after")
    def _requires_change(self) -> PodUpdateRequest:
        if self.env is None and self.ports is None and self.registry_auth_id is None:
            raise ValueError("pod update requires env, ports, or registry_auth_id")
        return self


class PodActionRequest(IdentifiedMutationRequest):
    action: PodAction


class EndpointWorkersRequest(PitwallModel):
    minimum: int = Field(default=0, ge=0, le=100)
    maximum: int = Field(default=3, ge=1, le=100)
    idle_timeout_seconds: int = Field(default=60, ge=1, le=3600)

    @model_validator(mode="after")
    def _ordered(self) -> EndpointWorkersRequest:
        if self.minimum > self.maximum:
            raise ValueError("worker minimum must not exceed maximum")
        return self


class EndpointScalingRequest(PitwallModel):
    type: Literal["QUEUE_DELAY", "REQUEST_COUNT"] = "QUEUE_DELAY"
    value: float = Field(default=4.0, gt=0)

    @model_validator(mode="after")
    def _valid_value(self) -> EndpointScalingRequest:
        if self.type == "QUEUE_DELAY" and self.value < 0.5:
            raise ValueError("QUEUE_DELAY value must be at least 0.5")
        if self.type == "REQUEST_COUNT" and not self.value.is_integer():
            raise ValueError("REQUEST_COUNT value must be an integer")
        return self


class EndpointGpuRequest(PitwallModel):
    pools: list[str] = Field(min_length=1, max_length=16)
    excluded_type_ids: list[str] = Field(default_factory=list, max_length=128)
    count: int = Field(default=1, ge=1, le=8)


class EndpointCreateRequest(MutationRequest):
    name: str = Field(min_length=1, max_length=128, pattern=_SAFE_NAME_PATTERN)
    endpoint_type: EndpointType = "QUEUE"
    template_id: str | None = Field(default=None, pattern=_RESOURCE_ID_PATTERN)
    image: str | None = Field(default=None, min_length=1, max_length=512)
    workers: EndpointWorkersRequest = Field(default_factory=EndpointWorkersRequest)
    scaling: EndpointScalingRequest = Field(default_factory=EndpointScalingRequest)
    gpu: EndpointGpuRequest
    flashboot: bool = False

    @model_validator(mode="after")
    def _valid_source_and_scaler(self) -> EndpointCreateRequest:
        if self.template_id is None and self.image is None:
            raise ValueError("endpoint create requires template_id or image")
        if self.endpoint_type == "LOAD_BALANCER" and self.scaling.type != "REQUEST_COUNT":
            raise ValueError("LOAD_BALANCER endpoints require REQUEST_COUNT scaling")
        return self


class EndpointUpdateRequest(IdentifiedMutationRequest):
    workers: EndpointWorkersRequest
    scaling: EndpointScalingRequest
    flashboot: bool = False
    gpu: EndpointGpuRequest | None = None


class TemplateCreateRequest(MutationRequest):
    name: str = Field(min_length=1, max_length=128, pattern=_SAFE_NAME_PATTERN)
    image: str = Field(min_length=1, max_length=512)
    disk_gb: int = Field(default=50, ge=10, le=4096)
    volume_gb: int = Field(default=0, ge=0, le=4096)
    volume_mount_path: str | None = Field(default=None, min_length=1, max_length=512)
    args: list[str] = Field(default_factory=list, max_length=256)
    env: dict[str, str] = Field(default_factory=dict)
    serverless: bool = False
    registry_auth_id: str | None = Field(default=None, pattern=_RESOURCE_ID_PATTERN)
    ports: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def _valid_mount(self) -> TemplateCreateRequest:
        if self.volume_gb and self.volume_gb < 10:
            raise ValueError("template volume_gb must be zero or at least 10")
        if self.volume_mount_path is not None and not self.volume_gb:
            raise ValueError("volume_mount_path requires volume_gb")
        return self


class TemplateUpdateRequest(IdentifiedMutationRequest):
    name: str | None = Field(default=None, min_length=1, max_length=128, pattern=_SAFE_NAME_PATTERN)
    image: str | None = Field(default=None, min_length=1, max_length=512)
    args: str | None = Field(default=None, max_length=4096)
    disk_gb: int | None = Field(default=None, ge=10, le=4096)
    volume_gb: int | None = Field(default=None, ge=10, le=4096)
    volume_mount_path: str | None = Field(default=None, min_length=1, max_length=512)
    ports: list[str] | None = Field(default=None, max_length=32)
    env: dict[str, str] | None = None
    serverless: bool | None = None

    @model_validator(mode="after")
    def _requires_change(self) -> TemplateUpdateRequest:
        changed = (
            self.name,
            self.image,
            self.args,
            self.disk_gb,
            self.volume_gb,
            self.volume_mount_path,
            self.ports,
            self.env,
            self.serverless,
        )
        if all(value is None for value in changed):
            raise ValueError("template update requires at least one field")
        return self


class VolumeCreateRequest(MutationRequest):
    name: str = Field(min_length=1, max_length=128, pattern=_SAFE_NAME_PATTERN)
    size_gb: int = Field(ge=10, le=4096)
    data_center_id: str = Field(pattern=_RESOURCE_ID_PATTERN)


class VolumeGrowRequest(IdentifiedMutationRequest):
    size_gb: int = Field(ge=10, le=4096)


class RegistryAuthCreateRequest(MutationRequest):
    name: str = Field(min_length=1, max_length=128, pattern=_SAFE_NAME_PATTERN)
    username: str = Field(min_length=1, max_length=256)
    password_env: CredentialReferenceName


class RegistryAuthReplaceRequest(IdentifiedMutationRequest):
    name: str = Field(min_length=1, max_length=128, pattern=_SAFE_NAME_PATTERN)
    username: str = Field(min_length=1, max_length=256)
    password_env: CredentialReferenceName


class PodResource(PitwallModel):
    id: str
    name: str
    status: str
    image: str | None = None
    gpu_type_id: str | None = None
    gpu_count: int | None = None
    cost_per_hour: Decimal | None = None
    uptime_seconds: int | None = None
    public_ip: str | None = None
    port_mappings: dict[str, int] = Field(default_factory=dict)


class EndpointResource(PitwallModel):
    id: str
    name: str
    endpoint_type: str | None = None
    workers: EndpointWorkersRequest
    scaling: EndpointScalingRequest
    flashboot: bool
    template_id: str | None = None
    image: str | None = None
    gpu_pools: tuple[str, ...] = ()
    excluded_gpu_type_ids: tuple[str, ...] = ()
    gpu_count: int | None = None
    created_at: str | None = None


class TemplateResource(PitwallModel):
    id: str
    name: str
    image: str
    docker_args_fingerprint: str | None = Field(default=None, exclude=True, repr=False)
    env_fingerprint: str | None = Field(default=None, exclude=True, repr=False)
    disk_gb: int
    volume_gb: int
    volume_mount_path: str | None = None
    ports: tuple[str, ...] = ()
    env_keys: tuple[str, ...] = ()
    serverless: bool
    public: bool
    registry_auth_id: str | None = None


class HubTemplateResource(PitwallModel):
    id: str
    name: str
    display_name: str | None = None
    image: str
    description: str | None = None
    github_url: str | None = None
    serverless: bool
    env_keys: tuple[str, ...] = ()


class VolumeResource(PitwallModel):
    id: str
    name: str
    size_gb: int
    data_center_id: str
    type: str | None = None


class RegistryAuthResource(PitwallModel):
    id: str
    name: str


class MutationResult(PitwallModel):
    provider: Literal["runpod"] = "runpod"
    operation: str
    resource_type: str
    resource_id: str | None
    dry_run: bool
    changed: bool
    already_absent: bool = False
    effect: str
    estimated_ceiling: str | None = None
    irreversible: bool = False
    idempotency_key: str
    resource: dict[str, object] | None = None
    replayed: bool = Field(
        default=False,
        description=(
            "True when this result is the stored outcome of an earlier apply with the same "
            "idempotency_key and request; RunPod was not called again."
        ),
    )


@dataclass(frozen=True, slots=True)
class ControlPlaneMutationAudit:
    """Credential-free journal metadata for one applied control-plane mutation."""

    operation: str
    resource_type: str
    entity_type: str
    action: MutationAction
    resource_id: str | None
    idempotency_key: str
    request_hash: str
    #: Non-secret request fields a reconciler needs to resolve an unknown outcome
    #: (``_recovery_fields``); ``None`` for every operation but ``pod.create``.
    recovery: Mapping[str, object] | None = None


class RunPodMutationJournal(Protocol):
    """Durable exact-key journal that makes an applied mutation replay instead of repeat."""

    async def run_idempotent[Context](
        self,
        audit: ControlPlaneMutationAudit,
        validate: Callable[[], Awaitable[Context]],
        apply: Callable[[Context, Any], Awaitable[MutationResult]],
    ) -> MutationResult:
        """Serialize one key; replay or refuse a known key, else validate, apply once, record.

        ``validate`` runs the read-only preconditions under the key lock before any journal
        write, so a failed read leaves no ``started`` row. ``apply`` receives its result and
        the journal's database connection, on which the mutation writes its own audit rows.
        """

    async def release(self, idempotency_key: str, *, reason: str) -> bool:
        """Append ``compensated`` so the key applies again; ``False`` if unused or released."""

    async def latest(self, idempotency_key: str) -> JournalEntry | None:
        """The key's newest journal entry, or ``None`` when the key is unused."""


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """The newest journal row for one idempotency key, as a caller may act on it."""

    state: str
    operation: str
    resource_id: str | None
    age_s: float
    #: Age of the key's newest ``started`` row: when the latest attempt began. ``None`` when
    #: the key has no ``started`` row.
    attempt_age_s: float | None = None


class _ApplyFailure(Exception):
    """Carries an unexpected callback failure past the journal's database-error mapping."""

    def __init__(self, error: Exception) -> None:
        super().__init__(type(error).__name__)
        self.error = error


@dataclass(frozen=True, slots=True)
class _JournalRow:
    payload: Mapping[str, object]
    entity_type: str
    entity_id: str
    action: MutationAction
    age_s: float


class PostgresRunPodMutationJournal:
    """Control-plane mutation journal on the append-only ``pitwall.config_audit`` table.

    Mirrors ``PostgresVolumeFileMutationJournal``: rows tagged with a ``kind`` and keyed by
    the idempotency key hold the request hash, a state, and the stored result, and a
    per-key session advisory lock serializes equal keys across the provider call. States:
    ``started`` (written after the read-only preconditions, before the provider write),
    ``completed`` (with the result), ``failed`` (the attempt definitely changed nothing),
    and ``compensated`` (the caller undid what the key created). ``failed`` and
    ``compensated`` release the key; a ``started`` row with no later row is an unknown
    outcome and is never re-applied.
    """

    _KIND = _JOURNAL_KIND

    def __init__(self, pool: Any, *, actor: RunPodActor, lock_timeout_s: float = 60.0) -> None:
        if not lock_timeout_s > 0:
            raise ValueError("lock_timeout_s must be greater than zero")
        self._pool = pool
        self._actor = actor
        self._lock_timeout_ms = max(1, int(lock_timeout_s * 1000))

    async def run_idempotent[Context](
        self,
        audit: ControlPlaneMutationAudit,
        validate: Callable[[], Awaitable[Context]],
        apply: Callable[[Context, Any], Awaitable[MutationResult]],
    ) -> MutationResult:
        lock_name = journal_lock_name(audit.idempotency_key)
        try:
            async with self._pool.acquire() as conn:
                await self._lock(conn, lock_name, audit.operation, audit.resource_type)
                try:
                    async with conn.transaction():
                        row = await self._load(conn, audit.idempotency_key)
                    if row is not None:
                        replayed = _journal_replay(audit, row.payload)
                        if replayed is not None:
                            return replayed
                    # Read-only preconditions run under the lock before any journal write:
                    # a failed read changed nothing and leaves the key free.
                    try:
                        context = await validate()
                    except RunPodControlPlaneError:
                        raise
                    except Exception as exc:  # reason: an unknown failure keeps its type
                        raise _ApplyFailure(exc) from exc
                    async with conn.transaction():
                        await self._insert(conn, audit, state="started")
                    # The session lock serializes equal keys across this provider I/O, while
                    # the started row is already committed.
                    try:
                        result = await apply(context, conn)
                    except RunPodControlPlaneError as exc:
                        if _outcome_unchanged(exc, audit.operation):
                            await self._record_failure(conn, audit, exc)
                        raise
                    except Exception as exc:  # reason: an unknown failure keeps its type and leaves the outcome ambiguous
                        raise _ApplyFailure(exc) from exc
                    try:
                        async with conn.transaction():
                            await self._insert(conn, audit, state="completed", result=result)
                    except (
                        Exception
                    ) as exc:  # reason: completion must be durable after a provider change
                        log.warning(
                            "RunPod control-plane journal completion failed (%s)",
                            type(exc).__name__,
                        )
                        raise RunPodControlPlaneError(
                            "audit_write_failed",
                            "RunPod mutation completed but its idempotency record was not written",
                            operation=audit.operation,
                            resource_type=audit.resource_type,
                            resource_id=result.resource_id,
                            changed=result.changed,
                        ) from exc
                    return result
                finally:
                    await _advisory_unlock(conn, lock_name)
        except _ApplyFailure as exc:
            raise exc.error from exc.error.__cause__
        except RunPodControlPlaneError:
            raise
        except Exception as exc:  # reason: database detail may contain operator input
            log.warning("RunPod control-plane journal failed (%s)", type(exc).__name__)
            raise RunPodControlPlaneError(
                "audit_unavailable",
                "durable mutation journal is unavailable",
                operation=audit.operation,
                resource_type=audit.resource_type,
                resource_id=audit.resource_id,
                retryable=True,
            ) from exc

    async def release(self, idempotency_key: str, *, reason: str) -> bool:
        lock_name = journal_lock_name(idempotency_key)
        try:
            async with self._pool.acquire() as conn:
                await self._lock(conn, lock_name, "release", "mutation")
                try:
                    async with conn.transaction():
                        row = await self._load(conn, idempotency_key)
                        if row is None or row.payload.get("state") == "compensated":
                            return False
                        payload = dict(row.payload)
                        payload.update(
                            {"state": "compensated", "result": None, "outcome": reason[:200]}
                        )
                        await insert_audit(
                            self._pool,
                            actor=self._actor,
                            action=row.action,
                            entity_type=row.entity_type,
                            entity_id=row.entity_id,
                            new_value=cast(Any, payload),
                            change_reason=f"runpod control-plane {payload.get('operation')} compensated",
                            conn=conn,
                        )
                    return True
                finally:
                    await _advisory_unlock(conn, lock_name)
        except RunPodControlPlaneError:
            raise
        except Exception as exc:  # reason: database detail may contain operator input
            log.warning("RunPod control-plane journal release failed (%s)", type(exc).__name__)
            raise RunPodControlPlaneError(
                "audit_unavailable",
                "durable mutation journal is unavailable",
                operation="release",
                resource_type="mutation",
                retryable=True,
            ) from exc

    async def latest(self, idempotency_key: str) -> JournalEntry | None:
        try:
            async with self._pool.acquire() as conn:
                row = await self._load(conn, idempotency_key)
                attempt_age = (
                    await conn.fetchval(_ATTEMPT_AGE_SQL, idempotency_key)
                    if row is not None
                    else None
                )
        except Exception as exc:  # reason: database detail may contain operator input
            log.warning("RunPod control-plane journal read failed (%s)", type(exc).__name__)
            raise RunPodControlPlaneError(
                "audit_unavailable",
                "durable mutation journal is unavailable",
                operation="journal.read",
                resource_type="mutation",
                retryable=True,
            ) from exc
        if row is None:
            return None
        result = row.payload.get("result")
        result_id = result.get("resource_id") if isinstance(result, Mapping) else None
        resource_id = result_id if result_id is not None else row.payload.get("resource_id")
        return JournalEntry(
            state=str(row.payload.get("state")),
            operation=str(row.payload.get("operation")),
            resource_id=str(resource_id) if resource_id is not None else None,
            age_s=row.age_s,
            attempt_age_s=float(attempt_age) if attempt_age is not None else None,
        )

    async def _lock(self, conn: Any, lock_name: str, operation: str, resource_type: str) -> None:
        """Take the per-key session lock, waiting at most the configured lock timeout."""
        try:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL lock_timeout = '{self._lock_timeout_ms}ms'")
                await conn.execute("SELECT pg_advisory_lock(hashtextextended($1, 0))", lock_name)
        except Exception as exc:  # reason: only a lock timeout is mapped; the rest propagate
            if getattr(exc, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
                raise
            raise RunPodControlPlaneError(
                "mutation_in_progress",
                "another request with this idempotency_key is still in progress; retry later",
                operation=operation,
                resource_type=resource_type,
                retryable=True,
            ) from exc

    async def _load(self, conn: Any, key: str) -> _JournalRow | None:
        row = await conn.fetchrow(
            """
            SELECT entity_type, entity_id, action, new_value,
                   EXTRACT(EPOCH FROM (now() - created_at))::float8 AS age_s
            FROM pitwall.config_audit
            WHERE new_value ->> 'kind' = 'runpod_control_plane_mutation'
              AND new_value ->> 'idempotency_key' = $1
              AND entity_type IN ('lease', 'provider', 'template', 'volume')
            ORDER BY id DESC
            LIMIT 1
            """,
            key,
        )
        if row is None:
            return None
        payload = row["new_value"]
        if not isinstance(payload, Mapping) or row["action"] not in {"create", "update", "delete"}:
            raise ValueError("malformed control-plane journal row")
        return _JournalRow(
            payload=cast(Mapping[str, object], payload),
            entity_type=str(row["entity_type"]),
            entity_id=str(row["entity_id"]),
            action=cast(MutationAction, row["action"]),
            age_s=float(row["age_s"]),
        )

    async def _record_failure(
        self, conn: Any, audit: ControlPlaneMutationAudit, error: RunPodControlPlaneError
    ) -> None:
        """Release the key after a failure that changed nothing; keep ``started`` otherwise."""
        try:
            async with conn.transaction():
                await self._insert(conn, audit, state="failed", outcome=error.code)
        except (
            Exception
        ) as exc:  # reason: the provider failure, not the journal write, reaches the caller
            log.warning(
                "RunPod control-plane journal failure record failed (%s)", type(exc).__name__
            )

    async def _insert(
        self,
        conn: Any,
        audit: ControlPlaneMutationAudit,
        *,
        state: Literal["started", "completed", "failed"],
        result: MutationResult | None = None,
        outcome: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "kind": self._KIND,
            "state": state,
            "provider": "runpod",
            "operation": audit.operation,
            "resource_type": audit.resource_type,
            "resource_id": audit.resource_id,
            "request_hash": audit.request_hash,
            "idempotency_key": audit.idempotency_key,
            "result": (
                result.model_dump(mode="json", exclude={"replayed"}) if result is not None else None
            ),
            "outcome": outcome,
            "recovery": dict(audit.recovery) if audit.recovery is not None else None,
        }
        entity_id = (result.resource_id if result is not None and result.resource_id else None) or (
            audit.resource_id or audit.idempotency_key
        )
        await insert_audit(
            self._pool,
            actor=self._actor,
            action=audit.action,
            entity_type=audit.entity_type,
            entity_id=entity_id,
            new_value=cast(Any, payload),
            change_reason=f"runpod control-plane {audit.operation} {state}",
            conn=conn,
        )


#: Age of a key's newest ``started`` row, on the database clock: when the latest attempt began.
#: The same lookup the orphaned-workload reaper uses for ``attempt_started_at``.
_ATTEMPT_AGE_SQL = """
    SELECT EXTRACT(EPOCH FROM (now() - created_at))::float8
    FROM pitwall.config_audit
    WHERE new_value ->> 'kind' = 'runpod_control_plane_mutation'
      AND new_value ->> 'idempotency_key' = $1
      AND new_value ->> 'state' = 'started'
      AND entity_type IN ('lease', 'provider', 'template', 'volume')
    ORDER BY id DESC
    LIMIT 1
"""

#: SQLSTATE ``lock_not_available``: ``lock_timeout`` expired while waiting for the key lock.
_LOCK_NOT_AVAILABLE = "55P03"


def _journal_replay(
    audit: ControlPlaneMutationAudit, payload: Mapping[str, object]
) -> MutationResult | None:
    """Replay, refuse, or (``None``) permit a new attempt for an existing journal entry.

    A ``compensated`` key was released by the caller that undid its resource, so it
    accepts a different request for the same operation, resource type, and resource id:
    a resumed workflow may rebuild the request around new dependency ids. Any other
    request on a released key is an ``idempotency_conflict``.
    """
    state = payload.get("state")
    if state == "compensated" and (
        payload.get("operation") == audit.operation
        and payload.get("resource_type") == audit.resource_type
        and payload.get("resource_id") == audit.resource_id
    ):
        return None
    if payload.get("request_hash") != audit.request_hash:
        raise RunPodControlPlaneError(
            "idempotency_conflict",
            "idempotency_key was already used for a different request",
            operation=audit.operation,
            resource_type=audit.resource_type,
            resource_id=audit.resource_id,
        )
    if state in {"failed", "compensated"}:
        return None
    if state == "started":
        raise RunPodControlPlaneError(
            "mutation_outcome_ambiguous",
            "a prior attempt with this idempotency_key has an unknown outcome; inspect the "
            "resource in RunPod, delete or adopt it, and retry with a new idempotency_key",
            operation=audit.operation,
            resource_type=audit.resource_type,
            resource_id=audit.resource_id,
        )
    if state == "completed":
        try:
            stored = MutationResult.model_validate(payload.get("result"))
        except ValidationError as exc:
            raise ValueError("malformed control-plane journal result") from exc
        return stored.model_copy(update={"replayed": True})
    raise ValueError("malformed control-plane journal state")


async def _advisory_unlock(conn: Any, lock_name: str) -> None:
    unlock = asyncio.create_task(
        conn.execute("SELECT pg_advisory_unlock(hashtextextended($1, 0))", lock_name)
    )
    try:
        await asyncio.shield(unlock)
    except asyncio.CancelledError:
        try:
            await unlock
        except Exception as exc:  # reason: cancellation still requires best-effort unlock
            log.warning("RunPod control-plane advisory unlock failed (%s)", type(exc).__name__)
        raise
    except Exception as exc:  # reason: unlock failure is logged without masking the outcome
        log.warning("RunPod control-plane advisory unlock failed (%s)", type(exc).__name__)


#: Failure codes that prove an apply changed nothing at RunPod, so the same key may retry.
_UNCHANGED_FAILURE_CODES = frozenset(
    {
        "credential_reference_unset",
        "invalid_request",
        "invalid_resource_id",
        "resource_name_conflict",
        "resource_not_found",
        "volume_grow_only",
    }
)


def _outcome_unchanged(error: RunPodControlPlaneError, operation: str) -> bool:
    """Whether a failed apply definitely left RunPod unchanged.

    A pod create never qualifies: ``create_pod_with_fallback`` walks several targets, and a
    later target's 4xx can follow an earlier attempt whose outcome is unknown. Its read-only
    preconditions run before the ``started`` row, so their failures never reach here. For the
    other operations, precondition-style refusals and provider 4xx rejections qualify;
    timeouts, 5xx and transport errors, malformed success responses, and partial failures
    do not.
    """
    if error.changed or operation == "pod.create":
        return False
    if error.code in _UNCHANGED_FAILURE_CODES:
        return True
    status = error.provider_status
    return error.code == "provider_error" and status is not None and 400 <= status < 500


#: Request fields that can carry credentials (environment values, process arguments, the
#: registry username). Their values enter the request hash only through a keyed HMAC.
_CREDENTIAL_REQUEST_FIELDS = frozenset({"env", "args", "username"})
_JOURNAL_KEY_CONTEXT = b"pitwall-runpod-journal-v1"


def _request_hash(operation: str, request: MutationRequest, journal_key: bytes | None) -> str:
    """SHA-256 of the canonical request; credential-bearing values enter only via HMAC.

    Each credential-bearing field keeps its shape (environment keys, argument count); the
    values are covered by one HMAC-SHA256 keyed from the RunPod API key, so a change to any
    value is a different request while the journal holds no plaintext and no unkeyed digest.
    """
    payload = request.model_dump(mode="json", exclude={"intent", "idempotency_key"})
    withheld = {
        field: payload[field]
        for field in sorted(_CREDENTIAL_REQUEST_FIELDS.intersection(payload))
        if payload[field]
    }
    for field in _CREDENTIAL_REQUEST_FIELDS.intersection(payload):
        payload[field] = _credential_free_shape(payload[field])
    if withheld:
        if journal_key is None:
            raise ValueError("journal key is required for credential-bearing fields")
        payload["withheld_hmac"] = hmac.new(
            journal_key, _canonical_json(withheld).encode("utf-8"), hashlib.sha256
        ).hexdigest()
    encoded = _canonical_json({"operation": operation, "request": payload})
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _recovery_fields(operation: str, request: MutationRequest) -> dict[str, object] | None:
    """Non-secret request fields that let a reconciler resolve an unknown-outcome create.

    Only ``pod.create`` records them: when a create's outcome is unknown and RunPod
    returned no pod id, ``reconciler.reap_orphaned_workloads`` looks for the pod carrying
    the attempt marker and leases it with the request's TTL and hourly cap.
    """
    if operation != "pod.create" or not isinstance(request, PodCreateRequest):
        return None
    cap = request.max_cost_per_hour
    return {
        "attempt_marker": create_attempt_marker(request.idempotency_key),
        "name": request.name,
        "ttl_minutes": request.ttl_minutes,
        "max_cost_per_hour": str(cap) if cap is not None else None,
    }


async def _no_preconditions() -> None:
    """The validate step of a mutation whose only provider I/O is the write itself."""


def _requires_journal_key(request: MutationRequest) -> bool:
    return any(getattr(request, field, None) for field in _CREDENTIAL_REQUEST_FIELDS)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _credential_free_shape(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(key): "<withheld>" for key in value}
    if isinstance(value, list):
        return ["<withheld>"] * len(value)
    return "<withheld>"


class StrictRunPodBackend:
    """Small concrete adapter over the existing strict RunPod clients."""

    async def list_pods(self) -> list[dict[str, Any]]:
        return await get_pods()

    async def get_pod(self, resource_id: str) -> dict[str, Any] | None:
        # Strict: only a 404 means absent. The lenient poll helper maps an unreachable RunPod to
        # None, which terminate would report as an already-absent pod that is still billing.
        return await get_pod_strict(resource_id)

    async def create_pod(self, request: PodCreateRequest) -> dict[str, Any]:
        workload = WorkloadConfig(
            name=request.name,
            capability="raw.runpod.resource",
            template_name=request.template_id,
            gpu_types=request.gpu_type_ids,
            gpu_count=request.gpu_count,
            container_disk_gb=request.disk_gb,
            min_vcpu=None,
            min_memory_gb=None,
            cloud_type=request.cloud,
            gpu_type_priority="custom",
            data_center_priority="custom",
            ports=",".join(request.ports) or None,
        )
        return await create_pod_with_fallback(
            name=request.name,
            template_id=request.template_id,
            image_name=request.image,
            workload=workload,
            env=request.env,
            cloud_type_override=request.cloud,
            network_volume_id=request.network_volume_id,
            data_center_id=request.data_center_id,
            docker_start_cmd=request.args or None,
            container_registry_auth_id=request.registry_auth_id,
            max_cost_per_hr=(
                float(request.max_cost_per_hour) if request.max_cost_per_hour is not None else None
            ),
            wait_for_readiness=False,
        )

    async def update_pod(self, request: PodUpdateRequest) -> dict[str, Any]:
        return await update_pod(
            request.resource_id,
            env=request.env,
            ports=request.ports,
            container_registry_auth_id=request.registry_auth_id,
        )

    async def action_pod(self, request: PodActionRequest) -> dict[str, Any]:
        actions = {
            "start": start_pod,
            "stop": stop_pod,
            "restart": restart_pod,
            "reset": reset_pod,
        }
        return await actions[request.action](request.resource_id)

    async def terminate_pod(self, resource_id: str) -> None:
        await terminate_pod(resource_id)

    async def list_endpoints(self) -> list[Endpoint]:
        return await list_endpoints()

    async def get_endpoint(self, resource_id: str) -> Endpoint:
        return await get_endpoint(resource_id)

    async def resolve_endpoint_gpu_types(
        self, gpu_type_ids: list[str], *, gpu_count: int
    ) -> EndpointGpuRequest:
        selection = await resolve_gpu_selection_for_types(gpu_type_ids)
        return EndpointGpuRequest(
            pools=selection.pools,
            excluded_type_ids=selection.excluded_types,
            count=gpu_count,
        )

    async def create_endpoint(self, request: EndpointCreateRequest) -> Endpoint:
        return await create_endpoint(
            request.name,
            request.template_id,
            gpu_pools=request.gpu.pools,
            excluded_gpu_types=request.gpu.excluded_type_ids,
            gpu_count=request.gpu.count,
            image_name=request.image,
            endpoint_type=request.endpoint_type,
            scaling=_endpoint_scaling(request.workers, request.scaling, request.flashboot),
        )

    async def update_endpoint(self, request: EndpointUpdateRequest) -> Endpoint:
        return await update_endpoint_scaling(
            request.resource_id,
            _endpoint_scaling(
                request.workers,
                request.scaling,
                request.flashboot,
            ),
            gpu_pools=request.gpu.pools if request.gpu is not None else None,
            excluded_gpu_types=(request.gpu.excluded_type_ids if request.gpu is not None else None),
            gpu_count=request.gpu.count if request.gpu is not None else 1,
        )

    async def delete_endpoint(self, resource_id: str) -> None:
        await delete_endpoint(resource_id)

    async def list_templates(self) -> list[Template]:
        return await list_account_templates()

    async def get_template(self, resource_id: str) -> Template:
        return await get_template(resource_id)

    async def create_template(self, request: TemplateCreateRequest) -> str:
        return await create_template_rest(
            name=request.name,
            image_name=request.image,
            container_disk_in_gb=request.disk_gb,
            volume_in_gb=request.volume_gb,
            volume_mount_path=request.volume_mount_path,
            docker_start_cmd=request.args,
            env=request.env,
            is_serverless=request.serverless,
            registry_auth_id=request.registry_auth_id,
            ports=request.ports,
        )

    async def update_template(self, request: TemplateUpdateRequest) -> Template:
        return await update_template(
            request.resource_id,
            name=request.name,
            image_name=request.image,
            docker_args=request.args,
            container_disk_in_gb=request.disk_gb,
            volume_in_gb=request.volume_gb,
            volume_mount_path=request.volume_mount_path,
            ports=(",".join(request.ports) if request.ports is not None else None),
            env=request.env,
            is_serverless=request.serverless,
        )

    async def delete_template(self, resource_id: str) -> None:
        await delete_template(resource_id)

    async def list_volumes(self) -> list[NetworkVolume]:
        client = NetworkVolumeClient()
        try:
            return await client.list()
        finally:
            await client.aclose()

    async def get_volume(self, resource_id: str) -> NetworkVolume:
        client = NetworkVolumeClient()
        try:
            return await client.get(resource_id)
        finally:
            await client.aclose()

    async def create_volume(self, request: VolumeCreateRequest) -> NetworkVolume:
        client = NetworkVolumeClient()
        try:
            return await client.create(request.name, request.size_gb, request.data_center_id)
        finally:
            await client.aclose()

    async def grow_volume(self, request: VolumeGrowRequest) -> NetworkVolume:
        client = NetworkVolumeClient()
        try:
            return await client.update(request.resource_id, request.size_gb)
        finally:
            await client.aclose()

    async def delete_volume(self, resource_id: str) -> None:
        client = NetworkVolumeClient()
        try:
            await client.delete(resource_id)
        finally:
            await client.aclose()

    async def list_registry_auths(self, timeout_s: float) -> list[ContainerRegistryAuth]:
        return await list_container_registry_auths(timeout_s=timeout_s)

    async def get_registry_auth(
        self, resource_id: str, timeout_s: float
    ) -> ContainerRegistryAuth | None:
        return await get_container_registry_auth(resource_id, timeout_s=timeout_s)

    async def create_registry_auth(
        self,
        request: RegistryAuthCreateRequest | RegistryAuthReplaceRequest,
        password: SecretStr,
        timeout_s: float,
    ) -> ContainerRegistryAuth:
        return await create_container_registry_auth(
            request.name,
            request.username,
            password.get_secret_value(),
            timeout_s=timeout_s,
        )

    async def delete_registry_auth(self, resource_id: str, timeout_s: float) -> None:
        await delete_container_registry_auth(resource_id, timeout_s=timeout_s)

    async def list_hub_templates(self, *, limit: int, offset: int) -> list[HubTemplate]:
        return await list_hub_templates(limit=limit, offset=offset)

    async def get_hub_template(self, resource_id: str) -> HubTemplate:
        return await get_hub_template(resource_id)


class RunPodControlPlaneService:
    """Resource-specific RunPod account operations shared by all transports."""

    def __init__(
        self,
        *,
        backend: StrictRunPodBackend | None = None,
        audit_pool: Any | None = None,
        actor: RunPodActor = "system",
        timeout_s: float = 60.0,
        environ: Mapping[str, str] | None = None,
        inspection_service: PreSpendInspectionService | None = None,
        mutation_journal: RunPodMutationJournal | None = None,
    ) -> None:
        if actor not in _SAFE_AUDIT_ACTORS:
            raise ValueError("unsupported RunPod control-plane audit actor")
        if not 0 < timeout_s <= _DEFAULT_TIMEOUT_CEILING_S:
            raise ValueError(
                f"timeout_s must be greater than zero and at most {_DEFAULT_TIMEOUT_CEILING_S:g}"
            )
        self._backend = backend or StrictRunPodBackend()
        self._audit_pool = audit_pool
        if mutation_journal is None and audit_pool is not None:
            mutation_journal = PostgresRunPodMutationJournal(
                audit_pool, actor=actor, lock_timeout_s=timeout_s
            )
        self._journal = mutation_journal
        self._actor = actor
        self._timeout_s = timeout_s
        self._environ = os.environ if environ is None else environ
        self._inspection_service = inspection_service or get_pre_spend_inspection_service()

    @property
    def has_audit_store(self) -> bool:
        """Whether live mutations can record their mandatory audit entry."""
        return self._audit_pool is not None

    async def list_pods(self) -> list[PodResource]:
        pods = await self._call("list", "pod", None, self._backend.list_pods)
        return [_pod_resource(item) for item in pods]

    async def get_pod(self, resource_id: str) -> PodResource:
        resource_id = _resource_id(resource_id, operation="get", resource_type="pod")
        pod = await self._call(
            "get", "pod", resource_id, lambda: self._backend.get_pod(resource_id)
        )
        if pod is None:
            raise _not_found("get", "pod", resource_id)
        return _pod_resource(pod)

    async def create_pod(self, request: PodCreateRequest) -> MutationResult:
        self._inspect_mutation(request, operation="pod.create", resource_type="pod")
        ceiling = (
            f"{request.max_cost_per_hour} USD/hour"
            if request.max_cost_per_hour is not None
            else None
        )
        if request.dry_run:
            return _preview(
                request,
                "pod.create",
                "pod",
                None,
                effect=f"create raw pod {request.name!r}; broker lease tracking is not created",
                ceiling=ceiling,
                irreversible=False,
            )

        async def validate() -> None:
            await self._ensure_unique_name("pod", request.name, self.list_pods)

        # The pod carries this attempt's marker, overriding any caller value, so a pod found
        # by the reaper after an unknown outcome can be proven to be this attempt's.
        marked = request.model_copy(
            update={
                "env": {
                    **request.env,
                    CREATE_ATTEMPT_ENV: create_attempt_marker(request.idempotency_key),
                }
            }
        )

        async def apply(_: None, conn: Any) -> MutationResult:
            try:
                raw = await self._call(
                    "create",
                    "pod",
                    None,
                    lambda: self._backend.create_pod(marked),
                    secrets=(*request.env.values(), *request.args),
                    timeout_s=OPERATION_TIMEOUT_CEILING_S["pod.create"],
                )
            except RunPodControlPlaneError as exc:
                await self._audit_failed_create(conn, request, exc)
                raise
            resource = _pod_resource(raw)
            result = _changed_result(
                request,
                "pod.create",
                "pod",
                resource.id,
                f"created raw pod {resource.id}",
                ceiling=ceiling,
                resource=resource,
            )
            await self._audit(conn, request, result, entity_type="lease", action="create")
            return result

        return await self._apply_once(
            request,
            operation="pod.create",
            resource_type="pod",
            entity_type="lease",
            action="create",
            validate=validate,
            apply=apply,
        )

    async def update_pod(self, request: PodUpdateRequest) -> MutationResult:
        self._inspect_mutation(request, operation="pod.update", resource_type="pod")
        fields = _present_fields(request, ("env", "ports", "registry_auth_id"))
        if request.dry_run:
            return _preview(
                request,
                "pod.update",
                "pod",
                request.resource_id,
                effect=f"replace mutable pod fields: {', '.join(fields)}",
                irreversible="env" in fields,
            )

        async def apply(_: None, conn: Any) -> MutationResult:
            raw = await self._call(
                "update",
                "pod",
                request.resource_id,
                lambda: self._backend.update_pod(request),
                secrets=(request.env or {}).values(),
            )
            resource = _pod_resource(raw)
            result = _changed_result(
                request,
                "pod.update",
                "pod",
                resource.id,
                f"updated pod fields: {', '.join(fields)}",
                irreversible="env" in fields,
                resource=resource,
            )
            await self._audit(
                conn, request, result, entity_type="lease", action="update", fields=fields
            )
            return result

        return await self._apply_once(
            request,
            operation="pod.update",
            resource_type="pod",
            entity_type="lease",
            action="update",
            validate=_no_preconditions,
            apply=apply,
        )

    async def action_pod(self, request: PodActionRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation=f"pod.{request.action}",
            resource_type="pod",
        )
        if request.dry_run:
            return _preview(
                request,
                f"pod.{request.action}",
                "pod",
                request.resource_id,
                effect=f"{request.action} raw pod {request.resource_id}",
                irreversible=request.action == "reset",
            )

        async def apply(_: None, conn: Any) -> MutationResult:
            raw = await self._call(
                request.action,
                "pod",
                request.resource_id,
                lambda: self._backend.action_pod(request),
            )
            resource = _pod_resource(raw)
            result = _changed_result(
                request,
                f"pod.{request.action}",
                "pod",
                resource.id,
                f"{request.action} requested for raw pod {resource.id}",
                irreversible=request.action == "reset",
                resource=resource,
            )
            await self._audit(conn, request, result, entity_type="lease", action="update")
            return result

        return await self._apply_once(
            request,
            operation=f"pod.{request.action}",
            resource_type="pod",
            entity_type="lease",
            action="update",
            validate=_no_preconditions,
            apply=apply,
        )

    async def terminate_pod(self, request: IdentifiedMutationRequest) -> MutationResult:
        self._inspect_mutation(request, operation="pod.terminate", resource_type="pod")
        if request.dry_run:
            return _preview(
                request,
                "pod.terminate",
                "pod",
                request.resource_id,
                effect=f"permanently terminate raw pod {request.resource_id}",
                irreversible=True,
            )

        async def validate() -> dict[str, Any] | None:
            return await self._call(
                "get",
                "pod",
                request.resource_id,
                lambda: self._backend.get_pod(request.resource_id),
            )

        async def apply(existing: dict[str, Any] | None, conn: Any) -> MutationResult:
            if existing is None:
                return _absent_result(request, "pod.terminate", "pod")
            await self._call(
                "terminate",
                "pod",
                request.resource_id,
                lambda: self._backend.terminate_pod(request.resource_id),
            )
            result = _changed_result(
                request,
                "pod.terminate",
                "pod",
                request.resource_id,
                f"permanently terminated raw pod {request.resource_id}",
                irreversible=True,
            )
            await self._audit(conn, request, result, entity_type="lease", action="delete")
            return result

        return await self._apply_once(
            request,
            operation="pod.terminate",
            resource_type="pod",
            entity_type="lease",
            action="delete",
            validate=validate,
            apply=apply,
        )

    async def list_endpoints(self) -> list[EndpointResource]:
        values = await self._call("list", "endpoint", None, self._backend.list_endpoints)
        return [_endpoint_resource(value) for value in values]

    async def get_endpoint(self, resource_id: str) -> EndpointResource:
        resource_id = _resource_id(resource_id, operation="get", resource_type="endpoint")
        value = await self._call(
            "get", "endpoint", resource_id, lambda: self._backend.get_endpoint(resource_id)
        )
        return _endpoint_resource(value)

    async def resolve_endpoint_gpu_types(
        self, gpu_type_ids: list[str], *, gpu_count: int
    ) -> EndpointGpuRequest:
        normalized = validate_canonical_gpu_names(gpu_type_ids)
        if (
            not normalized
            or len(normalized) > 16
            or len(set(normalized)) != len(normalized)
            or not 1 <= gpu_count <= 8
        ):
            raise RunPodControlPlaneError(
                "invalid_gpu_selection",
                "endpoint GPU type selection is invalid",
                operation="resolve_gpu_types",
                resource_type="endpoint",
            )
        return await self._call(
            "resolve_gpu_types",
            "endpoint",
            None,
            lambda: self._backend.resolve_endpoint_gpu_types(
                normalized,
                gpu_count=gpu_count,
            ),
        )

    async def create_endpoint(self, request: EndpointCreateRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation="endpoint.create",
            resource_type="endpoint",
        )
        if request.dry_run:
            return _preview(
                request,
                "endpoint.create",
                "endpoint",
                None,
                effect=(
                    f"create {request.endpoint_type} endpoint {request.name!r} with "
                    f"workers {request.workers.minimum}-{request.workers.maximum}"
                ),
            )

        async def validate() -> None:
            await self._ensure_unique_name("endpoint", request.name, self.list_endpoints)

        async def apply(_: None, conn: Any) -> MutationResult:
            value = await self._call(
                "create", "endpoint", None, lambda: self._backend.create_endpoint(request)
            )
            resource = _endpoint_resource(value)
            result = _changed_result(
                request,
                "endpoint.create",
                "endpoint",
                resource.id,
                f"created endpoint {resource.id}",
                resource=resource,
            )
            await self._audit(conn, request, result, entity_type="provider", action="create")
            return result

        return await self._apply_once(
            request,
            operation="endpoint.create",
            resource_type="endpoint",
            entity_type="provider",
            action="create",
            validate=validate,
            apply=apply,
        )

    async def update_endpoint(self, request: EndpointUpdateRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation="endpoint.update",
            resource_type="endpoint",
        )
        if request.dry_run:
            return _preview(
                request,
                "endpoint.update",
                "endpoint",
                request.resource_id,
                effect=(
                    f"replace endpoint workers/scaling with {request.workers.minimum}-"
                    f"{request.workers.maximum} and {request.scaling.type}"
                ),
            )

        async def apply(_: None, conn: Any) -> MutationResult:
            value = await self._call(
                "update",
                "endpoint",
                request.resource_id,
                lambda: self._backend.update_endpoint(request),
            )
            resource = _endpoint_resource(value)
            result = _changed_result(
                request,
                "endpoint.update",
                "endpoint",
                resource.id,
                "updated nested endpoint workers/scaling/GPU selection",
                resource=resource,
            )
            await self._audit(conn, request, result, entity_type="provider", action="update")
            return result

        return await self._apply_once(
            request,
            operation="endpoint.update",
            resource_type="endpoint",
            entity_type="provider",
            action="update",
            validate=_no_preconditions,
            apply=apply,
        )

    async def delete_endpoint(self, request: IdentifiedMutationRequest) -> MutationResult:
        return await self._delete_resource(
            request,
            resource_type="endpoint",
            operation="endpoint.delete",
            entity_type="provider",
            get=lambda: self._backend.get_endpoint(request.resource_id),
            delete=lambda: self._backend.delete_endpoint(request.resource_id),
            effect=f"permanently delete endpoint {request.resource_id}",
        )

    async def list_templates(self) -> list[TemplateResource]:
        values = await self._call("list", "template", None, self._backend.list_templates)
        return [_template_resource(value) for value in values]

    async def get_template(self, resource_id: str) -> TemplateResource:
        resource_id = _resource_id(resource_id, operation="get", resource_type="template")
        value = await self._call(
            "get", "template", resource_id, lambda: self._backend.get_template(resource_id)
        )
        return _template_resource(value)

    async def create_template(self, request: TemplateCreateRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation="template.create",
            resource_type="template",
        )
        if request.dry_run:
            return _preview(
                request,
                "template.create",
                "template",
                None,
                effect=f"create account template {request.name!r}; no Hub publication",
            )

        async def validate() -> None:
            await self._ensure_unique_name("template", request.name, self.list_templates)

        async def apply(_: None, conn: Any) -> MutationResult:
            resource_id = await self._call(
                "create",
                "template",
                None,
                lambda: self._backend.create_template(request),
                secrets=(*request.env.values(), *request.args),
            )
            try:
                resource = await self.get_template(resource_id)
            except RunPodControlPlaneError as exc:
                partial = _changed_result(
                    request,
                    "template.create",
                    "template",
                    resource_id,
                    f"created account template {resource_id}; result read failed",
                )
                await self._audit(
                    conn,
                    request,
                    partial,
                    entity_type="template",
                    action="create",
                )
                raise RunPodControlPlaneError(
                    "template_create_partial_failure",
                    "account template was created but its result could not be read",
                    operation="template.create",
                    resource_type="template",
                    resource_id=resource_id,
                    retryable=False,
                    provider_status=exc.provider_status,
                    changed=True,
                ) from exc
            result = _changed_result(
                request,
                "template.create",
                "template",
                resource.id,
                f"created account template {resource.id}",
                resource=resource,
            )
            await self._audit(conn, request, result, entity_type="template", action="create")
            return result

        return await self._apply_once(
            request,
            operation="template.create",
            resource_type="template",
            entity_type="template",
            action="create",
            validate=validate,
            apply=apply,
        )

    async def update_template(self, request: TemplateUpdateRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation="template.update",
            resource_type="template",
        )
        fields = _present_fields(
            request,
            (
                "name",
                "image",
                "args",
                "disk_gb",
                "volume_gb",
                "volume_mount_path",
                "ports",
                "env",
                "serverless",
            ),
        )
        if request.dry_run and request.volume_mount_path is not None and request.volume_gb is None:
            current = await self.get_template(request.resource_id)
            if current.volume_gb < 10:
                raise RunPodControlPlaneError(
                    "invalid_request",
                    "volume_mount_path requires an existing or requested template volume",
                    operation="template.update",
                    resource_type="template",
                    resource_id=request.resource_id,
                )
        if request.dry_run:
            return _preview(
                request,
                "template.update",
                "template",
                request.resource_id,
                effect=f"replace account template fields: {', '.join(fields)}",
                irreversible="env" in fields,
            )

        async def apply(_: None, conn: Any) -> MutationResult:
            value = await self._call(
                "update",
                "template",
                request.resource_id,
                lambda: self._backend.update_template(request),
                secrets=(*(request.env or {}).values(), request.args or ""),
            )
            resource = _template_resource(value)
            result = _changed_result(
                request,
                "template.update",
                "template",
                resource.id,
                f"updated account template fields: {', '.join(fields)}",
                irreversible="env" in fields,
                resource=resource,
            )
            await self._audit(
                conn, request, result, entity_type="template", action="update", fields=fields
            )
            return result

        return await self._apply_once(
            request,
            operation="template.update",
            resource_type="template",
            entity_type="template",
            action="update",
            validate=_no_preconditions,
            apply=apply,
        )

    async def delete_template(self, request: IdentifiedMutationRequest) -> MutationResult:
        return await self._delete_resource(
            request,
            resource_type="template",
            operation="template.delete",
            entity_type="template",
            get=lambda: self._backend.get_template(request.resource_id),
            delete=lambda: self._backend.delete_template(request.resource_id),
            effect=f"permanently delete account template {request.resource_id}",
        )

    async def list_volumes(self) -> list[VolumeResource]:
        values = await self._call("list", "volume", None, self._backend.list_volumes)
        return [_volume_resource(value) for value in values]

    async def get_volume(self, resource_id: str) -> VolumeResource:
        resource_id = _resource_id(resource_id, operation="get", resource_type="volume")
        value = await self._call(
            "get", "volume", resource_id, lambda: self._backend.get_volume(resource_id)
        )
        return _volume_resource(value)

    async def create_volume(self, request: VolumeCreateRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation="volume.create",
            resource_type="volume",
        )
        if request.dry_run:
            return _preview(
                request,
                "volume.create",
                "volume",
                None,
                effect=(
                    f"create {request.size_gb} GB volume {request.name!r} in "
                    f"{request.data_center_id}"
                ),
            )

        async def validate() -> None:
            await self._ensure_unique_name("volume", request.name, self.list_volumes)

        async def apply(_: None, conn: Any) -> MutationResult:
            value = await self._call(
                "create", "volume", None, lambda: self._backend.create_volume(request)
            )
            resource = _volume_resource(value)
            result = _changed_result(
                request,
                "volume.create",
                "volume",
                resource.id,
                f"created {resource.size_gb} GB volume {resource.id}",
                resource=resource,
            )
            await self._audit(conn, request, result, entity_type="volume", action="create")
            return result

        return await self._apply_once(
            request,
            operation="volume.create",
            resource_type="volume",
            entity_type="volume",
            action="create",
            validate=validate,
            apply=apply,
        )

    async def grow_volume(self, request: VolumeGrowRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation="volume.grow",
            resource_type="volume",
        )
        if request.dry_run:
            current = await self._volume_to_grow(request)
            return _preview(
                request,
                "volume.grow",
                "volume",
                request.resource_id,
                effect=f"grow volume from {current.size_gb} GB to {request.size_gb} GB",
                irreversible=True,
            )

        async def validate() -> VolumeResource:
            return await self._volume_to_grow(request)

        async def apply(current: VolumeResource, conn: Any) -> MutationResult:
            value = await self._call(
                "update",
                "volume",
                request.resource_id,
                lambda: self._backend.grow_volume(request),
            )
            resource = _volume_resource(value)
            result = _changed_result(
                request,
                "volume.grow",
                "volume",
                resource.id,
                f"grew volume from {current.size_gb} GB to {resource.size_gb} GB",
                irreversible=True,
                resource=resource,
            )
            await self._audit(
                conn,
                request,
                result,
                entity_type="volume",
                action="update",
                old_value={"size_gb": current.size_gb},
            )
            return result

        return await self._apply_once(
            request,
            operation="volume.grow",
            resource_type="volume",
            entity_type="volume",
            action="update",
            validate=validate,
            apply=apply,
        )

    async def _volume_to_grow(self, request: VolumeGrowRequest) -> VolumeResource:
        current = await self.get_volume(request.resource_id)
        if request.size_gb <= current.size_gb:
            raise RunPodControlPlaneError(
                "volume_grow_only",
                f"volume size must grow beyond current {current.size_gb} GB",
                operation="volume.grow",
                resource_type="volume",
                resource_id=request.resource_id,
            )
        return current

    async def delete_volume(self, request: IdentifiedMutationRequest) -> MutationResult:
        return await self._delete_resource(
            request,
            resource_type="volume",
            operation="volume.delete",
            entity_type="volume",
            get=lambda: self._backend.get_volume(request.resource_id),
            delete=lambda: self._backend.delete_volume(request.resource_id),
            effect=f"permanently delete volume {request.resource_id} and its data",
        )

    async def list_registry_auths(self) -> list[RegistryAuthResource]:
        values = await self._call(
            "list",
            "registry_auth",
            None,
            lambda: self._backend.list_registry_auths(self._timeout_s),
        )
        return [_registry_resource(value) for value in values]

    async def get_registry_auth(self, resource_id: str) -> RegistryAuthResource:
        resource_id = _resource_id(resource_id, operation="get", resource_type="registry_auth")
        value = await self._call(
            "get",
            "registry_auth",
            resource_id,
            lambda: self._backend.get_registry_auth(resource_id, self._timeout_s),
        )
        if value is None:
            raise _not_found("get", "registry_auth", resource_id)
        return _registry_resource(value)

    async def create_registry_auth(self, request: RegistryAuthCreateRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation="registry_auth.create",
            resource_type="registry_auth",
        )
        if request.dry_run:
            self._resolve_password(request.password_env)
            return _preview(
                request,
                "registry_auth.create",
                "registry_auth",
                None,
                effect=f"create registry auth {request.name!r} from {request.password_env}",
                irreversible=True,
            )

        async def validate() -> SecretStr:
            await self._ensure_unique_name("registry_auth", request.name, self.list_registry_auths)
            return self._resolve_password(request.password_env)

        async def apply(password: SecretStr, conn: Any) -> MutationResult:
            value = await self._call(
                "create",
                "registry_auth",
                None,
                lambda: self._backend.create_registry_auth(request, password, self._timeout_s),
                secrets=(password.get_secret_value(), request.username),
            )
            resource = _registry_resource(value)
            result = _changed_result(
                request,
                "registry_auth.create",
                "registry_auth",
                resource.id,
                f"created registry auth {resource.id}; credentials were not retained",
                irreversible=True,
                resource=resource,
            )
            await self._audit(conn, request, result, entity_type="provider", action="create")
            return result

        return await self._apply_once(
            request,
            operation="registry_auth.create",
            resource_type="registry_auth",
            entity_type="provider",
            action="create",
            validate=validate,
            apply=apply,
        )

    async def replace_registry_auth(self, request: RegistryAuthReplaceRequest) -> MutationResult:
        self._inspect_mutation(
            request,
            operation="registry_auth.replace",
            resource_type="registry_auth",
        )
        if request.dry_run:
            current, _password = await self._registry_auth_to_replace(request)
            return _preview(
                request,
                "registry_auth.replace",
                "registry_auth",
                request.resource_id,
                effect=(
                    f"delete registry auth {current.id}, then recreate {request.name!r}; "
                    "the identifier will change"
                ),
                irreversible=True,
            )

        async def validate() -> tuple[RegistryAuthResource, SecretStr]:
            return await self._registry_auth_to_replace(request)

        async def apply(
            checked: tuple[RegistryAuthResource, SecretStr], conn: Any
        ) -> MutationResult:
            _current, password = checked
            await self._call(
                "delete",
                "registry_auth",
                request.resource_id,
                lambda: self._backend.delete_registry_auth(request.resource_id, self._timeout_s),
            )
            deletion = _changed_result(
                request,
                "registry_auth.replace.delete",
                "registry_auth",
                request.resource_id,
                f"deleted registry auth {request.resource_id} before replacement",
                irreversible=True,
            )
            await self._audit(conn, request, deletion, entity_type="provider", action="delete")
            try:
                value = await self._call(
                    "create",
                    "registry_auth",
                    None,
                    lambda: self._backend.create_registry_auth(request, password, self._timeout_s),
                    secrets=(password.get_secret_value(), request.username),
                )
            except RunPodControlPlaneError as exc:
                raise RunPodControlPlaneError(
                    "registry_replace_partial_failure",
                    "old registry auth was deleted but replacement creation failed",
                    operation="registry_auth.replace",
                    resource_type="registry_auth",
                    resource_id=request.resource_id,
                    retryable=exc.retryable,
                    provider_status=exc.provider_status,
                    changed=True,
                ) from exc
            resource = _registry_resource(value)
            result = _changed_result(
                request,
                "registry_auth.replace",
                "registry_auth",
                resource.id,
                f"replaced registry auth {request.resource_id} with {resource.id}",
                irreversible=True,
                resource=resource,
            )
            await self._audit(conn, request, result, entity_type="provider", action="create")
            return result

        return await self._apply_once(
            request,
            operation="registry_auth.replace",
            resource_type="registry_auth",
            entity_type="provider",
            action="update",
            validate=validate,
            apply=apply,
        )

    async def _registry_auth_to_replace(
        self, request: RegistryAuthReplaceRequest
    ) -> tuple[RegistryAuthResource, SecretStr]:
        current = await self.get_registry_auth(request.resource_id)
        registry_auths = await self.list_registry_auths()
        duplicate = next(
            (
                value
                for value in registry_auths
                if value.name == request.name and value.id != current.id
            ),
            None,
        )
        if duplicate is not None:
            raise RunPodControlPlaneError(
                "resource_name_conflict",
                "registry_auth name already exists",
                operation="registry_auth.replace",
                resource_type="registry_auth",
                resource_id=duplicate.id,
            )
        return current, self._resolve_password(request.password_env)

    async def delete_registry_auth(self, request: IdentifiedMutationRequest) -> MutationResult:
        return await self._delete_resource(
            request,
            resource_type="registry_auth",
            operation="registry_auth.delete",
            entity_type="provider",
            get=lambda: self._backend.get_registry_auth(request.resource_id, self._timeout_s),
            delete=lambda: self._backend.delete_registry_auth(request.resource_id, self._timeout_s),
            effect=f"permanently delete registry auth {request.resource_id}",
        )

    async def list_hub_templates(
        self, *, limit: int = 50, offset: int = 0
    ) -> list[HubTemplateResource]:
        if not 1 <= limit <= 100:
            raise RunPodControlPlaneError(
                "invalid_request",
                "Hub template limit must be between 1 and 100",
                operation="list",
                resource_type="hub_template",
            )
        if offset < 0:
            raise RunPodControlPlaneError(
                "invalid_request",
                "Hub template offset must be non-negative",
                operation="list",
                resource_type="hub_template",
            )
        values = await self._call(
            "list",
            "hub_template",
            None,
            lambda: self._backend.list_hub_templates(limit=limit, offset=offset),
        )
        return [_hub_template_resource(value) for value in values]

    async def get_hub_template(self, resource_id: str) -> HubTemplateResource:
        resource_id = _resource_id(resource_id, operation="get", resource_type="hub_template")
        value = await self._call(
            "get",
            "hub_template",
            resource_id,
            lambda: self._backend.get_hub_template(resource_id),
        )
        return _hub_template_resource(value)

    async def search_hub_templates(
        self, query: str, *, limit: int = 50
    ) -> list[HubTemplateResource]:
        normalized = query.strip().casefold()
        if not normalized:
            raise RunPodControlPlaneError(
                "invalid_request",
                "Hub search query must be non-empty",
                operation="search",
                resource_type="hub_template",
            )
        if not 1 <= limit <= 100:
            raise RunPodControlPlaneError(
                "invalid_request",
                "Hub search limit must be between 1 and 100",
                operation="search",
                resource_type="hub_template",
            )
        values = await self.list_hub_templates(limit=100, offset=0)
        matches = [
            value
            for value in values
            if normalized
            in " ".join(
                filter(
                    None,
                    (
                        value.id,
                        value.name,
                        value.display_name,
                        value.image,
                        value.description,
                    ),
                )
            ).casefold()
        ]
        return matches[:limit]

    async def _delete_resource(
        self,
        request: IdentifiedMutationRequest,
        *,
        resource_type: str,
        operation: str,
        entity_type: str,
        get: Callable[[], Awaitable[object | None]],
        delete: Callable[[], Awaitable[None]],
        effect: str,
    ) -> MutationResult:
        self._inspect_mutation(request, operation=operation, resource_type=resource_type)
        if request.dry_run:
            return _preview(
                request,
                operation,
                resource_type,
                request.resource_id,
                effect=effect,
                irreversible=True,
            )

        async def validate() -> object | None:
            try:
                return await self._call("get", resource_type, request.resource_id, get)
            except RunPodControlPlaneError as exc:
                if exc.code != "resource_not_found":
                    raise
                return None

        async def apply(existing: object | None, conn: Any) -> MutationResult:
            if existing is None:
                return _absent_result(request, operation, resource_type)
            await self._call("delete", resource_type, request.resource_id, delete)
            result = _changed_result(
                request,
                operation,
                resource_type,
                request.resource_id,
                effect,
                irreversible=True,
            )
            await self._audit(conn, request, result, entity_type=entity_type, action="delete")
            return result

        return await self._apply_once(
            request,
            operation=operation,
            resource_type=resource_type,
            entity_type=entity_type,
            action="delete",
            validate=validate,
            apply=apply,
        )

    async def _ensure_unique_name[
        Resource: PodResource
        | EndpointResource
        | TemplateResource
        | VolumeResource
        | RegistryAuthResource
    ](
        self,
        resource_type: str,
        name: str,
        loader: Callable[[], Awaitable[list[Resource]]],
    ) -> None:
        values = await loader()
        duplicate = next((value for value in values if value.name == name), None)
        if duplicate is not None:
            raise RunPodControlPlaneError(
                "resource_name_conflict",
                f"{resource_type} name already exists",
                operation="create",
                resource_type=resource_type,
                resource_id=duplicate.id,
            )

    async def _apply_once[Context](
        self,
        request: MutationRequest,
        *,
        operation: str,
        resource_type: str,
        entity_type: str,
        action: MutationAction,
        validate: Callable[[], Awaitable[Context]],
        apply: Callable[[Context, Any], Awaitable[MutationResult]],
    ) -> MutationResult:
        """Run one applied mutation through the idempotency journal.

        The journal lookup comes before every provider call. ``validate`` holds the
        read-only preconditions and runs under the key lock before the ``started`` row, so
        a repeated key replays instead of being refused by its own earlier success, and a
        failed read leaves the key free. ``apply`` holds the provider write.
        """
        resource_id = getattr(request, "resource_id", None)
        self._require_audit(operation, resource_type, resource_id)
        journal = self._journal
        if journal is None:
            raise RunPodControlPlaneError(
                "audit_unavailable",
                "mutation requires the configured Pitwall audit store",
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        journal_key = self._journal_key() if _requires_journal_key(request) else None
        if _requires_journal_key(request) and journal_key is None:
            raise RunPodControlPlaneError(
                "credential_reference_unset",
                "RUNPOD_API_KEY is required to record this mutation in the idempotency journal",
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
            )
        audit = ControlPlaneMutationAudit(
            operation=operation,
            resource_type=resource_type,
            entity_type=entity_type,
            action=action,
            resource_id=resource_id,
            idempotency_key=request.idempotency_key,
            request_hash=_request_hash(operation, request, journal_key),
            recovery=_recovery_fields(operation, request),
        )
        return await journal.run_idempotent(audit, validate, apply)

    async def release_idempotency_key(self, idempotency_key: str, *, reason: str) -> bool:
        """Release a key whose created resource the caller has undone or proven absent.

        Appends a ``compensated`` journal row, so the same key and request apply again
        instead of replaying a resource that no longer exists. A released key accepts a
        different request only for the same operation, resource type, and resource id;
        any other request is an ``idempotency_conflict``. Returns ``False`` when the key
        is unused or already released.
        """
        journal = self._journal
        if journal is None:
            raise RunPodControlPlaneError(
                "audit_unavailable",
                "mutation requires the configured Pitwall audit store",
                operation="release",
                resource_type="mutation",
            )
        return await journal.release(idempotency_key, reason=reason)

    async def idempotency_key_status(self, idempotency_key: str) -> JournalEntry | None:
        """The key's newest journal entry: its state, resource id, and age in seconds."""
        journal = self._journal
        if journal is None:
            raise RunPodControlPlaneError(
                "audit_unavailable",
                "mutation requires the configured Pitwall audit store",
                operation="journal.read",
                resource_type="mutation",
            )
        return await journal.latest(idempotency_key)

    def _journal_key(self) -> bytes | None:
        """HMAC key for credential-bearing request values, derived from the RunPod API key."""
        api_key, _source = resolve_runpod_api_key(self._environ)
        if not api_key:
            return None
        return hashlib.sha256(_JOURNAL_KEY_CONTEXT + api_key.encode("utf-8")).digest()

    def _require_audit(
        self, operation: str, resource_type: str, resource_id: str | None = None
    ) -> None:
        if self._audit_pool is None:
            raise RunPodControlPlaneError(
                "audit_unavailable",
                "mutation requires the configured Pitwall audit store",
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
            )

    def _inspect_mutation(
        self,
        request: MutationRequest,
        *,
        operation: str,
        resource_type: str,
    ) -> None:
        """Enforce one bounded decision before any provider or audit I/O.

        Registry requests intentionally expose only the credential reference to
        inspection.  The referenced password is resolved later at the strict
        provider boundary and is expected credential material, not arbitrary
        operator payload.
        """
        payload = request.model_dump(mode="json", exclude_none=True)
        password_reference = payload.pop("password_env", None)
        if password_reference is not None:
            payload["environment_variable"] = password_reference
        inspect = (
            self._inspection_service.preview
            if request.dry_run
            else self._inspection_service.inspect
        )
        decision = inspect(payload)
        # Control-plane fields can alter resource identity, process argv, or
        # credentials.  Rewriting them silently is not schema-safe, so even a
        # redact decision fails closed rather than changing provider intent.
        if decision.decision != PreSpendDecision.ALLOW:
            raise RunPodControlPlaneError(
                "pre_spend_payload_rejected",
                "RunPod mutation payload was rejected by pre-spend policy",
                operation=operation,
                resource_type=resource_type,
                # The rejected identifier is part of the inspected payload.  Do
                # not reflect it through the otherwise-safe error envelope.
                resource_id=None,
            )

    async def _audit(
        self,
        conn: Any,
        request: MutationRequest,
        result: MutationResult,
        *,
        entity_type: str,
        action: Literal["create", "update", "delete"],
        fields: tuple[str, ...] = (),
        old_value: dict[str, object] | None = None,
    ) -> None:
        self._require_audit(result.operation, result.resource_type, result.resource_id)
        safe_new: dict[str, object] = {
            "provider": "runpod",
            "operation": result.operation,
            "resource_type": result.resource_type,
            "resource_id": result.resource_id,
            "changed": result.changed,
        }
        if fields:
            safe_new["fields"] = list(fields)
        try:
            await asyncio.wait_for(
                insert_audit(
                    self._audit_pool,
                    actor=self._actor,
                    action=action,
                    entity_type=entity_type,
                    entity_id=result.resource_id or request.idempotency_key,
                    old_value=old_value,
                    new_value=safe_new,
                    change_reason=(
                        f"runpod control-plane {result.operation}; "
                        f"idempotency_key={request.idempotency_key}"
                    ),
                    conn=conn,
                ),
                timeout=self._timeout_s,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise RunPodControlPlaneError(
                "audit_write_failed",
                "RunPod mutation completed but the audit write timed out",
                operation=result.operation,
                resource_type=result.resource_type,
                resource_id=result.resource_id,
                retryable=True,
                changed=result.changed,
            ) from exc
        except Exception as exc:  # reason: persistence internals must not escape transport adapters
            raise RunPodControlPlaneError(
                "audit_write_failed",
                "RunPod mutation completed but the audit write failed",
                operation=result.operation,
                resource_type=result.resource_type,
                resource_id=result.resource_id,
                retryable=True,
                changed=result.changed,
            ) from exc

    async def _audit_failed_create(
        self, conn: Any, request: PodCreateRequest, error: RunPodControlPlaneError
    ) -> None:
        """Record a failed or timed-out create; a create may have left a paid pod behind."""
        cause = error.__cause__
        pod_id = error.resource_id or getattr(cause, "pod_id", None)
        resource_id = pod_id if isinstance(pod_id, str) else None
        safe_new: dict[str, object] = {
            "provider": "runpod",
            "operation": "pod.create",
            "resource_type": "pod",
            "resource_id": resource_id,
            "changed": error.changed,
            "outcome": error.code,
            "ambiguous": bool(getattr(cause, "ambiguous_create", False)),
        }
        try:
            await asyncio.wait_for(
                insert_audit(
                    self._audit_pool,
                    actor=self._actor,
                    action="create",
                    entity_type="lease",
                    entity_id=resource_id or request.idempotency_key,
                    old_value=None,
                    new_value=safe_new,
                    change_reason=(
                        f"runpod control-plane pod.create failed ({error.code}); "
                        f"idempotency_key={request.idempotency_key}"
                    ),
                    conn=conn,
                ),
                timeout=self._timeout_s,
            )
        except asyncio.CancelledError:
            raise
        except Exception:  # reason: the create failure, not the audit failure, reaches the caller
            log.error("failed-create audit write failed", exc_info=True)

    def _resolve_password(self, reference: str) -> SecretStr:
        value = self._environ.get(reference, "")
        if not value:
            raise RunPodControlPlaneError(
                "credential_reference_unset",
                f"registry credential reference {reference!r} is unset or empty",
                operation="registry_auth.credentials",
                resource_type="registry_auth",
            )
        return SecretStr(value)

    async def _call(
        self,
        operation: str,
        resource_type: str,
        resource_id: str | None,
        call: Callable[[], Awaitable[_T]],
        *,
        secrets: Iterable[object] = (),
        timeout_s: float | None = _SERVICE_TIMEOUT,
    ) -> _T:
        secret_values = tuple(str(value) for value in secrets if str(value))
        ceiling = self._timeout_s if timeout_s is _SERVICE_TIMEOUT else timeout_s
        try:
            if ceiling is None:
                return await call()
            return await asyncio.wait_for(call(), timeout=ceiling)
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise RunPodControlPlaneError(
                "provider_timeout",
                "RunPod control-plane request timed out",
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
                retryable=True,
            ) from exc
        except httpx.TimeoutException as exc:
            raise RunPodControlPlaneError(
                "provider_timeout",
                "RunPod control-plane request timed out",
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
                retryable=True,
            ) from exc
        except TemplateNotFoundError as exc:
            raise _not_found(operation, resource_type, resource_id) from exc
        except RunPodRestError as exc:
            if exc.status_code == 404:
                raise _not_found(operation, resource_type, resource_id) from exc
            raise _provider_error(
                exc,
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
                status=exc.status_code,
                secrets=secret_values,
            ) from exc
        except RegistryAuthError as exc:
            if exc.status_code == 404:
                raise _not_found(operation, resource_type, resource_id) from exc
            raise _provider_error(
                exc,
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
                status=exc.status_code,
                secrets=secret_values,
            ) from exc
        except RunPodControlPlaneError:
            raise
        except ValidationError as exc:
            raise RunPodControlPlaneError(
                "malformed_provider_response",
                "RunPod returned a response that failed strict validation",
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise _provider_error(
                exc,
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
                status=None,
                secrets=secret_values,
                retryable_override=True,
            ) from exc
        except (RunPodError, ValueError) as exc:
            raise _provider_error(
                exc,
                operation=operation,
                resource_type=resource_type,
                resource_id=resource_id,
                status=None,
                secrets=secret_values,
            ) from exc


def _endpoint_scaling(
    workers: EndpointWorkersRequest,
    scaling: EndpointScalingRequest,
    flashboot: bool,
) -> EndpointScalingConfig:
    return EndpointScalingConfig(
        workers_min=workers.minimum,
        workers_max=workers.maximum,
        idle_timeout=workers.idle_timeout_seconds,
        flashboot=flashboot,
        scaler_type=scaling.type,
        scaler_value=scaling.value,
    )


def _resource_id(value: str, *, operation: str, resource_type: str) -> str:
    normalized = value.strip()
    if not _RESOURCE_ID_RE.fullmatch(normalized):
        raise RunPodControlPlaneError(
            "invalid_resource_id",
            "RunPod resource identifier is invalid",
            operation=operation,
            resource_type=resource_type,
        )
    return normalized


# Carrier-grade NAT (RFC 6598, a /10 at 0x64400000) is where RunPod's HTTP proxy entries
# live. It is built from its integer form because repository text policy forbids CGNAT
# address literals.
CARRIER_GRADE_NAT = ipaddress.IPv4Network((0x64400000, 10))
_NON_PUBLIC_NETWORKS = (
    CARRIER_GRADE_NAT,
    *(
        ipaddress.ip_network(net)
        for net in (
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "127.0.0.0/8",
            "169.254.0.0/16",
            "::1/128",
            "fc00::/7",
            "fe80::/10",
        )
    ),
)


def _publicly_routable(raw: object) -> str | None:
    text = _optional_string(raw)
    if text is None:
        return None
    try:
        address = ipaddress.ip_address(text)
    except ValueError:
        return None
    if any(address.version == net.version and address in net for net in _NON_PUBLIC_NETWORKS):
        return None
    return text


def _pod_connection(value: Mapping[str, Any]) -> tuple[str | None, dict[str, int]]:
    """The caller's own pod address from any RunPod pod shape.

    REST v1 carries ``publicIp`` and ``portMappings``; GraphQL carries ``runtime.ports`` with
    ``privatePort``/``publicPort``/``isIpPublic``; the REST v2 shape the broker reads carries
    ``runtime.ports`` with ``private``/``public``/``ip`` and no public flag, so an entry counts
    as public when its address is not private, carrier-grade NAT, loopback, or link-local.
    """
    mappings: dict[str, int] = {}
    raw = value.get("portMappings")
    if isinstance(raw, Mapping):
        for private, public in raw.items():
            port = _optional_int(public)
            if port is not None and str(private).isdigit():
                mappings[str(private)] = port
    public_ip = _publicly_routable(value.get("publicIp"))
    for entry in _mapping(value.get("runtime")).get("ports") or []:
        if not isinstance(entry, Mapping):
            continue
        if "isIpPublic" in entry:
            address = _optional_string(entry.get("ip")) if entry.get("isIpPublic") is True else None
        else:
            address = _publicly_routable(entry.get("ip"))
        if address is None:
            continue
        private = _optional_int(entry.get("privatePort", entry.get("private")))
        public = _optional_int(entry.get("publicPort", entry.get("public")))
        if private is not None and public is not None:
            mappings.setdefault(str(private), public)
        public_ip = public_ip or address
    return public_ip, mappings


def _pod_resource(value: Mapping[str, Any]) -> PodResource:
    machine = _mapping(value.get("machine"))
    gpu = _mapping(value.get("gpu"))
    resource_id = _provider_resource_id(value.get("id"), "pod")
    public_ip, port_mappings = _pod_connection(value)
    return PodResource(
        id=resource_id,
        name=str(value.get("name") or resource_id),
        status=str(value.get("desiredStatus") or value.get("status") or "unknown").lower(),
        image=_optional_string(value.get("image") or value.get("imageName")),
        gpu_type_id=_optional_string(
            value.get("gpuTypeId") or gpu.get("id") or machine.get("gpuTypeId")
        ),
        gpu_count=_optional_int(value.get("gpuCount") or gpu.get("count")),
        cost_per_hour=_optional_decimal(
            value.get("costPerHr") or value.get("costPerHour") or value.get("costPerHourUsd")
        ),
        uptime_seconds=_optional_int(value.get("uptimeSeconds") or value.get("uptimeInSeconds")),
        public_ip=public_ip,
        port_mappings=port_mappings,
    )


def _endpoint_resource(value: Endpoint) -> EndpointResource:
    raw = value.raw
    gpu = _mapping(raw.get("gpu"))
    return EndpointResource(
        id=_provider_resource_id(value.id, "endpoint"),
        name=value.name,
        endpoint_type=_optional_string(raw.get("type")),
        workers=EndpointWorkersRequest(
            minimum=value.scaling.workers_min,
            maximum=value.scaling.workers_max,
            idle_timeout_seconds=value.scaling.idle_timeout,
        ),
        scaling=EndpointScalingRequest(
            type=value.scaling.scaler_type,
            value=value.scaling.scaler_value,
        ),
        flashboot=value.scaling.flashboot,
        template_id=value.template_id,
        image=_optional_string(raw.get("image") or raw.get("imageName")),
        gpu_pools=tuple(str(item) for item in (gpu.get("pools") or []) if str(item)),
        excluded_gpu_type_ids=tuple(
            str(item) for item in (gpu.get("excludedTypes") or []) if str(item)
        ),
        gpu_count=_optional_int(gpu.get("count")),
        created_at=value.created_at,
    )


def _template_resource(value: Template) -> TemplateResource:
    ports = tuple(part.strip() for part in value.ports.split(",") if part.strip())
    env_keys = non_secret_env_keys(item.key for item in value.env or [])
    return TemplateResource(
        id=_provider_resource_id(value.id, "template"),
        name=value.name,
        image=value.image_name,
        docker_args_fingerprint=_opaque_fingerprint(value.docker_args),
        env_fingerprint=_environment_fingerprint(
            (item.key, item.value) for item in value.env or []
        ),
        disk_gb=value.container_disk_in_gb,
        volume_gb=value.volume_in_gb,
        volume_mount_path=value.volume_mount_path,
        ports=ports,
        env_keys=env_keys,
        serverless=value.is_serverless,
        public=value.is_public,
        registry_auth_id=value.registry_auth_id,
    )


def _hub_template_resource(value: HubTemplate) -> HubTemplateResource:
    return HubTemplateResource(
        id=_provider_resource_id(value.id, "hub_template"),
        name=value.name,
        display_name=value.display_name,
        image=value.image_name,
        description=value.description or value.template_description,
        github_url=value.github_url,
        serverless=value.is_serverless,
        env_keys=non_secret_env_keys(item.key for item in value.env or []),
    )


def _volume_resource(value: NetworkVolume) -> VolumeResource:
    return VolumeResource(
        id=_provider_resource_id(value.id, "volume"),
        name=value.name,
        size_gb=value.size,
        data_center_id=value.data_center_id,
        type=value.type,
    )


def _registry_resource(value: ContainerRegistryAuth) -> RegistryAuthResource:
    return RegistryAuthResource(
        id=_provider_resource_id(value.id, "registry_auth"), name=value.name
    )


def _provider_resource_id(value: object, resource_type: str) -> str:
    normalized = str(value or "").strip()
    if not _RESOURCE_ID_RE.fullmatch(normalized):
        raise RunPodControlPlaneError(
            "malformed_provider_response",
            "RunPod response contained an invalid resource identifier",
            operation="normalize",
            resource_type=resource_type,
        )
    return normalized


def _preview(
    request: MutationRequest,
    operation: str,
    resource_type: str,
    resource_id: str | None,
    *,
    effect: str,
    ceiling: str | None = None,
    irreversible: bool = False,
) -> MutationResult:
    return MutationResult(
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        dry_run=True,
        changed=False,
        effect=effect,
        estimated_ceiling=ceiling,
        irreversible=irreversible,
        idempotency_key=request.idempotency_key,
    )


def _changed_result(
    request: MutationRequest,
    operation: str,
    resource_type: str,
    resource_id: str | None,
    effect: str,
    *,
    ceiling: str | None = None,
    irreversible: bool = False,
    resource: PitwallModel | None = None,
) -> MutationResult:
    return MutationResult(
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        dry_run=False,
        changed=True,
        effect=effect,
        estimated_ceiling=ceiling,
        irreversible=irreversible,
        idempotency_key=request.idempotency_key,
        resource=resource.model_dump(mode="json") if resource is not None else None,
    )


def _absent_result(
    request: IdentifiedMutationRequest, operation: str, resource_type: str
) -> MutationResult:
    return MutationResult(
        operation=operation,
        resource_type=resource_type,
        resource_id=request.resource_id,
        dry_run=False,
        changed=False,
        already_absent=True,
        effect=f"{resource_type} {request.resource_id} was already absent",
        irreversible=True,
        idempotency_key=request.idempotency_key,
    )


def _not_found(
    operation: str, resource_type: str, resource_id: str | None
) -> RunPodControlPlaneError:
    return RunPodControlPlaneError(
        "resource_not_found",
        f"RunPod {resource_type} was not found",
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _provider_error(
    exc: Exception,
    *,
    operation: str,
    resource_type: str,
    resource_id: str | None,
    status: int | None,
    secrets: tuple[str, ...],
    retryable_override: bool | None = None,
) -> RunPodControlPlaneError:
    safe = redact_text(str(exc)[:1024], secrets=secrets)
    retryable = (
        retryable_override
        if retryable_override is not None
        else status == 429 or (status is not None and status >= 500)
    )
    return RunPodControlPlaneError(
        "provider_error",
        safe or "RunPod provider request failed",
        operation=operation,
        resource_type=resource_type,
        resource_id=resource_id,
        retryable=retryable,
        provider_status=status,
    )


def _present_fields(model: PitwallModel, fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(field for field in fields if getattr(model, field) is not None)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, str | int | float | Decimal):
        return None
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _optional_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except Exception:  # reason: malformed optional provider telemetry is rendered as unavailable
        return None


def _opaque_fingerprint(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _environment_fingerprint(values: Iterable[tuple[str, str]]) -> str | None:
    items = sorted(values)
    if not items:
        return None
    encoded = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, dict):
        return value
    return {}


__all__ = [
    "ControlPlaneMutationAudit",
    "EndpointCreateRequest",
    "EndpointGpuRequest",
    "EndpointResource",
    "EndpointScalingRequest",
    "EndpointUpdateRequest",
    "EndpointWorkersRequest",
    "HubTemplateResource",
    "IdentifiedMutationRequest",
    "JournalEntry",
    "MutationRequest",
    "MutationResult",
    "PodActionRequest",
    "PodCreateRequest",
    "PodResource",
    "PodUpdateRequest",
    "PostgresRunPodMutationJournal",
    "RegistryAuthCreateRequest",
    "RegistryAuthReplaceRequest",
    "RegistryAuthResource",
    "RunPodControlPlaneError",
    "RunPodControlPlaneService",
    "RunPodMutationJournal",
    "StrictRunPodBackend",
    "TemplateCreateRequest",
    "TemplateResource",
    "TemplateUpdateRequest",
    "VolumeCreateRequest",
    "VolumeGrowRequest",
    "VolumeResource",
]
