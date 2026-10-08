"""Narrow provider adapter contracts and normalized operation payloads."""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ValidationError

from pitwall.core.enums import LeaseState, WorkloadState
from pitwall.core.models import Capability, is_credential_reference_name
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import TaggedPricingModel
from pitwall.cost.reconcile_cost import ProviderActualCostResult

JsonObject = dict[str, Any]


class ProviderCapability(StrEnum):
    """Operations callers can query without invoking an unsupported method."""

    COMPUTE = "compute"
    SYNC_INFERENCE = "sync_inference"
    ASYNC_INFERENCE = "async_inference"
    ASYNC_STATUS = "async_status"
    ASYNC_CANCEL = "async_cancel"
    ACTUAL_COST = "actual_cost"
    AVAILABILITY = "availability"


class ResourceStatus(StrEnum):
    """Provider-independent resource lifecycle statuses."""

    UNKNOWN = "unknown"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    TERMINATED = "terminated"
    FAILED = "failed"


class AvailabilityKind(StrEnum):
    """Provider-independent classes of purchasable capacity."""

    COMPUTE = "compute"
    MODEL = "model"


@dataclass(frozen=True, slots=True)
class CredentialReference:
    """A persisted-safe environment-variable name, never a credential value."""

    name: str

    def __post_init__(self) -> None:
        if not is_credential_reference_name(self.name):
            raise ValueError("credential reference must be an environment-variable name")


type CredentialInput = BaseModel | Mapping[str, object] | CredentialReference


class CredentialResolutionError(RuntimeError):
    """Safe-to-log failure resolving or validating adapter credentials."""

    def __init__(self, adapter_id: str, reference: str | None, fields: tuple[str, ...]) -> None:
        field_text = ", ".join(fields) if fields else "<model>"
        if reference is None:
            message = f"credentials for adapter {adapter_id!r} are invalid: {field_text}"
        else:
            message = (
                f"credential reference {reference!r} for adapter {adapter_id!r} "
                f"could not be resolved: {field_text}"
            )
        super().__init__(message)
        self.adapter_id = adapter_id
        self.reference = reference
        self.fields = fields


def resolve_adapter_credentials[CredentialModelT: BaseModel](
    value: CredentialInput,
    schema: type[CredentialModelT],
    *,
    adapter_id: str,
) -> CredentialModelT:
    """Materialize and validate credentials at the adapter call boundary."""

    reference: str | None = None
    candidate: object = value
    if isinstance(value, CredentialReference):
        reference = value.name
        secret = os.environ.get(reference)
        if secret is None or not secret.strip():
            raise CredentialResolutionError(adapter_id, reference, ("api_key",))
        candidate = {"api_key": secret}
    try:
        return schema.model_validate(candidate)
    except ValidationError as exc:
        fields = _validation_error_fields(exc)
        raise CredentialResolutionError(adapter_id, reference, fields) from None


@dataclass(frozen=True, slots=True)
class ProviderOperationContext:
    """Runtime services available to provider operations."""

    pool: Any
    redis_client: Any | None = None
    now: dt.datetime | None = None


@dataclass(frozen=True, slots=True)
class ProvisionRequest:
    """Inputs required to provision a provider-backed lease/resource."""

    context: ProviderOperationContext
    capability: Capability
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    request_id: str | None = None
    extra_env: Mapping[str, str] | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    budget_gate: Any | None = None
    idempotency_key: str | None = None
    dry_run: bool = False
    #: SHA-256 of the caller's request; a keyed admission records it for same-key replays.
    request_fingerprint: str | None = None


@dataclass(frozen=True, slots=True)
class ProvisionResult:
    """Provider-independent provision result."""

    provider_id: str
    external_id: str | None
    lease_id: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StatusRequest:
    """Inputs required to read one provider-backed resource status."""

    context: ProviderOperationContext
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    external_id: str


@dataclass(frozen=True, slots=True)
class StatusResult:
    """Provider-independent status result."""

    provider_id: str
    external_id: str
    status: ResourceStatus
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReconcileRequest:
    """Inputs required to reconcile provider resources."""

    context: ProviderOperationContext
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    external_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Provider-independent reconcile summary."""

    provider_id: str
    checked: int
    updated: int
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TeardownRequest:
    """Inputs required to tear down a provider-backed lease/resource."""

    context: ProviderOperationContext
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    lease_id: str
    reason: str | None = None
    terminal_state: LeaseState | str = LeaseState.STOPPED


@dataclass(frozen=True, slots=True)
class TeardownResult:
    """Provider-independent teardown result."""

    provider_id: str
    lease_id: str
    external_id: str | None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AvailabilityRequest:
    """Inputs for one bounded, read-only provider availability snapshot."""

    context: ProviderOperationContext
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    limit: int = 100

    def __post_init__(self) -> None:
        if isinstance(self.limit, bool) or not 1 <= self.limit <= 100:
            raise ValueError("availability limit must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class AvailabilityItem:
    """One normalized compute offer or inference model."""

    provider_id: str
    resource_id: str
    kind: AvailabilityKind
    available: bool | None
    region: str | None = None
    accelerator: str | None = None
    accelerator_count: int | None = None
    pricing: Mapping[str, Decimal] = field(default_factory=dict)
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AvailabilityResult:
    """Deterministically ordered availability returned by one adapter."""

    provider_id: str
    observed_at: dt.datetime
    source_contract: str
    items: tuple[AvailabilityItem, ...] = ()


@dataclass(frozen=True, slots=True, order=True)
class ActualCostResourceReference:
    """One exact workload-to-provider-resource identity for billing."""

    workload_id: str
    category: str
    external_resource_id: str

    def __post_init__(self) -> None:
        for field_name in ("workload_id", "category", "external_resource_id"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")


@dataclass(frozen=True, slots=True)
class ActualCostRequest:
    """Bounded provider-actual read for explicitly mapped resources."""

    context: ProviderOperationContext
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    start_day: dt.date
    end_day: dt.date
    references: tuple[ActualCostResourceReference, ...]

    def __post_init__(self) -> None:
        if self.start_day >= self.end_day:
            raise ValueError("start_day must be before end_day")
        if not self.references:
            raise ValueError("actual-cost references must be non-empty")
        if len(self.references) > 100:
            raise ValueError("actual-cost references are limited to 100")


@dataclass(frozen=True, slots=True)
class InferenceRequest:
    """Inputs required for one synchronous inference operation."""

    context: ProviderOperationContext
    capability: Capability
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True, kw_only=True)
class InferenceResult:
    """Provider-independent successful inference result."""

    provider_id: str
    output: Any
    external_job_id: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AsyncInferenceRequest:
    """Inputs required to submit one asynchronous inference job."""

    context: ProviderOperationContext
    capability: Capability
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    payload: Mapping[str, Any] = field(default_factory=dict)
    webhook_url: str | None = None


@dataclass(frozen=True, slots=True)
class AsyncInferenceSubmission:
    """Provider-independent asynchronous inference submission result."""

    provider_id: str
    external_job_id: str
    state: WorkloadState
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AsyncInferenceStatusRequest:
    """Inputs required to read one asynchronous inference job."""

    context: ProviderOperationContext
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    external_job_id: str


@dataclass(frozen=True, slots=True)
class AsyncInferenceStatusResult:
    """Provider-independent asynchronous inference job status."""

    provider_id: str
    external_job_id: str
    state: WorkloadState
    output: Any = None
    error: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AsyncInferenceCancelRequest:
    """Inputs required to cancel one asynchronous inference job."""

    context: ProviderOperationContext
    provider_record: ProviderRecord
    credentials: CredentialInput = field(repr=False)
    external_job_id: str


@dataclass(frozen=True, slots=True)
class AsyncInferenceCancelResult:
    """Provider-independent asynchronous inference cancellation result."""

    provider_id: str
    external_job_id: str
    cancelled: bool
    raw: Mapping[str, Any] = field(default_factory=dict)


def optional_config_string(config: Mapping[str, Any], key: str) -> str | None:
    """Return ``config[key]`` when set; reject blank or whitespace-padded values."""

    if key not in config or config[key] is None:
        return None
    value = config[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"config.{key} must be a non-empty string")
    if value != value.strip():
        raise ValueError(f"config.{key} must not include surrounding whitespace")
    return value


def quota_window(free_type: str, now: dt.datetime) -> tuple[dt.datetime | None, dt.datetime | None]:
    """Return the ``(window_start, reset_at)`` a free-pool type implies at *now*."""

    if free_type in ("recurring-monthly", "recurring-credit"):
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, (start + dt.timedelta(days=32)).replace(day=1)
    if free_type == "recurring-daily":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + dt.timedelta(days=1)
    return None, None


type ProxyHeaderHook = Callable[[Any, dict[str, str]], dict[str, str] | None]
type QuotaTickHook = Callable[
    [Any, Mapping[str, Any], dt.datetime, Mapping[Any, Any]], Awaitable[None]
]


@dataclass(frozen=True, slots=True)
class ProviderDeclaration:
    """What one adapter says about itself so shared layers never branch on provider type.

    ``provider_types`` are the ``providers.provider_type`` values the adapter owns. Every hook
    receives the provider record (or the plain values it needs) and may branch on the
    provider type itself; shared routing, lockout, schema, seed, and reconciler code only reads
    the declaration.
    """

    #: Provider-type values this adapter owns (empty: it rides on another adapter's types).
    provider_types: frozenset[str] = frozenset()
    #: True when a provider of an owned type must name this adapter (seed enforces it).
    dedicated_provider_type: bool = False
    #: True when rows and requests with no provider type are treated as this adapter's.
    default_for_untyped: bool = False

    # -- fallback: the OpenAI pass-through --------------------------------------------------
    #: Whether this adapter's providers may serve the OpenAI-compatible proxy at all.
    openai_compatible: bool = True
    #: Returns a reason to skip a provider before the proxy tries it, or None to keep it.
    proxy_skip_reason: Callable[[Any], str | None] | None = None
    #: Rewrites the request body before it is sent to this provider.
    proxy_rewrite_body: Callable[[bytes, Any], bytes] | None = None
    #: Builds the outbound headers from the sanitized ones, or None for the default bearer.
    proxy_outbound_headers: ProxyHeaderHook | None = None

    #: Provider types that may take part in the OpenAI pass-through.
    openai_proxy_types: frozenset[str] = frozenset()
    #: Derives a provider's OpenAI base URL (configured or computed); None means unknown.
    derive_openai_base_url: Callable[[Any], str | None] | None = None

    # -- lockout ----------------------------------------------------------------------------
    #: Config key paths whose value is the model id a (provider, model) lockout is keyed on.
    lockout_model_paths: tuple[tuple[str, ...], ...] = ()

    # -- config schema ----------------------------------------------------------------------
    #: Provider types whose config carries an OpenAI base URL derived from the endpoint id.
    openai_url_types: frozenset[str] = frozenset()
    #: Provider types whose config may carry a load-balancer base URL.
    lb_url_types: frozenset[str] = frozenset()
    #: Provider types that may carry a ``self_hosted`` profile.
    self_hosted_types: frozenset[str] = frozenset()
    #: ``(provider_type, endpoint_id) -> url`` for the OpenAI base URL.
    openai_base_url: Callable[[str, str], str] | None = None
    #: ``endpoint_id -> url`` for the load-balancer base URL.
    lb_base_url: Callable[[str], str] | None = None
    #: Rejects a registration whose endpoint id does not suit the provider type.
    validate_endpoint: Callable[[str, str | None], None] | None = None
    #: ``(provider_type, endpoint_id, config)``: validates URL-bearing config.
    validate_url: Callable[[str | None, str | None, Mapping[str, Any]], None] | None = None

    # -- seed rows --------------------------------------------------------------------------
    #: Whether a seed row must name a canonical ``gpu_class``.
    requires_gpu_class: bool = True
    #: ``(spec, config, provider_type, endpoint_id) -> config`` adapter-specific seed config.
    seed_config: (
        Callable[[Mapping[str, Any], dict[str, Any], str, str | None], dict[str, Any]] | None
    ) = None

    # -- reconcile --------------------------------------------------------------------------
    #: One quota-reconcile tick for a provider row; ``provider_types`` selects which rows.
    quota_tick: QuotaTickHook | None = None
    #: Provider types the reconciler's health probe covers, in probe order.
    health_probe_types: tuple[str, ...] = ()
    #: ``(provider row, api_key) -> healthy`` for one provider row's endpoint.
    probe_endpoint: Callable[[Mapping[str, Any], str], Awaitable[bool]] | None = None
    #: ``(provider_type, endpoint_id, job_id, api_key) -> status`` of an in-flight job.
    poll_job_status: Callable[[str, str, str, str], Awaitable[str | None]] | None = None


@runtime_checkable
class ProviderAdapter(Protocol):
    """Identity, credential schema, capabilities, and pricing shared by adapters."""

    @property
    def id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def credential_schema(self) -> type[BaseModel]: ...

    @property
    def capabilities(self) -> frozenset[ProviderCapability]: ...

    @property
    def declaration(self) -> ProviderDeclaration: ...

    def pricing_model(
        self,
        capability: Capability,
        provider_record: ProviderRecord,
    ) -> TaggedPricingModel:
        """Return the adapter's tagged pricing model for one record."""
        ...


@runtime_checkable
class ComputeProvider(ProviderAdapter, Protocol):
    """Provider adapter that owns compute-resource lifecycle operations."""

    async def provision(self, request: ProvisionRequest) -> ProvisionResult: ...

    async def status(self, request: StatusRequest) -> StatusResult: ...

    async def reconcile(self, request: ReconcileRequest) -> ReconcileResult: ...

    async def teardown(self, request: TeardownRequest) -> TeardownResult: ...


@runtime_checkable
class InferenceProvider(ProviderAdapter, Protocol):
    """Provider adapter that can perform synchronous inference."""

    async def infer(self, request: InferenceRequest) -> InferenceResult: ...


@runtime_checkable
class AsyncInferenceProvider(ProviderAdapter, Protocol):
    """Provider adapter that can submit asynchronous inference jobs."""

    async def submit(self, request: AsyncInferenceRequest) -> AsyncInferenceSubmission: ...


@runtime_checkable
class AsyncInferenceStatusProvider(ProviderAdapter, Protocol):
    """Provider adapter that can read asynchronous inference job status."""

    async def job_status(
        self,
        request: AsyncInferenceStatusRequest,
    ) -> AsyncInferenceStatusResult: ...


@runtime_checkable
class AsyncInferenceCancelProvider(ProviderAdapter, Protocol):
    """Provider adapter that can cancel asynchronous inference jobs."""

    async def cancel_job(
        self,
        request: AsyncInferenceCancelRequest,
    ) -> AsyncInferenceCancelResult: ...


@runtime_checkable
class AvailabilityProvider(ProviderAdapter, Protocol):
    """Provider adapter that can return a bounded availability snapshot."""

    async def availability(self, request: AvailabilityRequest) -> AvailabilityResult: ...


@runtime_checkable
class ActualCostProvider(ProviderAdapter, Protocol):
    """Provider adapter that reads authoritative, workload-mapped actual cost."""

    async def actual_cost(self, request: ActualCostRequest) -> ProviderActualCostResult: ...


# Source compatibility for callers that imported the old shared name. It now
# denotes metadata only; callers must request a narrow compute/inference type.
Provider = ProviderAdapter


def _validation_error_fields(exc: ValidationError) -> tuple[str, ...]:
    fields: list[str] = []
    for item in exc.errors(include_input=False, include_url=False):
        loc = item.get("loc", ())
        if isinstance(loc, tuple) and loc:
            fields.append(".".join(str(part) for part in loc))
        elif isinstance(loc, str) and loc:
            fields.append(loc)
    return tuple(dict.fromkeys(fields))


__all__ = [
    "ActualCostProvider",
    "ActualCostRequest",
    "ActualCostResourceReference",
    "AvailabilityItem",
    "AvailabilityKind",
    "AvailabilityProvider",
    "AvailabilityRequest",
    "AvailabilityResult",
    "AsyncInferenceCancelProvider",
    "AsyncInferenceCancelRequest",
    "AsyncInferenceCancelResult",
    "AsyncInferenceProvider",
    "AsyncInferenceRequest",
    "AsyncInferenceStatusProvider",
    "AsyncInferenceStatusRequest",
    "AsyncInferenceStatusResult",
    "AsyncInferenceSubmission",
    "ComputeProvider",
    "CredentialInput",
    "CredentialReference",
    "CredentialResolutionError",
    "InferenceProvider",
    "InferenceRequest",
    "InferenceResult",
    "JsonObject",
    "Provider",
    "ProviderAdapter",
    "ProviderCapability",
    "ProviderDeclaration",
    "ProviderOperationContext",
    "ProvisionRequest",
    "ProvisionResult",
    "ReconcileRequest",
    "ReconcileResult",
    "ResourceStatus",
    "StatusRequest",
    "StatusResult",
    "TeardownRequest",
    "TeardownResult",
    "optional_config_string",
    "quota_window",
    "resolve_adapter_credentials",
]
