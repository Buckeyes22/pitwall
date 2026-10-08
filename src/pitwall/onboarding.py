"""Resumable, idempotent RunPod onboarding over existing Pitwall services.

The fixed sequence in this module is intentionally not a workflow engine.  It
plans one RunPod topology, detects completion from current provider and broker
state, records only safe ownership/evidence in ``config_audit``, and delegates
all resource writes to :mod:`pitwall.runpod_control_plane`.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import ConfigDict, Field, model_validator

from pitwall.api.provider_schemas import (
    expected_lb_base_url,
    expected_openai_base_url,
    validate_provider_registration_config,
)
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    ProviderAdapterId,
    ProviderType,
)
from pitwall.core.models import (
    Capability,
    CapabilityDefaults,
    CredentialReferenceName,
    JsonObject,
    PitwallModel,
    Provider,
)
from pitwall.cost.estimator import quote_cost
from pitwall.db.repository import (
    CapabilityRepository,
    ProviderRepository,
    insert_audit,
    list_audit,
)
from pitwall.resolver import resolve_capability
from pitwall.routing import RoutingRequest
from pitwall.runpod_client.discovery import GpuDiscoveryService, GpuDiscoverySnapshot
from pitwall.runpod_client.gpu import validate_canonical_gpu_names
from pitwall.runpod_client.graphql import RUNPOD_GRAPHQL_URL, RunpodGraphQLClient
from pitwall.runpod_client.pods import argv_to_v2_args
from pitwall.runpod_client.templates import non_secret_env_keys
from pitwall.runpod_control_plane import (
    UNKNOWN_OUTCOME_GRACE_S,
    EndpointCreateRequest,
    EndpointGpuRequest,
    EndpointResource,
    EndpointScalingRequest,
    EndpointWorkersRequest,
    IdentifiedMutationRequest,
    JournalEntry,
    MutationResult,
    PodCreateRequest,
    RegistryAuthCreateRequest,
    RegistryAuthResource,
    RunPodControlPlaneError,
    RunPodControlPlaneService,
    TemplateCreateRequest,
    TemplateResource,
    VolumeCreateRequest,
    VolumeResource,
)
from pitwall.security.pre_spend import (
    PreSpendDecision,
    PreSpendInspectionService,
    get_pre_spend_inspection_service,
)

_SAFE_NAME = r"^[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,127}$"
_SAFE_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
_PLAN_PREFIX = "runpod_onboard_"
_AUDIT_ENTITY_TYPE = "provider"
_MAX_AUDIT_EVENTS = 200
_USD_QUANTUM = Decimal("0.000001")
_ONBOARDING_LOCK_ID = int.from_bytes(
    hashlib.sha256(b"pitwall.runpod.onboarding.apply").digest()[:8],
    byteorder="big",
    signed=True,
)


class OnboardingAction(StrEnum):
    """Operator intent; planning is deliberately the default."""

    PLAN = "plan"
    APPLY = "apply"
    STATUS = "status"
    RESUME = "resume"
    ROLLBACK = "rollback"


class OnboardingTopology(StrEnum):
    ENDPOINT = "endpoint"
    POD_LEASE = "pod_lease"


class ResourceMode(StrEnum):
    NONE = "none"
    EXISTING = "existing"
    CREATE = "create"


class StepState(StrEnum):
    PLANNED = "planned"
    PENDING = "pending"
    REUSED = "reused"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATED = "compensated"
    RETAINED = "retained"
    SKIPPED = "skipped"


class OnboardingStatus(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    FAILED = "failed"
    COMPLETE = "complete"


class RegistrySelection(PitwallModel):
    mode: ResourceMode = ResourceMode.NONE
    resource_id: str | None = Field(default=None, pattern=_SAFE_ID)
    name: str | None = Field(default=None, pattern=_SAFE_NAME)
    username: str | None = Field(default=None, min_length=1, max_length=256)
    password_env: CredentialReferenceName | None = None

    @model_validator(mode="after")
    def _valid_selection(self) -> RegistrySelection:
        if self.mode == ResourceMode.NONE:
            if any((self.resource_id, self.name, self.username, self.password_env)):
                raise ValueError("registry mode none cannot include registry fields")
        elif self.mode == ResourceMode.EXISTING:
            if self.resource_id is None:
                raise ValueError("existing registry auth requires resource_id")
            if any((self.name, self.username, self.password_env)):
                raise ValueError("existing registry auth accepts only resource_id")
        elif not all((self.name, self.username, self.password_env)):
            raise ValueError("registry auth creation requires name, username, and password_env")
        return self


class VolumeSelection(PitwallModel):
    mode: ResourceMode = ResourceMode.NONE
    resource_id: str | None = Field(default=None, pattern=_SAFE_ID)
    name: str | None = Field(default=None, pattern=_SAFE_NAME)
    size_gb: int | None = Field(default=None, ge=10, le=4096)
    data_center_id: str | None = Field(default=None, pattern=_SAFE_ID)

    @model_validator(mode="after")
    def _valid_selection(self) -> VolumeSelection:
        if self.mode == ResourceMode.NONE:
            if any((self.resource_id, self.name, self.size_gb, self.data_center_id)):
                raise ValueError("volume mode none cannot include volume fields")
        elif self.mode == ResourceMode.EXISTING:
            if self.resource_id is None:
                raise ValueError("existing volume requires resource_id")
            if any((self.name, self.size_gb, self.data_center_id)):
                raise ValueError("existing volume accepts only resource_id")
        elif not all((self.name, self.size_gb, self.data_center_id)):
            raise ValueError("volume creation requires name, size_gb, and data_center_id")
        return self


class TemplateSelection(PitwallModel):
    mode: ResourceMode = ResourceMode.CREATE
    resource_id: str | None = Field(default=None, pattern=_SAFE_ID)
    name: str | None = Field(default=None, pattern=_SAFE_NAME)
    disk_gb: int = Field(default=50, ge=10, le=4096)
    args: list[str] = Field(default_factory=list, max_length=256)
    env: dict[str, str] = Field(default_factory=dict)
    ports: list[str] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def _valid_selection(self) -> TemplateSelection:
        if self.mode == ResourceMode.NONE:
            if self.resource_id is not None or self.name is not None:
                raise ValueError("template mode none cannot include an id or name")
        elif self.mode == ResourceMode.EXISTING:
            if self.resource_id is None:
                raise ValueError("existing template requires resource_id")
            if self.name is not None:
                raise ValueError("existing template accepts only resource_id and local defaults")
        elif self.name is None:
            raise ValueError("template creation requires name")
        return self


class EndpointSelection(PitwallModel):
    mode: ResourceMode = ResourceMode.CREATE
    resource_id: str | None = Field(default=None, pattern=_SAFE_ID)
    name: str | None = Field(default=None, pattern=_SAFE_NAME)
    endpoint_type: Literal["QUEUE", "LOAD_BALANCER"] = "QUEUE"
    workers_min: int = Field(default=0, ge=0, le=100)
    workers_max: int = Field(default=3, ge=1, le=100)
    idle_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    scaler_type: Literal["QUEUE_DELAY", "REQUEST_COUNT"] = "QUEUE_DELAY"
    scaler_value: float = Field(default=4.0, gt=0)
    flashboot: bool = False

    @model_validator(mode="after")
    def _valid_selection(self) -> EndpointSelection:
        if self.workers_min > self.workers_max:
            raise ValueError("endpoint workers_min must not exceed workers_max")
        if self.endpoint_type == "LOAD_BALANCER" and self.scaler_type != "REQUEST_COUNT":
            raise ValueError("LOAD_BALANCER endpoints require REQUEST_COUNT scaling")
        if self.mode == ResourceMode.NONE:
            if self.resource_id is not None or self.name is not None:
                raise ValueError("endpoint mode none cannot include an id or name")
        elif self.mode == ResourceMode.EXISTING:
            if self.resource_id is None:
                raise ValueError("existing endpoint requires resource_id")
            if self.name is not None:
                raise ValueError("existing endpoint accepts only resource_id and local defaults")
        elif self.name is None:
            raise ValueError("endpoint creation requires name")
        return self


class RunPodOnboardingRequest(PitwallModel):
    """One deterministic desired RunPod topology, free of credential values."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=128, pattern=_SAFE_NAME)
    topology: OnboardingTopology = OnboardingTopology.ENDPOINT
    capability_name: str = Field(min_length=1, max_length=255)
    capability_class: CapabilityClass = CapabilityClass.CUSTOM
    provider_name: str = Field(min_length=1, max_length=128, pattern=_SAFE_NAME)
    credential_ref: CredentialReferenceName = "RUNPOD_API_KEY"
    image: str = Field(min_length=1, max_length=512)
    public_image: bool = True
    gpu_type_ids: list[str] = Field(min_length=1, max_length=16)
    gpu_count: int = Field(default=1, ge=1, le=8)
    cloud: Literal["ALL", "COMMUNITY", "SECURE"] = "ALL"
    data_center_id: str | None = Field(default=None, pattern=_SAFE_ID)
    rate_per_hour_usd: Decimal = Field(gt=0, max_digits=12, decimal_places=6)
    execution_timeout_seconds: int = Field(default=600, ge=1, le=7200)
    registry: RegistrySelection = Field(default_factory=RegistrySelection)
    volume: VolumeSelection = Field(default_factory=VolumeSelection)
    template: TemplateSelection
    endpoint: EndpointSelection
    probe_payload: JsonObject = Field(default_factory=dict)
    describe_inference_probe: bool = False

    @model_validator(mode="after")
    def _valid_topology(self) -> RunPodOnboardingRequest:
        self.gpu_type_ids = validate_canonical_gpu_names(self.gpu_type_ids)
        if len(set(self.gpu_type_ids)) != len(self.gpu_type_ids):
            raise ValueError("gpu_type_ids must not contain duplicates")
        if not self.public_image and self.registry.mode == ResourceMode.NONE:
            raise ValueError("private images require existing or created registry auth")
        if self.public_image and self.registry.mode != ResourceMode.NONE:
            raise ValueError("public images must not configure registry auth")
        if self.topology == OnboardingTopology.ENDPOINT:
            if self.endpoint.mode == ResourceMode.NONE:
                raise ValueError("endpoint topology requires an endpoint selection")
            if not self.public_image and self.template.mode == ResourceMode.NONE:
                raise ValueError(
                    "private endpoint images require a template that carries registry auth"
                )
        elif self.endpoint.mode != ResourceMode.NONE:
            raise ValueError("pod_lease topology requires endpoint mode none")
        if self.volume.mode == ResourceMode.CREATE:
            if (
                self.data_center_id is not None
                and self.volume.data_center_id != self.data_center_id
            ):
                raise ValueError("volume and topology data_center_id values must match")
            if self.cloud != "SECURE":
                raise ValueError("new network volumes require cloud=SECURE")
        if self.credential_ref != "RUNPOD_API_KEY":
            raise ValueError("RunPod onboarding currently requires credential_ref=RUNPOD_API_KEY")
        return self


class OnboardingCommand(PitwallModel):
    action: OnboardingAction = OnboardingAction.PLAN
    request: RunPodOnboardingRequest
    confirmed_plan_id: str | None = Field(default=None, pattern=r"^runpod_onboard_[a-f0-9]{24}$")

    @model_validator(mode="after")
    def _requires_confirmation(self) -> OnboardingCommand:
        if self.action in {OnboardingAction.APPLY, OnboardingAction.RESUME}:
            if self.confirmed_plan_id is None:
                raise ValueError("apply and resume require confirmed_plan_id from plan output")
        elif self.confirmed_plan_id is not None:
            raise ValueError("confirmed_plan_id is accepted only for apply or resume")
        return self


class OnboardingStep(PitwallModel):
    key: str
    order: int = Field(ge=1)
    state: StepState
    effect: str
    writes: tuple[str, ...] = ()
    resource_type: str | None = None
    resource_id: str | None = None
    reused: bool = False
    rollback: str


class DryRunEvidence(PitwallModel):
    zero_write: Literal[True] = True
    guardrail_decision: Literal["allow", "redact"]
    selected_provider_id: str
    provider_type: ProviderType
    eligible_provider_ids: tuple[str, ...]
    discovered_rate_per_hour_usd: Decimal
    cost_quote: JsonObject
    request_kind: Literal["endpoint", "pod"]
    request_summary: JsonObject
    inference_probe: str | None = None


class OnboardingCostImpact(PitwallModel):
    """Explicit apply-time cost boundary from the confirmed request."""

    execution_ceiling_usd: Decimal
    provider_rate_per_hour_usd: Decimal
    discovered_rate_per_hour_usd: Decimal
    endpoint_minimum_hourly_usd: Decimal
    endpoint_maximum_hourly_usd: Decimal
    network_volume_gb_created: int | None = None
    network_volume_pricing: Literal["not_created", "provider_rate_unavailable"]
    paid_compute_floor_created_by_onboarding: bool


class OnboardingResult(PitwallModel):
    action: OnboardingAction
    plan_id: str
    status: OnboardingStatus
    topology: OnboardingTopology
    zero_write: bool
    estimated_ceiling_usd: Decimal
    cost_impact: OnboardingCostImpact
    credential_references: tuple[str, ...]
    discovered_gpu_type_ids: tuple[str, ...]
    discovered_data_center_ids: tuple[str, ...]
    provider_resources: tuple[str, ...]
    database_mutations: tuple[str, ...]
    existing_resource_reuse: tuple[str, ...]
    steps: tuple[OnboardingStep, ...]
    next_step: str | None
    can_resume: bool
    rollback_guidance: tuple[str, ...]
    retained_resources: tuple[str, ...] = ()
    dry_run_evidence: DryRunEvidence | None = None
    failure_code: str | None = None


class OnboardingEvent(PitwallModel):
    step: str
    state: StepState
    resource_type: str | None = None
    resource_id: str | None = None
    resource_name: str | None = None
    created_by_plan: bool = False
    detail: str


class OnboardingError(RuntimeError):
    """Stable, redacted onboarding failure safe for every operator surface."""

    def __init__(
        self,
        code: str,
        detail: str,
        *,
        step: str | None = None,
        result: OnboardingResult | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.step = step
        self.result = result

    def to_dict(self) -> dict[str, object]:
        return {
            "error": self.code,
            "detail": self.detail,
            "step": self.step,
            "result": self.result.model_dump(mode="json") if self.result is not None else None,
        }


class OnboardingState(Protocol):
    def apply_lock(self, plan_id: str) -> AbstractAsyncContextManager[None]: ...

    async def get_capability(self, name: str) -> Capability | None: ...

    async def get_capability_by_id(self, capability_id: str) -> Capability | None: ...

    async def get_provider(self, name: str) -> Provider | None: ...

    async def get_provider_by_id(self, provider_id: str) -> Provider | None: ...

    async def create_capability(self, request: RunPodOnboardingRequest) -> Capability: ...

    async def create_provider(
        self,
        request: RunPodOnboardingRequest,
        *,
        capability: Capability,
        endpoint_id: str | None,
        template_id: str | None,
        volume_id: str | None,
    ) -> Provider: ...

    async def enable_provider(self, provider_id: str) -> Provider | None: ...

    async def enable_capability(self, capability_id: str) -> Capability | None: ...

    async def disable_provider(self, provider_id: str) -> None: ...

    async def disable_capability(self, capability_id: str) -> None: ...

    async def load_events(self, plan_id: str) -> tuple[OnboardingEvent, ...]: ...

    async def append_event(self, plan_id: str, event: OnboardingEvent) -> None: ...


class RunPodResources(Protocol):
    async def list_registry_auths(self) -> list[RegistryAuthResource]: ...

    async def get_registry_auth(self, resource_id: str) -> RegistryAuthResource: ...

    async def create_registry_auth(self, request: RegistryAuthCreateRequest) -> MutationResult: ...

    async def delete_registry_auth(self, request: IdentifiedMutationRequest) -> MutationResult: ...

    async def list_volumes(self) -> list[VolumeResource]: ...

    async def get_volume(self, resource_id: str) -> VolumeResource: ...

    async def create_volume(self, request: VolumeCreateRequest) -> MutationResult: ...

    async def list_templates(self) -> list[TemplateResource]: ...

    async def get_template(self, resource_id: str) -> TemplateResource: ...

    async def create_template(self, request: TemplateCreateRequest) -> MutationResult: ...

    async def delete_template(self, request: IdentifiedMutationRequest) -> MutationResult: ...

    async def list_endpoints(self) -> list[EndpointResource]: ...

    async def get_endpoint(self, resource_id: str) -> EndpointResource: ...

    async def resolve_endpoint_gpu_types(
        self, gpu_type_ids: list[str], *, gpu_count: int
    ) -> EndpointGpuRequest: ...

    async def create_endpoint(self, request: EndpointCreateRequest) -> MutationResult: ...

    async def delete_endpoint(self, request: IdentifiedMutationRequest) -> MutationResult: ...

    async def create_pod(self, request: PodCreateRequest) -> MutationResult: ...

    async def release_idempotency_key(self, idempotency_key: str, *, reason: str) -> bool: ...

    async def idempotency_key_status(self, idempotency_key: str) -> JournalEntry | None: ...


class Discovery(Protocol):
    async def get_snapshot(self) -> GpuDiscoverySnapshot: ...

    async def aclose(self) -> None: ...


class EnvironmentRunPodDiscovery:
    """Lazily own one credentialed, cached RunPod discovery client."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._timeout_s = timeout_s
        self._service: GpuDiscoveryService | None = None
        self._lock = asyncio.Lock()

    async def get_snapshot(self) -> GpuDiscoverySnapshot:
        service = self._service
        if service is None:
            async with self._lock:
                service = self._service
                if service is None:
                    api_key = self._environ.get("RUNPOD_API_KEY", "")
                    if not api_key:
                        raise OnboardingError(
                            "credential_reference_unset",
                            "required credential reference is unset: RUNPOD_API_KEY",
                            step="validate_config",
                        )
                    service = GpuDiscoveryService(
                        RunpodGraphQLClient(
                            api_key=api_key,
                            graphql_url=self._environ.get("RUNPOD_GRAPHQL_URL", RUNPOD_GRAPHQL_URL),
                            timeout_s=self._timeout_s,
                        )
                    )
                    self._service = service
        return await service.get_snapshot()

    async def aclose(self) -> None:
        if self._service is not None:
            await self._service.aclose()
            self._service = None


class PostgresOnboardingState:
    """Existing registry plus ``config_audit``; no onboarding schema is added."""

    def __init__(
        self,
        pool: Any,
        *,
        actor: str = "system",
        lock_timeout_s: float = 60.0,
    ) -> None:
        if not 0 < lock_timeout_s <= 300:
            raise ValueError("lock_timeout_s must be greater than zero and at most 300")
        self._pool = pool
        self._actor = actor
        self._lock_timeout_s = lock_timeout_s
        self._capabilities = CapabilityRepository(pool)
        self._providers = ProviderRepository(pool)

    @asynccontextmanager
    async def apply_lock(self, plan_id: str) -> AsyncIterator[None]:
        """Serialize all onboarding writes across processes with one DB lock.

        A single fixed lock also prevents two different plan IDs that reuse the
        same broker or RunPod names from racing and confusing ownership.
        ``plan_id`` remains part of the contract for diagnostics and fakes.
        """

        del plan_id
        try:
            conn = await asyncio.wait_for(
                self._pool.acquire(),
                timeout=self._lock_timeout_s,
            )
        except TimeoutError as exc:
            raise OnboardingError(
                "onboarding_lock_timeout",
                "another onboarding apply is still in progress",
            ) from exc
        except Exception as exc:  # reason: asyncpg pool failures share one stable operator error
            raise OnboardingError(
                "onboarding_lock_unavailable",
                "onboarding serialization is unavailable",
            ) from exc
        locked = False
        try:
            try:
                await asyncio.wait_for(
                    conn.execute("SELECT pg_advisory_lock($1)", _ONBOARDING_LOCK_ID),
                    timeout=self._lock_timeout_s,
                )
                locked = True
            except TimeoutError as exc:
                raise OnboardingError(
                    "onboarding_lock_timeout",
                    "another onboarding apply is still in progress",
                ) from exc
            except (
                Exception
            ) as exc:  # reason: asyncpg lock failures share one stable operator error
                raise OnboardingError(
                    "onboarding_lock_unavailable",
                    "onboarding serialization is unavailable",
                ) from exc
            yield
        finally:
            if locked:
                try:
                    await asyncio.shield(
                        conn.execute("SELECT pg_advisory_unlock($1)", _ONBOARDING_LOCK_ID)
                    )
                except (
                    Exception
                ):  # reason: failed unlock requires terminating the pooled connection
                    # asyncpg normally resets released connections, including
                    # advisory locks. Terminate if explicit unlock is unavailable.
                    conn.terminate()
            await asyncio.shield(self._pool.release(conn))

    async def get_capability(self, name: str) -> Capability | None:
        return await self._capabilities.get_by_name(name)

    async def get_capability_by_id(self, capability_id: str) -> Capability | None:
        return await self._capabilities.get(capability_id)

    async def get_provider(self, name: str) -> Provider | None:
        return await self._providers.get_by_name(name)

    async def get_provider_by_id(self, provider_id: str) -> Provider | None:
        return await self._providers.get(provider_id)

    async def create_capability(self, request: RunPodOnboardingRequest) -> Capability:
        now = dt.datetime.now(dt.UTC)
        capability = _proposed_capability(request, now=now)
        result = await self._capabilities.create(capability)
        await insert_audit(
            self._pool,
            actor=self._actor,
            action="create",
            entity_type="capability",
            entity_id=result.id,
            new_value={"name": result.name, "class": result.class_.value},
            change_reason="RunPod onboarding",
        )
        return result

    async def create_provider(
        self,
        request: RunPodOnboardingRequest,
        *,
        capability: Capability,
        endpoint_id: str | None,
        template_id: str | None,
        volume_id: str | None,
    ) -> Provider:
        provider = _proposed_provider(
            request,
            capability=capability,
            endpoint_id=endpoint_id,
            template_id=template_id,
            volume_id=volume_id,
            now=dt.datetime.now(dt.UTC),
        )
        result = await self._providers.create(provider)
        await insert_audit(
            self._pool,
            actor=self._actor,
            action="create",
            entity_type="provider",
            entity_id=result.id,
            new_value={
                "name": result.name,
                "adapter_id": result.adapter_id.value,
                "credential_ref": result.credential_ref,
                "capability_id": result.capability_id,
            },
            change_reason="RunPod onboarding",
        )
        return result

    async def enable_provider(self, provider_id: str) -> Provider | None:
        result = await self._providers.enable(provider_id)
        if result is not None:
            await insert_audit(
                self._pool,
                actor=self._actor,
                action="enable",
                entity_type="provider",
                entity_id=provider_id,
                change_reason="RunPod onboarding resume",
            )
        return result

    async def enable_capability(self, capability_id: str) -> Capability | None:
        result = await self._capabilities.enable(capability_id)
        if result is not None:
            await insert_audit(
                self._pool,
                actor=self._actor,
                action="enable",
                entity_type="capability",
                entity_id=capability_id,
                change_reason="RunPod onboarding resume",
            )
        return result

    async def disable_provider(self, provider_id: str) -> None:
        if await self._providers.disable(provider_id) is not None:
            await insert_audit(
                self._pool,
                actor=self._actor,
                action="disable",
                entity_type="provider",
                entity_id=provider_id,
                change_reason="RunPod onboarding safe compensation",
            )

    async def disable_capability(self, capability_id: str) -> None:
        if await self._capabilities.disable(capability_id) is not None:
            await insert_audit(
                self._pool,
                actor=self._actor,
                action="disable",
                entity_type="capability",
                entity_id=capability_id,
                change_reason="RunPod onboarding safe compensation",
            )

    async def load_events(self, plan_id: str) -> tuple[OnboardingEvent, ...]:
        entries = await list_audit(
            self._pool,
            entity_type=_AUDIT_ENTITY_TYPE,
            entity_id=plan_id,
            limit=_MAX_AUDIT_EVENTS,
        )
        events: list[OnboardingEvent] = []
        for entry in reversed(entries):
            payload = entry.new_value or {}
            if payload.get("kind") != "runpod_onboarding_event":
                continue
            events.append(OnboardingEvent.model_validate(payload["event"]))
        return tuple(events)

    async def append_event(self, plan_id: str, event: OnboardingEvent) -> None:
        await insert_audit(
            self._pool,
            actor=self._actor,
            action="update",
            entity_type=_AUDIT_ENTITY_TYPE,
            entity_id=plan_id,
            new_value={
                "kind": "runpod_onboarding_event",
                "event": event.model_dump(mode="json"),
            },
            change_reason=f"RunPod onboarding step {event.step}",
        )


@dataclass
class _ApplyState:
    """Mutable progress of one locked apply, shared by its named steps."""

    plan_id: str
    resume: bool = False
    created: list[tuple[str, str, str]] = field(default_factory=list)
    reactivated: list[tuple[str, str, str]] = field(default_factory=list)
    changed: bool = True
    current_step: str = "validate_config"
    capability_create_attempted: bool = False
    provider_create_attempted: bool = False
    endpoint_gpu: EndpointGpuRequest | None = None
    registry_id: str | None = None
    volume_id: str | None = None
    template_id: str | None = None
    endpoint_id: str | None = None
    capability: Capability | None = None
    provider: Provider | None = None


class RunPodOnboardingService:
    """One shared plan/apply/status/resume/rollback-guidance service."""

    def __init__(
        self,
        *,
        state: OnboardingState,
        resources: RunPodResources,
        discovery: Discovery,
        environ: Mapping[str, str] | None = None,
        guardrails: PreSpendInspectionService | None = None,
    ) -> None:
        self._state = state
        self._resources = resources
        self._discovery = discovery
        self._environ = os.environ if environ is None else environ
        self._guardrails = guardrails or get_pre_spend_inspection_service()
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def execute(self, command: OnboardingCommand) -> OnboardingResult:
        if command.action == OnboardingAction.PLAN:
            return await self.plan(command.request)
        if command.action == OnboardingAction.STATUS:
            return await self.status(command.request)
        if command.action == OnboardingAction.ROLLBACK:
            return await self.rollback_guidance(command.request)
        assert command.confirmed_plan_id is not None
        return await self.apply(
            command.request,
            confirmed_plan_id=command.confirmed_plan_id,
            resume=command.action == OnboardingAction.RESUME,
        )

    async def aclose(self) -> None:
        """Close the owned discovery client when the runtime service is retired."""
        await self._discovery.aclose()

    async def plan(self, request: RunPodOnboardingRequest) -> OnboardingResult:
        return await self._observe(request, action=OnboardingAction.PLAN)

    async def status(self, request: RunPodOnboardingRequest) -> OnboardingResult:
        return await self._observe(request, action=OnboardingAction.STATUS)

    async def rollback_guidance(self, request: RunPodOnboardingRequest) -> OnboardingResult:
        return await self._observe(request, action=OnboardingAction.ROLLBACK)

    async def apply(
        self,
        request: RunPodOnboardingRequest,
        *,
        confirmed_plan_id: str,
        resume: bool = False,
    ) -> OnboardingResult:
        observed = await self._observe(
            request,
            action=OnboardingAction.RESUME if resume else OnboardingAction.APPLY,
        )
        if observed.plan_id != confirmed_plan_id:
            raise OnboardingError(
                "plan_confirmation_mismatch",
                "confirmed plan does not match the current desired topology",
            )
        lock = await self._plan_lock(observed.plan_id)
        async with lock, self._state.apply_lock(observed.plan_id):
            observed = await self._observe(
                request,
                action=OnboardingAction.RESUME if resume else OnboardingAction.APPLY,
                record_inspection=True,
            )
            if observed.status == OnboardingStatus.COMPLETE:
                return observed.model_copy(update={"zero_write": True})
            return await self._apply_locked(request, observed, resume=resume)

    async def _observe(
        self,
        request: RunPodOnboardingRequest,
        *,
        action: OnboardingAction,
        record_inspection: bool = False,
    ) -> OnboardingResult:
        safe_payload, scan_decision = self._inspect_request(
            request,
            record=record_inspection,
        )
        plan_id = _plan_id(request, safe_payload)
        self._validate_credential_references(request)
        try:
            snapshot = await self._discovery.get_snapshot()
        except OnboardingError:
            raise
        except Exception as exc:  # reason: discovery transports may include credential detail
            raise OnboardingError(
                "discovery_unavailable",
                "RunPod GPU and datacenter discovery is unavailable",
                step="discover_capacity",
            ) from exc
        discovered_rate = self._validate_discovery(request, snapshot)
        try:
            endpoint_gpu = await self._endpoint_gpu_selection(request)
            events = await self._state.load_events(plan_id)
            inventory = await self._inventory(request, events, endpoint_gpu=endpoint_gpu)
        except OnboardingError:
            raise
        except RunPodControlPlaneError as exc:
            raise OnboardingError(
                f"runpod_{exc.code}",
                "RunPod resource-state inspection failed safely",
                step="detect_state",
            ) from exc
        except Exception as exc:  # reason: persistence/provider detail must remain bounded
            raise OnboardingError(
                "onboarding_state_unavailable",
                "onboarding state inspection is unavailable",
                step="detect_state",
            ) from exc
        steps = self._steps(request, inventory, events)
        status = _status(steps, events)
        evidence = self._dry_run(
            request,
            safe_payload=safe_payload,
            inventory=inventory,
            scan_decision=scan_decision,
            discovered_rate=discovered_rate,
            endpoint_gpu=endpoint_gpu,
        )
        return _result(
            action=action,
            plan_id=plan_id,
            request=request,
            snapshot=snapshot,
            steps=steps,
            status=status,
            evidence=evidence,
            zero_write=True,
            events=events,
        )

    def _inspect_request(
        self,
        request: RunPodOnboardingRequest,
        *,
        record: bool,
    ) -> tuple[JsonObject, PreSpendDecision]:
        """Inspect every operator-controlled field before any external I/O.

        Credential reference *names* are moved under a neutral field so the
        inspector does not mistake them for credential values. Redaction is
        permitted only inside the hypothetical inference probe; resource and
        broker fields must pass unchanged or fail closed.
        """

        payload = request.model_dump(mode="json")
        credential_references = [payload.pop("credential_ref")]
        registry = payload.get("registry")
        if isinstance(registry, dict):
            password_env = registry.pop("password_env", None)
            if password_env is not None:
                credential_references.append(password_env)
        payload["environment_variables"] = credential_references
        inspect = self._guardrails.inspect if record else self._guardrails.preview
        scan = inspect(payload)
        outside_probe = any(
            finding.path != "$.probe_payload"
            and not finding.path.startswith("$.probe_payload.")
            and not finding.path.startswith("$.probe_payload[")
            for finding in scan.findings
        )
        if scan.decision == PreSpendDecision.BLOCK or outside_probe:
            raise OnboardingError(
                "guardrail_rejected",
                "RunPod onboarding request was rejected by pre-spend guardrails",
                step="validate_config",
            )
        redacted = scan.redacted_payload
        safe_payload = redacted.get("probe_payload") if isinstance(redacted, dict) else None
        return (
            safe_payload if isinstance(safe_payload, dict) else {},
            scan.decision,
        )

    async def _apply_locked(
        self,
        request: RunPodOnboardingRequest,
        observed: OnboardingResult,
        *,
        resume: bool,
    ) -> OnboardingResult:
        # Entering apply records durable step evidence even when every provider
        # resource and broker row can be reused, hence ``changed`` starts true.
        state = _ApplyState(plan_id=observed.plan_id, resume=resume)
        try:
            state.endpoint_gpu = await self._endpoint_gpu_selection(request)
            await self._record_completed(
                state.plan_id, state.current_step, "local configuration validated"
            )
            state.current_step = "discover_capacity"
            await self._record_completed(
                state.plan_id, state.current_step, "GPU and datacenter discovery validated"
            )
            events = await self._state.load_events(state.plan_id)
            inventory = await self._inventory(request, events, endpoint_gpu=state.endpoint_gpu)

            await self._apply_locked_registry(request, state, inventory)
            await self._apply_locked_volume(request, state, inventory)
            await self._apply_locked_template(request, state, inventory)
            await self._apply_locked_endpoint(request, state, inventory)
            await self._apply_locked_pod_preview(request, state)
            await self._apply_locked_capability(request, state)
            await self._apply_locked_provider(request, state)
            await self._apply_locked_finish(request, state)
        except asyncio.CancelledError:
            await asyncio.shield(self._apply_locked_cancelled(request, state))
            raise
        except Exception as exc:  # reason: all provider/persistence details are redacted here
            raise await self._apply_locked_failed(request, state, exc, resume=resume) from exc

        return (
            await self._observe(
                request,
                action=OnboardingAction.RESUME if resume else OnboardingAction.APPLY,
            )
        ).model_copy(update={"zero_write": not state.changed})

    async def _apply_locked_registry(
        self,
        request: RunPodOnboardingRequest,
        state: _ApplyState,
        inventory: Any,
    ) -> None:
        plan_id = state.plan_id
        state.registry_id = _resource_id(inventory.registry)
        if request.registry.mode == ResourceMode.CREATE and state.registry_id is None:
            state.current_step = "registry_auth"
            registry_selection = request.registry
            assert (
                registry_selection.name
                and registry_selection.username
                and registry_selection.password_env
            )
            await self._record_intent(
                plan_id, state.current_step, "registry_auth", registry_selection.name
            )
            await self._release_absent_step_key(state)
            mutation = await self._resources.create_registry_auth(
                RegistryAuthCreateRequest(
                    intent="apply",
                    idempotency_key=_step_idempotency_key(plan_id, state.current_step),
                    name=registry_selection.name,
                    username=registry_selection.username,
                    password_env=registry_selection.password_env,
                )
            )
            state.registry_id = _required_result_id(mutation, state.current_step)
            state.created.append((state.current_step, "registry_auth", state.registry_id))
            state.changed = True
            await self._record_resource(
                plan_id,
                state.current_step,
                "registry_auth",
                state.registry_id,
                registry_selection.name,
            )
        elif state.registry_id is not None:
            await self._record_reused(plan_id, "registry_auth", "registry_auth", state.registry_id)

    async def _apply_locked_volume(
        self,
        request: RunPodOnboardingRequest,
        state: _ApplyState,
        inventory: Any,
    ) -> None:
        plan_id = state.plan_id
        state.volume_id = _resource_id(inventory.volume)
        if request.volume.mode == ResourceMode.CREATE and state.volume_id is None:
            state.current_step = "volume"
            volume_selection = request.volume
            assert (
                volume_selection.name
                and volume_selection.size_gb
                and volume_selection.data_center_id
            )
            await self._record_intent(plan_id, state.current_step, "volume", volume_selection.name)
            await self._release_absent_step_key(state)
            mutation = await self._resources.create_volume(
                VolumeCreateRequest(
                    intent="apply",
                    idempotency_key=_step_idempotency_key(plan_id, state.current_step),
                    name=volume_selection.name,
                    size_gb=volume_selection.size_gb,
                    data_center_id=volume_selection.data_center_id,
                )
            )
            state.volume_id = _required_result_id(mutation, state.current_step)
            state.created.append((state.current_step, "volume", state.volume_id))
            state.changed = True
            await self._record_resource(
                plan_id, state.current_step, "volume", state.volume_id, volume_selection.name
            )
        elif state.volume_id is not None:
            await self._record_reused(plan_id, "volume", "volume", state.volume_id)

    async def _apply_locked_template(
        self,
        request: RunPodOnboardingRequest,
        state: _ApplyState,
        inventory: Any,
    ) -> None:
        plan_id = state.plan_id
        state.template_id = _resource_id(inventory.template)
        if request.template.mode == ResourceMode.CREATE and state.template_id is None:
            state.current_step = "template"
            template_selection = request.template
            assert template_selection.name
            await self._record_intent(
                plan_id, state.current_step, "template", template_selection.name
            )
            await self._release_absent_step_key(state)
            mutation = await self._resources.create_template(
                TemplateCreateRequest(
                    intent="apply",
                    idempotency_key=_step_idempotency_key(plan_id, state.current_step),
                    name=template_selection.name,
                    image=request.image,
                    disk_gb=template_selection.disk_gb,
                    volume_gb=0,
                    args=template_selection.args,
                    env=template_selection.env,
                    serverless=request.topology == OnboardingTopology.ENDPOINT,
                    registry_auth_id=state.registry_id,
                    ports=template_selection.ports,
                )
            )
            state.template_id = _required_result_id(mutation, state.current_step)
            state.created.append((state.current_step, "template", state.template_id))
            state.changed = True
            await self._record_resource(
                plan_id, state.current_step, "template", state.template_id, template_selection.name
            )
        elif state.template_id is not None:
            await self._record_reused(plan_id, "template", "template", state.template_id)

    async def _apply_locked_endpoint(
        self,
        request: RunPodOnboardingRequest,
        state: _ApplyState,
        inventory: Any,
    ) -> None:
        plan_id = state.plan_id
        state.endpoint_id = _resource_id(inventory.endpoint)
        if (
            request.topology == OnboardingTopology.ENDPOINT
            and request.endpoint.mode == ResourceMode.CREATE
            and state.endpoint_id is None
        ):
            state.current_step = "endpoint"
            assert request.endpoint.name
            assert state.endpoint_gpu is not None
            await self._record_intent(
                plan_id, state.current_step, "endpoint", request.endpoint.name
            )
            await self._release_absent_step_key(state)
            mutation = await self._resources.create_endpoint(
                _endpoint_create_request(
                    request,
                    plan_id=plan_id,
                    intent="apply",
                    template_id=state.template_id,
                    gpu=state.endpoint_gpu,
                )
            )
            state.endpoint_id = _required_result_id(mutation, state.current_step)
            state.created.append((state.current_step, "endpoint", state.endpoint_id))
            state.changed = True
            await self._record_resource(
                plan_id,
                state.current_step,
                "endpoint",
                state.endpoint_id,
                request.endpoint.name,
            )
        elif state.endpoint_id is not None:
            await self._record_reused(plan_id, "endpoint", "endpoint", state.endpoint_id)

    async def _apply_locked_pod_preview(
        self, request: RunPodOnboardingRequest, state: _ApplyState
    ) -> None:
        if request.topology != OnboardingTopology.POD_LEASE:
            return
        state.current_step = "pod_request_preview"
        await self._resources.create_pod(
            _pod_create_request(
                request,
                plan_id=state.plan_id,
                intent="preview",
                template_id=state.template_id,
                volume_id=state.volume_id,
                registry_id=state.registry_id,
            )
        )
        await self._record_completed(
            state.plan_id,
            state.current_step,
            "validated pod request without creating paid compute",
        )

    async def _apply_locked_capability(
        self, request: RunPodOnboardingRequest, state: _ApplyState
    ) -> None:
        state.current_step = "capability"
        capability = await self._state.get_capability(request.capability_name)
        if capability is None:
            state.capability_create_attempted = True
            capability = await self._state.create_capability(request)
            state.created.append((state.current_step, "capability", capability.id))
            state.changed = True
            await self._record_resource(
                state.plan_id, state.current_step, "capability", capability.id
            )
        else:
            _validate_capability_match(request, capability)
            if not capability.enabled:
                state.reactivated.append((state.current_step, "capability", capability.id))
                capability = await self._state.enable_capability(capability.id)
                if capability is None:
                    raise OnboardingError(
                        "capability_disappeared",
                        "registered capability disappeared during resume",
                        step=state.current_step,
                    )
                state.changed = True
            await self._record_reused(
                state.plan_id, state.current_step, "capability", capability.id
            )
        state.capability = capability

    async def _apply_locked_provider(
        self, request: RunPodOnboardingRequest, state: _ApplyState
    ) -> None:
        state.current_step = "provider"
        capability = state.capability
        assert capability is not None
        provider = await self._state.get_provider(request.provider_name)
        if provider is None:
            state.provider_create_attempted = True
            provider = await self._state.create_provider(
                request,
                capability=capability,
                endpoint_id=state.endpoint_id,
                template_id=state.template_id,
                volume_id=state.volume_id,
            )
            state.created.append((state.current_step, "provider", provider.id))
            state.changed = True
            await self._record_resource(state.plan_id, state.current_step, "provider", provider.id)
        else:
            _validate_provider_match(
                request,
                provider,
                capability=capability,
                endpoint_id=state.endpoint_id,
                template_id=state.template_id,
                volume_id=state.volume_id,
            )
            if not provider.enabled:
                state.reactivated.append((state.current_step, "provider", provider.id))
                provider = await self._state.enable_provider(provider.id)
                if provider is None:
                    raise OnboardingError(
                        "provider_disappeared",
                        "registered provider disappeared during resume",
                        step=state.current_step,
                    )
                state.changed = True
            await self._record_reused(state.plan_id, state.current_step, "provider", provider.id)
        state.provider = provider

    async def _apply_locked_finish(
        self, request: RunPodOnboardingRequest, state: _ApplyState
    ) -> None:
        assert state.capability is not None
        assert state.provider is not None
        state.current_step = "final_dry_run"
        await self._production_dry_run(
            request,
            capability=state.capability,
            provider=state.provider,
            template_id=state.template_id,
            volume_id=state.volume_id,
            registry_id=state.registry_id,
            plan_id=state.plan_id,
        )
        await self._record_completed(
            state.plan_id,
            state.current_step,
            "production resolution, cost, guardrail, and request construction passed",
        )
        state.current_step = "complete"
        await self._record_completed(
            state.plan_id, state.current_step, "RunPod onboarding complete"
        )

    async def _apply_locked_cancelled(
        self, request: RunPodOnboardingRequest, state: _ApplyState
    ) -> None:
        """Record and compensate what a cancelled apply created; runs shielded."""
        await self._capture_ambiguous_broker_records(
            request,
            state.created,
            capability_attempted=state.capability_create_attempted,
            provider_attempted=state.provider_create_attempted,
        )
        await self._safe_failure_event(state.plan_id, state.current_step)
        await self._compensate(
            state.plan_id,
            state.created,
            reactivated=state.reactivated,
            preserve_dependencies=_has_ambiguous_dependencies(state.current_step),
        )

    async def _apply_locked_failed(
        self,
        request: RunPodOnboardingRequest,
        state: _ApplyState,
        exc: Exception,
        *,
        resume: bool,
    ) -> OnboardingError:
        """Compensate a failed apply and build the typed error the caller raises."""
        await self._capture_ambiguous_broker_records(
            request,
            state.created,
            capability_attempted=state.capability_create_attempted,
            provider_attempted=state.provider_create_attempted,
        )
        await self._safe_failure_event(state.plan_id, state.current_step)
        retained = await self._compensate(
            state.plan_id,
            state.created,
            reactivated=state.reactivated,
            preserve_dependencies=_has_ambiguous_dependencies(state.current_step),
        )
        failed = await self._observe(
            request,
            action=OnboardingAction.RESUME if resume else OnboardingAction.APPLY,
        )
        failed = failed.model_copy(
            update={
                "status": OnboardingStatus.FAILED,
                "failure_code": _failure_code(exc),
                "retained_resources": tuple(retained),
                "zero_write": False,
            }
        )
        if isinstance(exc, OnboardingError):
            return OnboardingError(
                exc.code,
                exc.detail,
                step=exc.step or state.current_step,
                result=failed,
            )
        if isinstance(exc, RunPodControlPlaneError) and exc.code == "mutation_outcome_ambiguous":
            return OnboardingError(
                _failure_code(exc),
                _ambiguous_step_remedy(request, state.current_step),
                step=state.current_step,
                result=failed,
            )
        return OnboardingError(
            _failure_code(exc),
            "RunPod onboarding step failed; status is safe to inspect and resume",
            step=state.current_step,
            result=failed,
        )

    async def _production_dry_run(
        self,
        request: RunPodOnboardingRequest,
        *,
        capability: Capability,
        provider: Provider,
        template_id: str | None,
        volume_id: str | None,
        registry_id: str | None,
        plan_id: str,
    ) -> None:
        payload, _ = self._inspect_request(request, record=False)
        resolution = await resolve_capability(
            request.capability_name,
            capability_repo=_OnboardingCapabilityResolver(self._state),
            provider_repo=_OnboardingProviderResolver(self._state),
            provider_id=provider.id,
            request=RoutingRequest(
                capability_name=request.capability_name,
                capability_id=capability.id,
                payload_bytes=len(_canonical_json(payload).encode("utf-8")),
            ),
        )
        quote_cost(
            capability=capability, provider_cost=provider.config, payload=payload
        ).upper_bound()
        if request.topology == OnboardingTopology.ENDPOINT:
            if provider.runpod_endpoint_id is None:
                raise OnboardingError(
                    "endpoint_not_ready",
                    "registered endpoint provider has no endpoint id",
                    step="final_dry_run",
                )
            expected = _provider_type(request)
            expected_openai_base_url(expected, provider.runpod_endpoint_id)
        else:
            await self._resources.create_pod(
                _pod_create_request(
                    request,
                    plan_id=plan_id,
                    intent="preview",
                    template_id=template_id,
                    volume_id=volume_id,
                    registry_id=registry_id,
                )
            )
        if resolution.provider.id != provider.id:
            raise OnboardingError(
                "resolution_mismatch",
                "production dry-run selected a different provider",
                step="final_dry_run",
            )

    async def _inventory(
        self,
        request: RunPodOnboardingRequest,
        events: tuple[OnboardingEvent, ...],
        *,
        endpoint_gpu: EndpointGpuRequest | None,
    ) -> _Inventory:
        owned = _owned_resources(events)
        owned_names = _owned_resource_names(events)
        registry = await self._registry_inventory(request.registry, owned, owned_names)
        volume = await self._volume_inventory(request.volume, owned, owned_names)
        template = await self._template_inventory(
            request,
            owned,
            owned_names,
            registry_id=_resource_id(registry),
        )
        endpoint = await self._endpoint_inventory(
            request,
            owned,
            owned_names,
            template_id=_resource_id(template),
            gpu=endpoint_gpu,
        )
        capability = await self._state.get_capability(request.capability_name)
        if capability is not None:
            _validate_capability_match(request, capability)
        provider = await self._state.get_provider(request.provider_name)
        if provider is not None and capability is not None:
            _validate_provider_match(
                request,
                provider,
                capability=capability,
                endpoint_id=_resource_id(endpoint),
                template_id=_resource_id(template),
                volume_id=_resource_id(volume),
            )
        return _Inventory(
            registry=registry,
            volume=volume,
            template=template,
            endpoint=endpoint,
            capability=capability,
            provider=provider,
        )

    async def _registry_inventory(
        self,
        selection: RegistrySelection,
        owned: Mapping[str, set[str]],
        owned_names: Mapping[str, set[str]],
    ) -> RegistryAuthResource | None:
        if selection.mode == ResourceMode.NONE:
            return None
        if selection.mode == ResourceMode.EXISTING:
            resource_id = selection.resource_id
            assert resource_id
            return await _safe_get(
                lambda: self._resources.get_registry_auth(resource_id),
                "registry_auth",
                resource_id,
            )
        assert selection.name
        values = await self._resources.list_registry_auths()
        duplicate = next((item for item in values if item.name == selection.name), None)
        if (
            duplicate is not None
            and duplicate.id not in owned.get("registry_auth", set())
            and duplicate.name not in owned_names.get("registry_auth", set())
        ):
            raise OnboardingError(
                "unowned_resource_conflict",
                "registry auth name already exists; select it explicitly to reuse it",
                step="registry_auth",
            )
        return duplicate

    async def _volume_inventory(
        self,
        selection: VolumeSelection,
        owned: Mapping[str, set[str]],
        owned_names: Mapping[str, set[str]],
    ) -> VolumeResource | None:
        if selection.mode == ResourceMode.NONE:
            return None
        if selection.mode == ResourceMode.EXISTING:
            resource_id = selection.resource_id
            assert resource_id
            return await _safe_get(
                lambda: self._resources.get_volume(resource_id),
                "volume",
                resource_id,
            )
        assert selection.name and selection.size_gb and selection.data_center_id
        values = await self._resources.list_volumes()
        duplicate = next((item for item in values if item.name == selection.name), None)
        if duplicate is not None:
            if duplicate.id not in owned.get(
                "volume", set()
            ) and duplicate.name not in owned_names.get("volume", set()):
                raise OnboardingError(
                    "unowned_resource_conflict",
                    "volume name already exists; select it explicitly to reuse it",
                    step="volume",
                )
            if (
                duplicate.size_gb != selection.size_gb
                or duplicate.data_center_id != selection.data_center_id
            ):
                raise OnboardingError(
                    "resource_drift",
                    "owned volume no longer matches the confirmed plan",
                    step="volume",
                )
        return duplicate

    async def _template_inventory(
        self,
        request: RunPodOnboardingRequest,
        owned: Mapping[str, set[str]],
        owned_names: Mapping[str, set[str]],
        *,
        registry_id: str | None,
    ) -> TemplateResource | None:
        selection = request.template
        if selection.mode == ResourceMode.NONE:
            return None
        if selection.mode == ResourceMode.EXISTING:
            resource_id = selection.resource_id
            assert resource_id
            resource = await _safe_get(
                lambda: self._resources.get_template(resource_id),
                "template",
                resource_id,
            )
            _validate_template_resource(
                request,
                resource,
                registry_id=registry_id,
            )
            return resource
        assert selection.name
        values = await self._resources.list_templates()
        duplicate = next((item for item in values if item.name == selection.name), None)
        if duplicate is not None:
            if duplicate.id not in owned.get(
                "template", set()
            ) and duplicate.name not in owned_names.get("template", set()):
                raise OnboardingError(
                    "unowned_resource_conflict",
                    "template name already exists; select it explicitly to reuse it",
                    step="template",
                )
            _validate_template_resource(
                request,
                duplicate,
                registry_id=registry_id,
            )
        return duplicate

    async def _endpoint_inventory(
        self,
        request: RunPodOnboardingRequest,
        owned: Mapping[str, set[str]],
        owned_names: Mapping[str, set[str]],
        *,
        template_id: str | None,
        gpu: EndpointGpuRequest | None,
    ) -> EndpointResource | None:
        selection = request.endpoint
        if request.topology != OnboardingTopology.ENDPOINT:
            return None
        assert gpu is not None
        if selection.mode == ResourceMode.EXISTING:
            resource_id = selection.resource_id
            assert resource_id
            resource = await _safe_get(
                lambda: self._resources.get_endpoint(resource_id),
                "endpoint",
                resource_id,
            )
            _validate_endpoint_resource(
                request,
                resource,
                template_id=template_id,
                gpu=gpu,
            )
            return resource
        assert selection.name
        values = await self._resources.list_endpoints()
        duplicate = next((item for item in values if item.name == selection.name), None)
        if duplicate is not None:
            if duplicate.id not in owned.get(
                "endpoint", set()
            ) and duplicate.name not in owned_names.get("endpoint", set()):
                raise OnboardingError(
                    "unowned_resource_conflict",
                    "endpoint name already exists; select it explicitly to reuse it",
                    step="endpoint",
                )
            _validate_endpoint_resource(
                request,
                duplicate,
                template_id=template_id,
                gpu=gpu,
            )
        return duplicate

    def _steps(
        self,
        request: RunPodOnboardingRequest,
        inventory: _Inventory,
        events: tuple[OnboardingEvent, ...],
    ) -> tuple[OnboardingStep, ...]:
        latest = {event.step: event for event in events}
        steps: list[OnboardingStep] = []

        def add(
            key: str,
            effect: str,
            rollback: str,
            *,
            resource_type: str | None = None,
            resource: object | None = None,
            planned_resource_id: str | None = None,
            writes: tuple[str, ...] = (),
            skipped: bool = False,
        ) -> None:
            observed_resource_id = _resource_id(resource)
            resource_id = observed_resource_id or planned_resource_id
            event = latest.get(key)
            state = StepState.SKIPPED if skipped else StepState.PENDING
            reused = False
            observed_enabled = getattr(resource, "enabled", True)
            if observed_resource_id is not None and observed_enabled:
                state = StepState.REUSED
                reused = True
            if event is not None and event.state in {
                StepState.COMPLETED,
                StepState.REUSED,
                StepState.RETAINED,
            }:
                live_resource_required = resource_type is not None
                live_resource_ready = observed_resource_id is not None and observed_enabled
                if not live_resource_required or live_resource_ready:
                    state = event.state
                    resource_id = resource_id or event.resource_id
                    reused = event.state == StepState.REUSED
            steps.append(
                OnboardingStep(
                    key=key,
                    order=len(steps) + 1,
                    state=state,
                    effect=effect,
                    writes=writes,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    reused=reused,
                    rollback=rollback,
                )
            )

        add(
            "validate_config",
            "validate local config and credential references",
            "retain bounded audit evidence",
            writes=("pitwall.config_audit",),
        )
        add("discover_capacity", "read GPU and datacenter catalogue", "read only")
        add(
            "registry_auth",
            _resource_effect(request.registry, "registry auth"),
            "delete only when this plan created it and no dependent resource remains",
            resource_type="registry_auth",
            resource=inventory.registry,
            planned_resource_id=(
                _planned_resource_id(request, "registry_auth")
                if request.registry.mode == ResourceMode.CREATE and inventory.registry is None
                else None
            ),
            writes=("RunPod registry auth",)
            if request.registry.mode == ResourceMode.CREATE and inventory.registry is None
            else (),
            skipped=request.registry.mode == ResourceMode.NONE,
        )
        add(
            "volume",
            _resource_effect(request.volume, "network volume"),
            "retain volumes for explicit operator review; automatic cleanup never destroys data",
            resource_type="volume",
            resource=inventory.volume,
            planned_resource_id=(
                _planned_resource_id(request, "volume")
                if request.volume.mode == ResourceMode.CREATE and inventory.volume is None
                else None
            ),
            writes=("RunPod network volume",)
            if request.volume.mode == ResourceMode.CREATE and inventory.volume is None
            else (),
            skipped=request.volume.mode == ResourceMode.NONE,
        )
        add(
            "template",
            _resource_effect(request.template, "template"),
            "delete only when this plan created it and no endpoint remains",
            resource_type="template",
            resource=inventory.template,
            planned_resource_id=(
                _planned_resource_id(request, "template")
                if request.template.mode == ResourceMode.CREATE and inventory.template is None
                else None
            ),
            writes=("RunPod account template",)
            if request.template.mode == ResourceMode.CREATE and inventory.template is None
            else (),
            skipped=request.template.mode == ResourceMode.NONE,
        )
        add(
            "endpoint",
            _resource_effect(request.endpoint, "serverless endpoint"),
            "delete only when this plan created it",
            resource_type="endpoint",
            resource=inventory.endpoint,
            planned_resource_id=(
                _planned_resource_id(request, "endpoint")
                if request.topology == OnboardingTopology.ENDPOINT
                and request.endpoint.mode == ResourceMode.CREATE
                and inventory.endpoint is None
                else None
            ),
            writes=("RunPod serverless endpoint",)
            if request.topology == OnboardingTopology.ENDPOINT
            and request.endpoint.mode == ResourceMode.CREATE
            and inventory.endpoint is None
            else (),
            skipped=request.topology != OnboardingTopology.ENDPOINT,
        )
        add(
            "pod_request_preview",
            "construct a pod request without creating paid compute",
            "preview only",
            skipped=request.topology != OnboardingTopology.POD_LEASE,
        )
        add(
            "capability",
            "create or reuse the broker capability",
            "disable a capability created by a failed plan; retain its audit history",
            resource_type="capability",
            resource=inventory.capability,
            writes=("pitwall.capabilities",)
            if inventory.capability is None
            else (("pitwall.capabilities.enabled",) if not inventory.capability.enabled else ()),
        )
        add(
            "provider",
            "create or reuse the healthy RunPod provider binding",
            "disable a provider created by a failed plan; retain its audit history",
            resource_type="provider",
            resource=inventory.provider,
            writes=("pitwall.providers",)
            if inventory.provider is None
            else (("pitwall.providers.enabled",) if not inventory.provider.enabled else ()),
        )
        add(
            "final_dry_run",
            "exercise production resolution, cost, guardrail, and request construction",
            "zero-write validation",
        )
        add("complete", "record completion evidence", "audit evidence is retained")
        return tuple(steps)

    def _dry_run(
        self,
        request: RunPodOnboardingRequest,
        *,
        safe_payload: JsonObject,
        inventory: _Inventory,
        scan_decision: PreSpendDecision,
        discovered_rate: Decimal,
        endpoint_gpu: EndpointGpuRequest | None,
    ) -> DryRunEvidence:
        capability = inventory.capability or _proposed_capability(
            request, now=dt.datetime.now(dt.UTC)
        )
        endpoint_id = _resource_id(inventory.endpoint) or _planned_resource_id(request, "endpoint")
        template_id = _resource_id(inventory.template) or (
            _planned_resource_id(request, "template")
            if request.template.mode == ResourceMode.CREATE
            else request.template.resource_id
        )
        volume_id = _resource_id(inventory.volume) or (
            _planned_resource_id(request, "volume")
            if request.volume.mode == ResourceMode.CREATE
            else request.volume.resource_id
        )
        registry_id = _resource_id(inventory.registry) or (
            _planned_resource_id(request, "registry_auth")
            if request.registry.mode == ResourceMode.CREATE
            else request.registry.resource_id
        )
        provider = inventory.provider or _proposed_provider(
            request,
            capability=capability,
            endpoint_id=endpoint_id if request.topology == OnboardingTopology.ENDPOINT else None,
            template_id=template_id,
            volume_id=volume_id,
            now=dt.datetime.now(dt.UTC),
        )
        quote = quote_cost(
            capability=capability,
            provider_cost=provider.config,
            payload=safe_payload,
        )
        request_summary: JsonObject
        if request.topology == OnboardingTopology.ENDPOINT:
            assert endpoint_gpu is not None
            if request.endpoint.mode == ResourceMode.CREATE:
                endpoint_preview = _endpoint_create_request(
                    request,
                    plan_id=_plan_id(request, safe_payload),
                    intent="preview",
                    template_id=template_id,
                    gpu=endpoint_gpu,
                )
                request_summary = {
                    "name": endpoint_preview.name,
                    "template_id": endpoint_preview.template_id,
                    "image": endpoint_preview.image,
                    "gpu_type_ids": list(request.gpu_type_ids),
                    "gpu_pools": list(endpoint_preview.gpu.pools),
                    "excluded_gpu_type_ids": list(endpoint_preview.gpu.excluded_type_ids),
                    "gpu_count": endpoint_preview.gpu.count,
                    "workers_min": endpoint_preview.workers.minimum,
                    "workers_max": endpoint_preview.workers.maximum,
                }
            else:
                request_summary = {
                    "resource_id": endpoint_id,
                    "reuse": True,
                    "gpu_type_ids": list(request.gpu_type_ids),
                    "gpu_pools": list(endpoint_gpu.pools),
                    "excluded_gpu_type_ids": list(endpoint_gpu.excluded_type_ids),
                    "gpu_count": endpoint_gpu.count,
                }
            kind: Literal["endpoint", "pod"] = "endpoint"
        else:
            pod_preview = _pod_create_request(
                request,
                plan_id=_plan_id(request, safe_payload),
                intent="preview",
                template_id=template_id,
                volume_id=volume_id,
                registry_id=registry_id,
            )
            request_summary = {
                "name": pod_preview.name,
                "image": pod_preview.image,
                "gpu_type_ids": pod_preview.gpu_type_ids,
                "gpu_count": pod_preview.gpu_count,
                "template_id": pod_preview.template_id,
                "network_volume_id": pod_preview.network_volume_id,
                "registry_auth_id": pod_preview.registry_auth_id,
                "max_cost_per_hour": str(pod_preview.max_cost_per_hour),
            }
            kind = "pod"
        return DryRunEvidence(
            guardrail_decision=("redact" if scan_decision == PreSpendDecision.REDACT else "allow"),
            selected_provider_id=provider.id,
            provider_type=provider.provider_type,
            eligible_provider_ids=(provider.id,),
            discovered_rate_per_hour_usd=discovered_rate,
            cost_quote=quote.to_serializable_dict(),
            request_kind=kind,
            request_summary=request_summary,
            inference_probe=(
                "Optional paid inference remains a separate LIVE-RP-01 authorization; "
                "onboarding never performs it."
                if request.describe_inference_probe
                else None
            ),
        )

    async def _endpoint_gpu_selection(
        self, request: RunPodOnboardingRequest
    ) -> EndpointGpuRequest | None:
        if request.topology != OnboardingTopology.ENDPOINT:
            return None
        try:
            return await self._resources.resolve_endpoint_gpu_types(
                request.gpu_type_ids,
                gpu_count=request.gpu_count,
            )
        except RunPodControlPlaneError as exc:
            raise OnboardingError(
                "runpod_gpu_selection_unavailable",
                "RunPod serverless GPU selection is unavailable",
                step="discover_capacity",
            ) from exc

    def _validate_credential_references(self, request: RunPodOnboardingRequest) -> None:
        references = [request.credential_ref]
        if request.registry.password_env is not None:
            references.append(request.registry.password_env)
        missing = [reference for reference in references if not self._environ.get(reference, "")]
        if missing:
            raise OnboardingError(
                "credential_reference_unset",
                "required credential reference is unset: " + ", ".join(sorted(missing)),
                step="validate_config",
            )

    @staticmethod
    def _validate_discovery(
        request: RunPodOnboardingRequest,
        snapshot: GpuDiscoverySnapshot,
    ) -> Decimal:
        known_gpus = {gpu.gpu_type_id: gpu for gpu in snapshot.gpus}
        missing_gpus = [gpu for gpu in request.gpu_type_ids if gpu not in known_gpus]
        if missing_gpus:
            raise OnboardingError(
                "gpu_not_discovered",
                "requested GPU type is absent from the discovered RunPod catalogue",
                step="discover_capacity",
            )
        discovered_rates: list[Decimal] = []
        for gpu_type_id in request.gpu_type_ids:
            gpu = known_gpus[gpu_type_id]
            cloud_rates: tuple[Decimal | None, ...]
            if request.cloud == "SECURE":
                cloud_rates = (gpu.secure_price,) if gpu.secure_cloud else ()
            elif request.cloud == "COMMUNITY":
                cloud_rates = (gpu.community_price,) if gpu.community_cloud else ()
            else:
                cloud_rates = tuple(
                    rate
                    for supported, rate in (
                        (gpu.secure_cloud, gpu.secure_price),
                        (gpu.community_cloud, gpu.community_price),
                    )
                    if supported
                )
            if not cloud_rates:
                raise OnboardingError(
                    "gpu_cloud_unavailable",
                    "requested GPU type is unavailable in the selected cloud lane",
                    step="discover_capacity",
                )
            if any(rate is None for rate in cloud_rates):
                raise OnboardingError(
                    "pricing_unavailable",
                    "current pricing is unavailable for a requested GPU cloud lane",
                    step="discover_capacity",
                )
            if gpu.available_gpu_counts and request.gpu_count not in gpu.available_gpu_counts:
                raise OnboardingError(
                    "gpu_count_unavailable",
                    "requested GPU count is absent from the current capacity catalogue",
                    step="discover_capacity",
                )
            discovered_rates.extend(
                rate * request.gpu_count for rate in cloud_rates if rate is not None
            )
        discovered_rate = max(discovered_rates)
        if request.rate_per_hour_usd < discovered_rate:
            raise OnboardingError(
                "rate_below_discovered_price",
                "confirmed hourly ceiling is below the current discovered GPU price",
                step="discover_capacity",
            )

        data_centers = {dc.datacenter_id: dc for dc in snapshot.datacenters}
        requested_dc = request.data_center_id or request.volume.data_center_id
        if requested_dc is not None and requested_dc not in data_centers:
            raise OnboardingError(
                "data_center_not_discovered",
                "requested datacenter is absent from the discovered RunPod catalogue",
                step="discover_capacity",
            )
        if requested_dc is not None:
            dc = data_centers[requested_dc]
            if any(not dc.gpu_availability.get(gpu, False) for gpu in request.gpu_type_ids):
                raise OnboardingError(
                    "gpu_unavailable_in_data_center",
                    "requested GPU type is unavailable in the selected datacenter",
                    step="discover_capacity",
                )
            if request.volume.mode == ResourceMode.CREATE and not dc.storage_support:
                raise OnboardingError(
                    "volume_unsupported_in_data_center",
                    "selected datacenter does not support network volumes",
                    step="discover_capacity",
                )
        return discovered_rate

    async def _record_completed(self, plan_id: str, step: str, detail: str) -> None:
        await self._state.append_event(
            plan_id,
            OnboardingEvent(step=step, state=StepState.COMPLETED, detail=detail),
        )

    async def _record_resource(
        self,
        plan_id: str,
        step: str,
        resource_type: str,
        resource_id: str,
        resource_name: str | None = None,
    ) -> None:
        await self._state.append_event(
            plan_id,
            OnboardingEvent(
                step=step,
                state=StepState.COMPLETED,
                resource_type=resource_type,
                resource_id=resource_id,
                resource_name=resource_name,
                created_by_plan=True,
                detail=f"created {resource_type}",
            ),
        )

    async def _record_intent(
        self,
        plan_id: str,
        step: str,
        resource_type: str,
        resource_name: str,
    ) -> None:
        await self._state.append_event(
            plan_id,
            OnboardingEvent(
                step=step,
                state=StepState.PENDING,
                resource_type=resource_type,
                resource_name=resource_name,
                created_by_plan=True,
                detail=f"attempting {resource_type} creation",
            ),
        )

    async def _record_reused(
        self, plan_id: str, step: str, resource_type: str, resource_id: str
    ) -> None:
        await self._state.append_event(
            plan_id,
            OnboardingEvent(
                step=step,
                state=StepState.REUSED,
                resource_type=resource_type,
                resource_id=resource_id,
                detail=f"reused {resource_type}",
            ),
        )

    async def _safe_failure_event(self, plan_id: str, step: str) -> None:
        try:
            await self._state.append_event(
                plan_id,
                OnboardingEvent(
                    step=step,
                    state=StepState.FAILED,
                    detail="step failed; provider and persistence details were redacted",
                ),
            )
        except Exception:  # reason: preserve the primary failure if audit storage is degraded
            return

    async def _capture_ambiguous_broker_records(
        self,
        request: RunPodOnboardingRequest,
        created: list[tuple[str, str, str]],
        *,
        capability_attempted: bool,
        provider_attempted: bool,
    ) -> None:
        """Find a row committed before its create/audit call was interrupted."""
        known = {(resource_type, resource_id) for _, resource_type, resource_id in created}
        try:
            if capability_attempted:
                capability = await self._state.get_capability(request.capability_name)
                expected_id = f"cap_{_slug(request.capability_name)}"
                if (
                    capability is not None
                    and capability.id == expected_id
                    and ("capability", capability.id) not in known
                ):
                    created.append(("capability", "capability", capability.id))
                    known.add(("capability", capability.id))
            if provider_attempted:
                provider = await self._state.get_provider(request.provider_name)
                expected_id = f"prov_{_slug(request.provider_name)}"
                if (
                    provider is not None
                    and provider.id == expected_id
                    and ("provider", provider.id) not in known
                ):
                    created.append(("provider", "provider", provider.id))
        except Exception:  # reason: best effort during a persistence/audit failure
            return

    async def _compensate(
        self,
        plan_id: str,
        created: list[tuple[str, str, str]],
        *,
        reactivated: list[tuple[str, str, str]],
        preserve_dependencies: bool = False,
    ) -> list[str]:
        retained: list[str] = []
        for step, resource_type, resource_id in reversed(reactivated):
            try:
                if resource_type == "provider":
                    await self._state.disable_provider(resource_id)
                else:
                    await self._state.disable_capability(resource_id)
                retained.append(f"{resource_type}:{resource_id} (disabled)")
                await self._compensation_event(
                    plan_id,
                    step,
                    resource_type,
                    resource_id,
                    retained=True,
                    created_by_plan=False,
                )
            except Exception:  # reason: restoration detail must stay bounded
                retained.append(f"{resource_type}:{resource_id} (restore failed)")
                await self._compensation_event(
                    plan_id,
                    step,
                    resource_type,
                    resource_id,
                    retained=True,
                    created_by_plan=False,
                )
        preserve_dependencies = preserve_dependencies or any(
            item[1] == "provider" for item in created
        )
        for step, resource_type, resource_id in reversed(created):
            if resource_type == "volume" or (
                preserve_dependencies and resource_type in {"endpoint", "template", "registry_auth"}
            ):
                retained.append(f"{resource_type}:{resource_id}")
                await self._compensation_event(
                    plan_id, step, resource_type, resource_id, retained=True
                )
                continue
            try:
                if resource_type == "endpoint":
                    await self._resources.delete_endpoint(
                        IdentifiedMutationRequest(
                            intent="apply",
                            idempotency_key=_rollback_idempotency_key(
                                plan_id, "rollback_endpoint", resource_id
                            ),
                            resource_id=resource_id,
                        )
                    )
                elif resource_type == "template":
                    await self._resources.delete_template(
                        IdentifiedMutationRequest(
                            intent="apply",
                            idempotency_key=_rollback_idempotency_key(
                                plan_id, "rollback_template", resource_id
                            ),
                            resource_id=resource_id,
                        )
                    )
                elif resource_type == "registry_auth":
                    await self._resources.delete_registry_auth(
                        IdentifiedMutationRequest(
                            intent="apply",
                            idempotency_key=_rollback_idempotency_key(
                                plan_id, "rollback_registry", resource_id
                            ),
                            resource_id=resource_id,
                        )
                    )
                elif resource_type == "provider":
                    await self._state.disable_provider(resource_id)
                    retained.append(f"provider:{resource_id} (disabled)")
                elif resource_type == "capability":
                    await self._state.disable_capability(resource_id)
                    retained.append(f"capability:{resource_id} (disabled)")
                else:
                    retained.append(f"{resource_type}:{resource_id}")
                if resource_type in {"endpoint", "template", "registry_auth"}:
                    await self._release_compensated_key(plan_id, step)
                await self._compensation_event(
                    plan_id,
                    step,
                    resource_type,
                    resource_id,
                    retained=resource_type in {"provider", "capability"},
                )
            except Exception:  # reason: cleanup detail may contain provider response content
                retained.append(f"{resource_type}:{resource_id} (cleanup failed)")
                await self._compensation_event(
                    plan_id, step, resource_type, resource_id, retained=True
                )
        return retained

    async def _release_compensated_key(self, plan_id: str, step: str) -> None:
        """Free the create key of a resource compensation deleted, so resume recreates it.

        Best effort: a resume releases the key again once a get by the recorded resource
        id answers not found (see ``_release_absent_step_key``).
        """
        try:
            await self._resources.release_idempotency_key(
                _step_idempotency_key(plan_id, step),
                reason="onboarding compensation deleted the created resource",
            )
        except Exception:  # reason: cleanup must remain best effort; resume releases again
            return

    async def _release_absent_step_key(self, state: _ApplyState) -> None:
        """On resume, free a create step's key only when its earlier outcome is settled.

        The step creates only after inventory, a list by name, found no live resource. A
        list can lag, so the step's newest journal entry decides:

        - ``completed``: release only when a get by the recorded resource id answers not
          found. Otherwise the create replays the live resource.
        - ``started`` (unknown outcome): release only once the attempt is older than
          ``_UNKNOWN_OUTCOME_GRACE_S``, so an in-flight create has settled at RunPod.
          Until then the create meets the journal's ambiguous refusal, and the failure
          names the remedy.
        """
        if not state.resume:
            return
        key = _step_idempotency_key(state.plan_id, state.current_step)
        entry = await self._resources.idempotency_key_status(key)
        if entry is None:
            return
        if entry.state == "completed":
            if entry.resource_id is None or not await self._resource_absent(
                state.current_step, entry.resource_id
            ):
                return
        elif entry.state != "started" or entry.age_s < _UNKNOWN_OUTCOME_GRACE_S:
            return
        await self._resources.release_idempotency_key(
            key, reason="onboarding resume found the step's earlier resource absent"
        )

    async def _resource_absent(self, step: str, resource_id: str) -> bool:
        """Whether a get by id answers not found for the resource a create step made."""
        getters: dict[str, Callable[[str], Awaitable[object]]] = {
            "registry_auth": self._resources.get_registry_auth,
            "volume": self._resources.get_volume,
            "template": self._resources.get_template,
            "endpoint": self._resources.get_endpoint,
        }
        get = getters.get(step)
        if get is None:
            return False
        try:
            await get(resource_id)
        except RunPodControlPlaneError as exc:
            if exc.code == "resource_not_found":
                return True
            raise
        return False

    async def _compensation_event(
        self,
        plan_id: str,
        step: str,
        resource_type: str,
        resource_id: str,
        *,
        retained: bool,
        created_by_plan: bool = True,
    ) -> None:
        try:
            await self._state.append_event(
                plan_id,
                OnboardingEvent(
                    step=step,
                    state=StepState.RETAINED if retained else StepState.COMPENSATED,
                    resource_type=resource_type,
                    resource_id=resource_id,
                    created_by_plan=created_by_plan,
                    detail=(
                        "resource retained for operator review"
                        if retained
                        else "safe compensation completed"
                    ),
                ),
            )
        except Exception:  # reason: cleanup must remain best effort during audit outage
            return

    async def _plan_lock(self, plan_id: str) -> asyncio.Lock:
        async with self._locks_guard:
            return self._locks.setdefault(plan_id, asyncio.Lock())


class _OnboardingProviderResolver:
    def __init__(self, state: OnboardingState) -> None:
        self._state = state

    async def get(self, provider_id: str) -> Provider | None:
        return await self._state.get_provider_by_id(provider_id)

    async def list(
        self,
        *,
        capability_id: str | None = None,
        enabled_only: bool = False,
        provider_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Provider]:
        del capability_id, enabled_only, provider_type, limit, offset
        return []


class _OnboardingCapabilityResolver:
    def __init__(self, state: OnboardingState) -> None:
        self._state = state

    async def get(self, capability_id: str) -> Capability | None:
        return await self._state.get_capability_by_id(capability_id)

    async def get_by_name(self, name: str) -> Capability | None:
        return await self._state.get_capability(name)


class _Inventory:
    def __init__(
        self,
        *,
        registry: RegistryAuthResource | None,
        volume: VolumeResource | None,
        template: TemplateResource | None,
        endpoint: EndpointResource | None,
        capability: Capability | None,
        provider: Provider | None,
    ) -> None:
        self.registry = registry
        self.volume = volume
        self.template = template
        self.endpoint = endpoint
        self.capability = capability
        self.provider = provider


def _result(
    *,
    action: OnboardingAction,
    plan_id: str,
    request: RunPodOnboardingRequest,
    snapshot: GpuDiscoverySnapshot,
    steps: tuple[OnboardingStep, ...],
    status: OnboardingStatus,
    evidence: DryRunEvidence,
    zero_write: bool,
    events: tuple[OnboardingEvent, ...],
) -> OnboardingResult:
    actionable = [
        step
        for step in steps
        if step.state not in {StepState.SKIPPED, StepState.COMPLETED, StepState.REUSED}
    ]
    writes = tuple(write for step in steps for write in step.writes if write.startswith("pitwall."))
    resources = tuple(
        f"{step.resource_type}:{step.resource_id or 'proposed'}"
        for step in steps
        if step.resource_type in {"registry_auth", "volume", "template", "endpoint"}
        and step.state != StepState.SKIPPED
    )
    reused = tuple(
        f"{step.resource_type}:{step.resource_id}"
        for step in steps
        if step.reused and step.resource_type and step.resource_id
    )
    retained = tuple(
        f"{event.resource_type}:{event.resource_id}"
        for event in events
        if event.state == StepState.RETAINED and event.resource_type and event.resource_id
    )
    return OnboardingResult(
        action=action,
        plan_id=plan_id,
        status=status,
        topology=request.topology,
        zero_write=zero_write,
        estimated_ceiling_usd=Decimal(str(evidence.cost_quote["ceiling"])),
        cost_impact=_cost_impact(request, evidence),
        credential_references=tuple(
            ref
            for ref in (request.credential_ref, request.registry.password_env)
            if ref is not None
        ),
        discovered_gpu_type_ids=tuple(gpu.gpu_type_id for gpu in snapshot.gpus),
        discovered_data_center_ids=tuple(dc.datacenter_id for dc in snapshot.datacenters),
        provider_resources=resources,
        database_mutations=tuple(dict.fromkeys(writes)),
        existing_resource_reuse=reused,
        steps=steps,
        next_step=actionable[0].key if actionable else None,
        can_resume=status in {OnboardingStatus.IN_PROGRESS, OnboardingStatus.FAILED},
        rollback_guidance=tuple(step.rollback for step in steps if step.rollback != "no writes"),
        retained_resources=retained,
        dry_run_evidence=evidence,
    )


def _cost_impact(
    request: RunPodOnboardingRequest,
    evidence: DryRunEvidence,
) -> OnboardingCostImpact:
    if request.topology == OnboardingTopology.ENDPOINT:
        minimum = request.rate_per_hour_usd * request.endpoint.workers_min
        maximum = request.rate_per_hour_usd * request.endpoint.workers_max
    else:
        # Onboarding previews the pod request but never launches paid compute.
        minimum = maximum = Decimal(0)
    volume_gb = request.volume.size_gb if request.volume.mode == ResourceMode.CREATE else None
    return OnboardingCostImpact(
        execution_ceiling_usd=Decimal(str(evidence.cost_quote["ceiling"])),
        provider_rate_per_hour_usd=request.rate_per_hour_usd,
        discovered_rate_per_hour_usd=evidence.discovered_rate_per_hour_usd,
        endpoint_minimum_hourly_usd=minimum,
        endpoint_maximum_hourly_usd=maximum,
        network_volume_gb_created=volume_gb,
        network_volume_pricing=(
            "provider_rate_unavailable" if volume_gb is not None else "not_created"
        ),
        paid_compute_floor_created_by_onboarding=(
            request.topology == OnboardingTopology.ENDPOINT and request.endpoint.workers_min > 0
        ),
    )


def _status(
    steps: tuple[OnboardingStep, ...],
    events: tuple[OnboardingEvent, ...],
) -> OnboardingStatus:
    successful = {StepState.COMPLETED, StepState.REUSED, StepState.SKIPPED}
    live_complete = all(step.state in successful for step in steps)
    last_complete = max(
        (
            index
            for index, event in enumerate(events)
            if event.step == "complete" and event.state == StepState.COMPLETED
        ),
        default=-1,
    )
    last_failure = max(
        (index for index, event in enumerate(events) if event.state == StepState.FAILED),
        default=-1,
    )
    if live_complete and last_complete >= last_failure and last_complete >= 0:
        return OnboardingStatus.COMPLETE
    if last_failure > last_complete:
        return OnboardingStatus.FAILED
    if events:
        return OnboardingStatus.IN_PROGRESS
    if all(step.state in {StepState.REUSED, StepState.SKIPPED} for step in steps[2:-2]):
        return OnboardingStatus.READY
    return OnboardingStatus.PLANNED


def _plan_id(request: RunPodOnboardingRequest, safe_payload: JsonObject) -> str:
    value = request.model_dump(mode="json")
    value["probe_payload"] = safe_payload
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"{_PLAN_PREFIX}{digest[:24]}"


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
    if not slug:
        raise OnboardingError("invalid_name", "name cannot produce a stable broker id")
    return slug


def _proposed_capability(
    request: RunPodOnboardingRequest,
    *,
    now: dt.datetime,
) -> Capability:
    return Capability(
        id=f"cap_{_slug(request.capability_name)}",
        name=request.capability_name,
        version="1.0.0",
        class_=request.capability_class,
        description=f"RunPod onboarding topology {request.name}",
        defaults=CapabilityDefaults(execution_timeout_ms=request.execution_timeout_seconds * 1000),
        cost_mode=CostMode.PER_SECOND,
        source=CapabilitySource.API,
        enabled=True,
        created_at=now,
        updated_at=now,
    )


def _provider_type(request: RunPodOnboardingRequest) -> ProviderType:
    if request.topology == OnboardingTopology.POD_LEASE:
        return ProviderType.POD_LEASE
    if request.endpoint.endpoint_type == "LOAD_BALANCER":
        return ProviderType.SERVERLESS_LB
    return ProviderType.SERVERLESS_QUEUE


def _provider_config(
    request: RunPodOnboardingRequest,
    *,
    endpoint_id: str | None,
    template_id: str | None,
    volume_id: str | None,
) -> JsonObject:
    provider_type = _provider_type(request)
    per_second = (request.rate_per_hour_usd / Decimal(3600)).quantize(
        _USD_QUANTUM,
        rounding=ROUND_CEILING,
    )
    config: JsonObject = {
        "gpu_class": request.gpu_type_ids[0],
        "gpu_type_priority": list(request.gpu_type_ids),
        "gpu_count": request.gpu_count,
        "cost": {"mode": "per_second", "per_second_active": str(per_second)},
        "workers": {"workers_min": request.endpoint.workers_min},
        "idle_timeout_minutes": 0,
        "flash_boot_verified": False,
        "max_payload_mb": 30,
        "request_timeout_s": request.execution_timeout_seconds,
    }
    if request.cloud == "ALL":
        # The providers table stores only concrete SECURE/COMMUNITY values;
        # launch preserves the established ALL fallback through provider config.
        config["cloud_type"] = "ALL"
    if template_id is not None:
        config["template_id"] = template_id
    if volume_id is not None:
        config["network_volume_id"] = volume_id
    if request.data_center_id is not None:
        config["data_center_id"] = request.data_center_id
    if endpoint_id is not None:
        config["openai_base_url"] = expected_openai_base_url(provider_type, endpoint_id)
        if provider_type == ProviderType.SERVERLESS_LB:
            config["lb_base_url"] = expected_lb_base_url(endpoint_id)
    validate_provider_registration_config(
        provider_type=provider_type,
        endpoint_id=endpoint_id,
        cloud_type=None if request.cloud == "ALL" else request.cloud,
        config=config,
    )
    return config


def _proposed_provider(
    request: RunPodOnboardingRequest,
    *,
    capability: Capability,
    endpoint_id: str | None,
    template_id: str | None,
    volume_id: str | None,
    now: dt.datetime,
) -> Provider:
    return Provider(
        id=f"prov_{_slug(request.provider_name)}",
        capability_id=capability.id,
        name=request.provider_name,
        adapter_id=ProviderAdapterId.RUNPOD,
        credential_ref=request.credential_ref,
        provider_type=_provider_type(request),
        runpod_endpoint_id=endpoint_id,
        runpod_template_id=template_id,
        region=request.data_center_id,
        cloud_type=None if request.cloud == "ALL" else request.cloud,
        config=_provider_config(
            request,
            endpoint_id=endpoint_id,
            template_id=template_id,
            volume_id=volume_id,
        ),
        priority=0,
        enabled=True,
        health_status="healthy",
        source=CapabilitySource.API,
        updated_at=now,
    )


def _validate_capability_match(
    request: RunPodOnboardingRequest,
    capability: Capability,
) -> None:
    if (
        capability.class_ != request.capability_class
        or capability.cost_mode != CostMode.PER_SECOND
        or capability.defaults.execution_timeout_ms != request.execution_timeout_seconds * 1000
    ):
        raise OnboardingError(
            "broker_state_conflict",
            "existing capability does not match the confirmed onboarding plan",
            step="capability",
        )


def _validate_provider_match(
    request: RunPodOnboardingRequest,
    provider: Provider,
    *,
    capability: Capability,
    endpoint_id: str | None,
    template_id: str | None,
    volume_id: str | None,
) -> None:
    expected = _proposed_provider(
        request,
        capability=capability,
        endpoint_id=endpoint_id,
        template_id=template_id,
        volume_id=volume_id,
        now=provider.updated_at,
    )
    if any(
        (
            provider.capability_id != expected.capability_id,
            provider.adapter_id != expected.adapter_id,
            provider.credential_ref != expected.credential_ref,
            provider.provider_type != expected.provider_type,
            provider.runpod_endpoint_id != expected.runpod_endpoint_id,
            provider.runpod_template_id != expected.runpod_template_id,
            provider.cloud_type != expected.cloud_type,
            provider.config != expected.config,
        )
    ):
        raise OnboardingError(
            "broker_state_conflict",
            "existing provider does not match the confirmed onboarding plan",
            step="provider",
        )


def _endpoint_create_request(
    request: RunPodOnboardingRequest,
    *,
    plan_id: str,
    intent: Literal["preview", "apply"],
    template_id: str | None,
    gpu: EndpointGpuRequest,
) -> EndpointCreateRequest:
    endpoint = request.endpoint
    assert endpoint.name
    return EndpointCreateRequest(
        intent=intent,
        idempotency_key=_step_idempotency_key(plan_id, "endpoint"),
        name=endpoint.name,
        endpoint_type=endpoint.endpoint_type,
        template_id=template_id,
        image=None if template_id is not None else request.image,
        workers=EndpointWorkersRequest(
            minimum=endpoint.workers_min,
            maximum=endpoint.workers_max,
            idle_timeout_seconds=endpoint.idle_timeout_seconds,
        ),
        scaling=EndpointScalingRequest(type=endpoint.scaler_type, value=endpoint.scaler_value),
        gpu=gpu,
        flashboot=endpoint.flashboot,
    )


def _pod_create_request(
    request: RunPodOnboardingRequest,
    *,
    plan_id: str,
    intent: Literal["preview", "apply"],
    template_id: str | None,
    volume_id: str | None,
    registry_id: str | None,
) -> PodCreateRequest:
    # Onboarding only ever previews the pod request (paid compute is created
    # later through the broker lease path). ttl_minutes is still required by
    # the D6(a) contract, so derive it from the probe's own execution timeout
    # with a 15-minute floor and a 5-minute margin.
    ttl_minutes = max(15, -(-request.execution_timeout_seconds // 60) + 5)
    return PodCreateRequest(
        intent=intent,
        idempotency_key=_step_idempotency_key(plan_id, "pod_preview"),
        name=f"pitwall-onboard-{_slug(request.name)}",
        image=request.image,
        gpu_type_ids=request.gpu_type_ids,
        gpu_count=request.gpu_count,
        template_id=template_id,
        disk_gb=request.template.disk_gb,
        cloud=request.cloud,
        data_center_id=request.data_center_id,
        network_volume_id=volume_id,
        ports=request.template.ports,
        env=request.template.env,
        args=request.template.args,
        registry_auth_id=registry_id,
        max_cost_per_hour=request.rate_per_hour_usd,
        ttl_minutes=ttl_minutes,
    )


def _step_idempotency_key(plan_id: str, step: str) -> str:
    digest = hashlib.sha256(f"{plan_id}:{step}".encode()).hexdigest()[:24]
    return f"onboard:{step}:{digest}"[:128]


#: How long an unknown-outcome create must have been started before a resume may release
#: its key: the service's per-call timeout ceiling plus a margin, so an in-flight create
#: has settled at RunPod.
_UNKNOWN_OUTCOME_GRACE_S = UNKNOWN_OUTCOME_GRACE_S


def _rollback_idempotency_key(plan_id: str, step: str, resource_id: str) -> str:
    """One key per deleted resource, so a later compensation never reuses a spent key."""
    digest = hashlib.sha256(f"{plan_id}:{step}:{resource_id}".encode()).hexdigest()[:24]
    return f"onboard:{step}:{digest}"


def _ambiguous_step_remedy(request: RunPodOnboardingRequest, step: str) -> str:
    names = {
        "registry_auth": request.registry.name,
        "volume": request.volume.name,
        "template": request.template.name,
        "endpoint": request.endpoint.name,
    }
    name = names.get(step)
    target = f"the {step} named {name!r}" if name else f"the {step} resource"
    return (
        f"an earlier attempt to create {target} has an unknown outcome: inspect it in RunPod, "
        "delete it or select it as an existing resource, then resume"
    )


def _planned_resource_id(request: RunPodOnboardingRequest, resource_type: str) -> str:
    digest = hashlib.sha256(f"{request.name}:{resource_type}".encode()).hexdigest()[:12]
    return f"planned_{resource_type}_{digest}"


def _required_result_id(result: MutationResult, step: str) -> str:
    if not result.changed or result.resource_id is None:
        raise OnboardingError(
            "provider_result_invalid",
            "provider mutation did not return a created resource id",
            step=step,
        )
    return result.resource_id


def _resource_id(value: object | None) -> str | None:
    resource_id = getattr(value, "id", None)
    if resource_id is None:
        resource_id = getattr(value, "resource_id", None)
    return resource_id if isinstance(resource_id, str) else None


async def _safe_get[Resource](
    operation: Callable[[], Awaitable[Resource]],
    resource_type: str,
    resource_id: str,
) -> Resource:
    try:
        return await operation()
    except RunPodControlPlaneError as exc:
        if exc.code == "resource_not_found":
            raise OnboardingError(
                "selected_resource_not_found",
                f"selected {resource_type} does not exist",
                step=resource_type,
            ) from exc
        raise


def _validate_template_resource(
    request: RunPodOnboardingRequest,
    resource: TemplateResource,
    *,
    registry_id: str | None,
) -> None:
    selection = request.template
    expected_args = argv_to_v2_args(selection.args) if selection.args else None
    expected_env_keys = non_secret_env_keys(selection.env)
    expected_ports = tuple(selection.ports)
    if any(
        (
            resource.image != request.image,
            resource.docker_args_fingerprint != _opaque_fingerprint(expected_args),
            resource.env_fingerprint != _environment_fingerprint(selection.env),
            resource.disk_gb != selection.disk_gb,
            resource.volume_gb != 0,
            resource.volume_mount_path is not None,
            resource.ports != expected_ports,
            resource.env_keys != expected_env_keys,
            resource.serverless != (request.topology == OnboardingTopology.ENDPOINT),
            resource.public,
            resource.registry_auth_id != registry_id,
        )
    ):
        raise OnboardingError(
            "resource_drift",
            "selected template does not exactly match the confirmed plan",
            step="template",
        )


def _validate_endpoint_resource(
    request: RunPodOnboardingRequest,
    resource: EndpointResource,
    *,
    template_id: str | None,
    gpu: EndpointGpuRequest,
) -> None:
    selection = request.endpoint
    expected_workers = EndpointWorkersRequest(
        minimum=selection.workers_min,
        maximum=selection.workers_max,
        idle_timeout_seconds=selection.idle_timeout_seconds,
    )
    expected_scaling = EndpointScalingRequest(
        type=selection.scaler_type,
        value=selection.scaler_value,
    )
    image_mismatch = template_id is None and resource.image != request.image
    if any(
        (
            resource.endpoint_type != selection.endpoint_type,
            resource.workers != expected_workers,
            resource.scaling != expected_scaling,
            resource.flashboot != selection.flashboot,
            resource.template_id != template_id,
            resource.gpu_pools != tuple(gpu.pools),
            resource.excluded_gpu_type_ids != tuple(gpu.excluded_type_ids),
            resource.gpu_count != gpu.count,
            image_mismatch,
        )
    ):
        raise OnboardingError(
            "resource_drift",
            "selected endpoint does not exactly match the confirmed plan",
            step="endpoint",
        )


def _resource_effect(
    selection: RegistrySelection | VolumeSelection | TemplateSelection | EndpointSelection,
    resource_type: str,
) -> str:
    mode = selection.mode
    if mode == ResourceMode.NONE:
        return f"skip {resource_type}"
    if mode == ResourceMode.EXISTING:
        return f"reuse selected {resource_type} {selection.resource_id}"
    return f"create or resume owned {resource_type} {selection.name}"


def _opaque_fingerprint(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _environment_fingerprint(values: Mapping[str, str]) -> str | None:
    items = sorted(values.items())
    if not items:
        return None
    encoded = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _owned_resources(events: tuple[OnboardingEvent, ...]) -> dict[str, set[str]]:
    owned: dict[str, set[str]] = {}
    compensated: set[tuple[str, str]] = set()
    for event in events:
        if event.resource_type is None or event.resource_id is None:
            continue
        key = (event.resource_type, event.resource_id)
        if event.state == StepState.COMPENSATED:
            compensated.add(key)
        if event.created_by_plan:
            owned.setdefault(event.resource_type, set()).add(event.resource_id)
    for resource_type, resource_id in compensated:
        owned.get(resource_type, set()).discard(resource_id)
    return owned


def _owned_resource_names(events: tuple[OnboardingEvent, ...]) -> dict[str, set[str]]:
    """Names attempted by this plan, excluding steps later compensated.

    A provider write can complete remotely while cancellation or a broken
    response prevents its resource ID reaching Pitwall. The pre-write name
    event lets resume rediscover and validate that exact desired resource.
    """
    owned: dict[str, set[str]] = {}
    names_by_step: dict[str, tuple[str, str]] = {}
    for event in events:
        if event.resource_type is not None and event.resource_name is not None:
            names_by_step[event.step] = (event.resource_type, event.resource_name)
            if event.created_by_plan:
                owned.setdefault(event.resource_type, set()).add(event.resource_name)
        if event.state == StepState.COMPENSATED:
            resource = names_by_step.get(event.step)
            if resource is not None:
                resource_type, resource_name = resource
                owned.get(resource_type, set()).discard(resource_name)
    return owned


def _failure_code(exc: BaseException) -> str:
    if isinstance(exc, OnboardingError):
        return exc.code
    if isinstance(exc, RunPodControlPlaneError):
        return f"runpod_{exc.code}"
    return "onboarding_step_failed"


def _has_ambiguous_dependencies(step: str) -> bool:
    """Whether an interrupted write could already reference earlier resources."""
    return step in {"template", "endpoint", "provider"}


def create_runpod_onboarding_service(
    pool: Any,
    *,
    actor: Literal["rest:admin", "mcp:admin", "mcp", "system"] = "system",
    environ: Mapping[str, str] | None = None,
    timeout_s: float = 60.0,
) -> RunPodOnboardingService:
    """Build the production service over existing control-plane and audit stores."""
    environment = os.environ if environ is None else environ
    return RunPodOnboardingService(
        state=PostgresOnboardingState(
            pool,
            actor=actor,
            lock_timeout_s=timeout_s,
        ),
        resources=RunPodControlPlaneService(
            audit_pool=pool,
            actor=actor,
            timeout_s=timeout_s,
            environ=environment,
        ),
        discovery=EnvironmentRunPodDiscovery(
            environ=environment,
            timeout_s=timeout_s,
        ),
        environ=environment,
    )


__all__ = [
    "DryRunEvidence",
    "EndpointSelection",
    "EnvironmentRunPodDiscovery",
    "OnboardingAction",
    "OnboardingCommand",
    "OnboardingCostImpact",
    "OnboardingError",
    "OnboardingEvent",
    "OnboardingResult",
    "OnboardingState",
    "OnboardingStatus",
    "OnboardingStep",
    "OnboardingTopology",
    "PostgresOnboardingState",
    "RegistrySelection",
    "ResourceMode",
    "RunPodOnboardingRequest",
    "RunPodOnboardingService",
    "StepState",
    "TemplateSelection",
    "VolumeSelection",
    "create_runpod_onboarding_service",
]
