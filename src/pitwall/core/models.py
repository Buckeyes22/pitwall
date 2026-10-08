"""Pydantic v2 domain models for Pitwall registry and runtime records."""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Mapping
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    field_serializer,
    model_validator,
)

from pitwall.core.enums import (
    CapabilityClass,
    CapabilityHint,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderAdapterId,
    ProviderType,
    ResultDelivery,
    WorkloadState,
)

JsonObject = dict[str, Any]
UsdAmount = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=6)]
_CREDENTIAL_REFERENCE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _reject_unstorable(value: object) -> None:
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError("strings must not contain NUL characters")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("strings must be valid Unicode") from None
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_unstorable(key)
            _reject_unstorable(item)
    elif isinstance(value, list):
        for item in value:
            _reject_unstorable(item)


def _storable_json_object(value: JsonObject) -> JsonObject:
    _reject_unstorable(value)
    return value


# Postgres text and jsonb cannot hold NUL and asyncpg cannot encode lone surrogates, so
# request values bound for a column are refused at validation (422), not by the driver (500).
StoredText = Annotated[str, Field(pattern=r"^[^\x00]*$")]
RequiredStoredText = Annotated[str, Field(min_length=1, pattern=r"^[^\x00]+$")]
StoredJsonObject = Annotated[JsonObject, AfterValidator(_storable_json_object)]


def is_credential_reference_name(value: object) -> bool:
    """Return whether *value* is a serializable environment-variable name."""

    return isinstance(value, str) and _CREDENTIAL_REFERENCE_RE.fullmatch(value) is not None


def _redact_invalid_credential_reference(value: object) -> object:
    # Returning a known-invalid sentinel lets the Field constraint report the
    # error without retaining a credential-shaped input in ValidationError.
    return value if is_credential_reference_name(value) else "[REDACTED]"


def _require_string_enum_value(value: object) -> object:
    if isinstance(value, str):
        return value
    raise ValueError("enum values must be strings")


def _as_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include timezone information")
    return value.astimezone(dt.UTC)


UTCDateTime = Annotated[dt.datetime, AfterValidator(_as_utc)]
StrictCapabilityClass = Annotated[CapabilityClass, BeforeValidator(_require_string_enum_value)]
StrictCapabilityHint = Annotated[CapabilityHint, BeforeValidator(_require_string_enum_value)]
StrictCapabilitySource = Annotated[CapabilitySource, BeforeValidator(_require_string_enum_value)]
StrictCostMode = Annotated[CostMode, BeforeValidator(_require_string_enum_value)]
StrictLeaseRenewalPolicy = Annotated[
    LeaseRenewalPolicy, BeforeValidator(_require_string_enum_value)
]
StrictLeaseState = Annotated[LeaseState, BeforeValidator(_require_string_enum_value)]
StrictResultDelivery = Annotated[ResultDelivery, BeforeValidator(_require_string_enum_value)]
StrictWorkloadState = Annotated[WorkloadState, BeforeValidator(_require_string_enum_value)]
StrictProviderAdapterId = Annotated[ProviderAdapterId, BeforeValidator(_require_string_enum_value)]


NonEmptyString = Annotated[str, Field(min_length=1)]
RoutePlanId = Annotated[str, Field(pattern=r"^plan_[0-9a-f]{32}$")]
NonNegativeInt = Annotated[int, Field(ge=0)]
CredentialReferenceName = Annotated[
    str,
    BeforeValidator(_redact_invalid_credential_reference),
    Field(min_length=1, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$"),
]
WebhookEventType = Literal[
    "workload.completed",
    "lease.ready",
    "lease.renewed",
    "lease.stopped",
    "lease.expiring",
]


def _default_webhook_event_types() -> list[WebhookEventType]:
    return ["workload.completed"]


class PitwallModel(BaseModel):
    """Base model policy shared by public Pitwall domain objects."""

    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        use_enum_values=False,
    )


class CapabilityDefaults(PitwallModel):
    """Default execution settings attached to a capability."""

    execution_timeout_ms: NonNegativeInt = 60_000
    ttl_ms: NonNegativeInt = 300_000
    result_delivery: StrictResultDelivery = ResultDelivery.SYNC


class Capability(PitwallModel):
    """What a consumer asks Pitwall to fulfill."""

    id: NonEmptyString
    name: NonEmptyString
    version: NonEmptyString
    class_: StrictCapabilityClass = Field(
        validation_alias=AliasChoices("class", "class_", "capability_class"),
        serialization_alias="class",
    )
    description: str | None = None
    input_schema: JsonObject = Field(default_factory=dict)
    output_schema: JsonObject = Field(default_factory=dict)
    defaults: CapabilityDefaults = Field(default_factory=CapabilityDefaults)
    cost_mode: StrictCostMode
    hints_supported: list[StrictCapabilityHint] = Field(default_factory=list)
    source: StrictCapabilitySource = CapabilitySource.API
    last_applied_yaml_hash: str | None = None
    served_model_id: str | None = None
    enabled: bool = True
    created_at: UTCDateTime
    updated_at: UTCDateTime

    @property
    def capability_class(self) -> CapabilityClass:
        """Return the capability class using a non-reserved Python name."""
        return self.class_


class Workload(PitwallModel):
    """A single persisted unit of consumer-requested work."""

    id: NonEmptyString
    capability_id: NonEmptyString
    provider_id: NonEmptyString
    type: NonEmptyString
    state: StrictWorkloadState
    external_job_id: str | None = None
    runpod_job_id: str | None = None
    idempotency_key: str | None = None
    input: JsonObject | None = None
    result: JsonObject | None = None
    submitted_at: UTCDateTime
    started_at: UTCDateTime | None = None
    completed_at: UTCDateTime | None = None
    execution_ms: NonNegativeInt | None = None
    queue_ms: NonNegativeInt | None = None
    cold_start_ms: NonNegativeInt | None = None
    input_bytes: NonNegativeInt | None = None
    output_bytes: NonNegativeInt | None = None
    cost_estimate_usd: UsdAmount | None = None
    cost_ceiling_usd: UsdAmount | None = None
    cost_quote: JsonObject | None = None
    cost_actual_usd: UsdAmount | None = None
    cost_actual_provenance: NonEmptyString | None = None
    cost_reconciled_at: UTCDateTime | None = None
    fallback_chain: list[NonEmptyString] = Field(default_factory=list)
    route_plan_id: RoutePlanId | None = None
    route_plan: JsonObject | None = None
    route_attempts: list[JsonObject] = Field(default_factory=list, max_length=100)
    error: JsonObject | None = None
    langfuse_trace_id: str | None = None

    @model_validator(mode="after")
    def _normalize_external_job_id(self) -> Workload:
        if (
            self.external_job_id is not None
            and self.runpod_job_id is not None
            and self.external_job_id != self.runpod_job_id
        ):
            raise ValueError("external_job_id and runpod_job_id must match when both are set")
        if self.external_job_id is None and self.runpod_job_id is not None:
            self.external_job_id = self.runpod_job_id
        if (self.route_plan_id is None) != (self.route_plan is None):
            raise ValueError("route_plan_id and route_plan must be set together")
        if self.route_plan is not None and self.route_plan.get("plan_id") != self.route_plan_id:
            raise ValueError("route_plan.plan_id must match route_plan_id")
        return self


class LeaseTcpEndpoint(PitwallModel):
    """TCP proxy endpoint exposed by RunPod for a lease."""

    host: NonEmptyString
    port: Annotated[int, Field(ge=1, le=65_535)]


class LeaseEndpoints(PitwallModel):
    """HTTP and TCP endpoints observed for a pod lease."""

    http: dict[str, NonEmptyString] = Field(default_factory=dict)
    tcp: dict[str, LeaseTcpEndpoint] = Field(default_factory=dict)


class LeaseReadiness(PitwallModel):
    """Readiness signals required before a lease can become active."""

    runtime_seen_at: UTCDateTime | None = None
    port_mappings_seen_at: UTCDateTime | None = None
    probe_passed_at: UTCDateTime | None = None
    probe_method: str | None = None

    @property
    def has_active_signals(self) -> bool:
        return (
            self.runtime_seen_at is not None
            and self.port_mappings_seen_at is not None
            and self.probe_passed_at is not None
        )


class Lease(PitwallModel):
    """Stateful provider resource allocation from create through teardown."""

    id: NonEmptyString
    provider_id: NonEmptyString
    workload_id: NonEmptyString | None = None
    external_resource_id: NonEmptyString | None = None
    runpod_pod_id: NonEmptyString | None = None
    state: StrictLeaseState
    created_at: UTCDateTime
    expires_at: UTCDateTime
    renewal_policy: StrictLeaseRenewalPolicy
    last_traffic_at: UTCDateTime | None = None
    ready_at: UTCDateTime | None = None
    idle_timeout_min: Annotated[int, Field(ge=5)] | None = None
    max_usd_per_hour: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=4)] | None = None
    auto_teardown_on_expiry: bool = True
    endpoints: LeaseEndpoints | None = None
    readiness: LeaseReadiness | None = None
    cost_accrued_usd: UsdAmount | None = None
    last_health_at: UTCDateTime | None = None
    terminated_at: UTCDateTime | None = None
    terminated_reason: str | None = None

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> Lease:
        if self.external_resource_id is None and self.runpod_pod_id is None:
            raise ValueError("lease requires external_resource_id or runpod_pod_id")
        if (
            self.external_resource_id is not None
            and self.runpod_pod_id is not None
            and self.external_resource_id != self.runpod_pod_id
        ):
            raise ValueError("external_resource_id and runpod_pod_id must match when both are set")
        if self.external_resource_id is None:
            self.external_resource_id = self.runpod_pod_id
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be after created_at")
        if self.state == LeaseState.ACTIVE:
            if self.endpoints is None:
                raise ValueError("active leases require endpoints")
            if self.readiness is None or not self.readiness.has_active_signals:
                raise ValueError(
                    "active leases require runtime, port mapping, and probe readiness signals"
                )
        return self


StrictProviderType = Annotated[ProviderType, BeforeValidator(_require_string_enum_value)]


class Provider(PitwallModel):
    """A concrete fulfillment binding to one static provider adapter."""

    id: NonEmptyString
    capability_id: NonEmptyString
    name: NonEmptyString
    adapter_id: StrictProviderAdapterId = ProviderAdapterId.RUNPOD
    credential_ref: CredentialReferenceName = "RUNPOD_API_KEY"
    provider_type: StrictProviderType
    runpod_endpoint_id: str | None = None
    runpod_template_id: str | None = None
    region: str | None = None
    cloud_type: str | None = None
    config: JsonObject = Field(default_factory=dict, repr=False)
    priority: int = Field(ge=0)
    enabled: bool = True
    health_status: str = "unknown"
    consecutive_failures: NonNegativeInt = 0
    cooldown_trips: NonNegativeInt = 0
    cold_start_p50_ms: NonNegativeInt | None = None
    cold_start_p95_ms: NonNegativeInt | None = None
    recent_error_rate: float = Field(default=0, ge=0, le=1)
    cooldown_until: UTCDateTime | None = None
    source: StrictCapabilitySource = CapabilitySource.API
    last_applied_yaml_hash: str | None = None
    updated_at: UTCDateTime

    @model_validator(mode="after")
    def _default_adapter_credential_reference(self) -> Provider:
        if "credential_ref" not in self.model_fields_set:
            self.credential_ref = default_credential_reference(self.adapter_id)
        return self

    @field_serializer("config")
    def _serialize_config(self, value: JsonObject) -> JsonObject:
        return redact_provider_serialized_config(value)


_SECRET_CONFIG_KEYS = frozenset(
    {
        "access_key",
        "access_key_id",
        "api_key",
        "authorization",
        "client_secret",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "secret_key",
        "token",
    }
)
_REFERENCE_KEY_SUFFIXES = ("_env", "_ref", "_reference")


def default_credential_reference(adapter_id: ProviderAdapterId | str) -> str:
    """Return the conventional environment reference for a built-in adapter."""

    adapter = ProviderAdapterId(adapter_id)
    return {
        ProviderAdapterId.RUNPOD: "RUNPOD_API_KEY",
        ProviderAdapterId.VAST: "VAST_API_KEY",
        ProviderAdapterId.TOGETHER: "TOGETHER_API_KEY",
        ProviderAdapterId.LAMBDA_CLOUD: "LAMBDA_CLOUD_API_KEY",
        ProviderAdapterId.GATEWAY: "PITWALL_GATEWAY_API_KEY",
        ProviderAdapterId.MODEL_STUDIO: "MODEL_STUDIO_API_KEY",
    }[adapter]


def validate_provider_serialized_config(config: Mapping[str, Any]) -> None:
    """Reject serialized provider configuration that contains credential values.

    Reference-bearing keys such as ``api_key_env`` are allowed, but their values
    must be environment-variable names. Error messages contain paths only.
    """

    _validate_provider_config_value(config, path="config")
    validate_provider_storage_payload(config)


class ProviderStoragePolicyError(ValueError):
    """Provider-bound metadata the pre-spend policy refuses to persist or audit."""


def validate_provider_storage_payload(payload: Mapping[str, Any]) -> None:
    """Reject provider-bound metadata that is unsafe to persist or audit."""
    from pitwall.security.pre_spend import (
        PreSpendDecision,
        get_pre_spend_inspection_service,
    )

    inspection = get_pre_spend_inspection_service().inspect(
        _mask_provider_credential_references(payload)
    )
    if inspection.decision != PreSpendDecision.ALLOW:
        raise ProviderStoragePolicyError("provider configuration rejected by pre-spend policy")


def redact_provider_serialized_config(config: Mapping[str, Any]) -> JsonObject:
    """Return a copy with any credential-shaped values replaced."""

    return _redact_provider_config_mapping(config)


def normalize_provider_config_key(key: str) -> str:
    """Normalize snake, kebab, dotted, spaced, and camel-case config keys."""

    stripped = key.strip()
    normalized: list[str] = []
    for index, character in enumerate(stripped):
        is_ascii_upper = "A" <= character <= "Z"
        is_ascii_lower = "a" <= character <= "z"
        is_ascii_digit = "0" <= character <= "9"
        if not (is_ascii_upper or is_ascii_lower or is_ascii_digit):
            if normalized and normalized[-1] != "_":
                normalized.append("_")
            continue

        if is_ascii_upper and normalized and normalized[-1] != "_":
            previous = stripped[index - 1]
            next_character = stripped[index + 1] if index + 1 < len(stripped) else ""
            previous_is_lower_or_digit = "a" <= previous <= "z" or "0" <= previous <= "9"
            acronym_boundary = "A" <= previous <= "Z" and "a" <= next_character <= "z"
            if previous_is_lower_or_digit or acronym_boundary:
                normalized.append("_")
        normalized.append(character.lower())
    return "".join(normalized).strip("_")


_normalize_provider_config_key = normalize_provider_config_key


def _mask_provider_credential_references(value: object) -> Any:
    if isinstance(value, Mapping):
        masked: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = _normalize_provider_config_key(key)
            if _is_credential_reference_key(normalized) and is_credential_reference_name(child):
                continue
            masked[key] = _mask_provider_credential_references(child)
        return masked
    if isinstance(value, list):
        return [_mask_provider_credential_references(item) for item in value]
    return value


def _validate_provider_config_value(value: object, *, path: str) -> None:
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            normalized = _normalize_provider_config_key(key)
            child_path = f"{path}.{key}"
            if _is_credential_reference_key(normalized):
                if not is_credential_reference_name(child):
                    raise ValueError(f"{child_path} must be an environment-variable reference")
                continue
            if _is_secret_config_key(normalized):
                raise ValueError(f"{child_path} must contain a credential reference, not a value")
            _validate_provider_config_value(child, path=child_path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            _validate_provider_config_value(child, path=f"{path}[{index}]")


def _redact_provider_config_mapping(value: Mapping[str, Any]) -> JsonObject:
    redacted: JsonObject = {}
    for raw_key, child in value.items():
        key = str(raw_key)
        normalized = _normalize_provider_config_key(key)
        is_reference = _is_credential_reference_key(normalized)
        if (is_reference and not is_credential_reference_name(child)) or (
            not is_reference and _is_secret_config_key(normalized)
        ):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = _redact_provider_config_value(child)
    return redacted


def _redact_provider_config_value(value: object) -> Any:
    if isinstance(value, Mapping):
        return _redact_provider_config_mapping(value)
    if isinstance(value, list):
        return [_redact_provider_config_value(item) for item in value]
    return value


def _is_credential_reference_key(normalized: str) -> bool:
    for suffix in _REFERENCE_KEY_SUFFIXES:
        if not normalized.endswith(suffix):
            continue
        stem = normalized[: -len(suffix)].rstrip("_")
        return _is_secret_config_key(stem)
    return False


def _is_secret_config_key(normalized: str) -> bool:
    return normalized in _SECRET_CONFIG_KEYS or any(
        normalized.endswith(f"_{secret_key}") for secret_key in _SECRET_CONFIG_KEYS
    )


class ConfigAuditEntry(PitwallModel):
    """A single row in the config mutation audit trail."""

    id: int
    actor: NonEmptyString
    action: NonEmptyString
    entity_type: NonEmptyString
    entity_id: NonEmptyString
    old_value: JsonObject | None = None
    new_value: JsonObject | None = None
    change_reason: str | None = None
    created_at: UTCDateTime

    @model_validator(mode="after")
    def _redact_provider_values(self) -> ConfigAuditEntry:
        # Migration 0028 scrubs durable legacy rows. Keep this read boundary
        # defensive as well so an out-of-band provider audit insert cannot be
        # reflected by REST/MCP serialization before repair.
        if self.entity_type == "provider":
            if self.old_value is not None:
                self.old_value = redact_provider_serialized_config(self.old_value)
            if self.new_value is not None:
                self.new_value = redact_provider_serialized_config(self.new_value)
        return self


class WebhookSubscription(PitwallModel):
    """A consumer-registered webhook URL for async job result callbacks."""

    id: NonEmptyString
    consumer: NonEmptyString
    webhook_url: NonEmptyString
    hmac_secret: str | None = Field(
        default=None,
        repr=False,
        description="Secret for HMAC-signed webhook delivery. Never exposed via API.",
    )
    active: bool = True
    event_types: list[WebhookEventType] = Field(default_factory=_default_webhook_event_types)
    created_at: UTCDateTime
    updated_at: UTCDateTime


class WebhookSubscriptionCreate(PitwallModel):
    """Input schema for creating a webhook subscription."""

    consumer: Annotated[str, Field(min_length=1, pattern=r"^[^\x00]+$")]
    webhook_url: Annotated[str, Field(min_length=1, pattern=r"^[^\x00]+$")]
    event_types: list[WebhookEventType] = Field(
        default_factory=_default_webhook_event_types, min_length=1
    )


class WebhookSubscriptionResponse(PitwallModel):
    """Output schema for webhook subscription read operations.

    The hmac_secret is explicitly excluded to prevent accidental exposure.
    """

    id: NonEmptyString
    consumer: NonEmptyString
    webhook_url: NonEmptyString
    active: bool
    event_types: list[WebhookEventType] = Field(default_factory=_default_webhook_event_types)
    created_at: UTCDateTime
    updated_at: UTCDateTime


class WebhookSubscriptionCreated(WebhookSubscriptionResponse):
    """Creation response containing the signing secret exactly once."""

    signing_secret: NonEmptyString = Field(repr=False)


class WebhookSecretRotationResponse(PitwallModel):
    """Secret rotation response; the new secret is never listable later."""

    id: NonEmptyString
    signing_secret: NonEmptyString = Field(repr=False)


class WebhookDeliveryFailure(PitwallModel):
    """Records a failed consumer webhook delivery attempt.

    Delivery failures are tracked separately from workload state so that
    transient delivery failures do not pollute workload state. Bounded
    retries (max 4 attempts) are applied before giving up.
    """

    id: int | None = None
    workload_id: NonEmptyString
    subscription_id: Annotated[int, Field(ge=1)]
    attempt: Annotated[int, Field(ge=1, le=4)]
    attempted_at: UTCDateTime
    next_retry_at: UTCDateTime | None = None
    payload: JsonObject
    status_code: Annotated[int | None, Field(ge=100, le=599)] = None
    error_message: str | None = None


__all__ = [
    "Capability",
    "CapabilityDefaults",
    "ConfigAuditEntry",
    "JsonObject",
    "Lease",
    "LeaseEndpoints",
    "LeaseReadiness",
    "LeaseTcpEndpoint",
    "PitwallModel",
    "Provider",
    "UTCDateTime",
    "UsdAmount",
    "WebhookDeliveryFailure",
    "WebhookEventType",
    "WebhookSubscription",
    "WebhookSubscriptionCreate",
    "WebhookSubscriptionCreated",
    "WebhookSubscriptionResponse",
    "WebhookSecretRotationResponse",
    "Workload",
]
