"""Pydantic v2 schemas for the Provider API surface."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import Field, ValidationError, field_validator, model_validator

from pitwall.core.enums import CapabilitySource, CostMode, ProviderAdapterId, ProviderType
from pitwall.core.models import (
    CredentialReferenceName,
    JsonObject,
    NonNegativeInt,
    PitwallModel,
    StoredJsonObject,
    default_credential_reference,
    redact_provider_serialized_config,
    validate_provider_serialized_config,
    validate_provider_storage_payload,
)
from pitwall.providers.interface import ProviderDeclaration
from pitwall.providers.selfhosted.profile import SelfHostedProfile
from pitwall.runpod_client.gpu import (
    validate_canonical_gpu_name,
    validate_canonical_gpu_names,
)

Priority = Annotated[int, Field(ge=0, strict=True)]

_SINGLE_GPU_CONFIG_KEYS = ("gpu_class", "gpu_type", "gpu_type_id", "gpu_name")
_LIST_GPU_CONFIG_KEYS = (
    "gpu_type_priority",
    "gpu_types",
    "gpu_classes",
    "gpuTypeIds",
)
_REQUIRED_COST_KEYS_BY_MODE = {
    CostMode.PER_SECOND: ("per_second_active",),
    CostMode.PER_REQUEST: ("per_request",),
    CostMode.PER_TOKEN: (
        "per_million_input_tokens",
        "per_million_output_tokens",
    ),
    CostMode.ZERO: (),
}


def _type_value(provider_type: ProviderType | str | None) -> str | None:
    return None if provider_type is None else str(provider_type)


def _declaration(provider_type: ProviderType | str | None) -> ProviderDeclaration | None:
    from pitwall.providers.registry import declaration_for_provider_type  # local: import cycle

    return declaration_for_provider_type(_type_value(provider_type))


def _declared_types(field_name: str) -> frozenset[str]:
    """Union one provider-type set across every registered adapter declaration."""

    from pitwall.providers.registry import get_default_registry  # local: import cycle

    return frozenset().union(
        *(
            getattr(declaration, field_name)
            for _, declaration in get_default_registry().declarations()
        )
    )


def expected_openai_base_url(
    provider_type: ProviderType,
    endpoint_id: str,
) -> str:
    """Return the OpenAI-compatible base URL the owning adapter derives for a provider surface."""

    declaration = _declaration(provider_type)
    if declaration is None or declaration.openai_base_url is None:
        raise ValueError(f"{provider_type.value} providers do not expose openai_base_url")
    return declaration.openai_base_url(provider_type.value, endpoint_id)


def expected_lb_base_url(endpoint_id: str) -> str:
    """Return the load-balancer base URL of a registered endpoint."""

    declaration = _declaration(None)
    if declaration is None or declaration.lb_base_url is None:
        raise ValueError("no provider adapter derives a load-balancer base URL")
    return declaration.lb_base_url(endpoint_id)


def validate_openai_provider_type(provider_type: ProviderType) -> None:
    """Validate that a provider type is OpenAI-compatible.

    Raises ValueError for a provider type whose adapter declares no OpenAI base URL, such as
    a pod lease, which does not support the OpenAI pass-through route.
    """
    allowed_types = _declared_types("openai_url_types")
    if provider_type.value not in allowed_types:
        allowed = ", ".join(sorted(allowed_types))
        raise ValueError(
            f"provider_type must be one of: {allowed}; "
            f"got {provider_type.value!r} which is not OpenAI-compatible"
        )


def _validate_cloud_type_volume(
    cloud_type: str | None,
    config: Mapping[str, Any],
) -> None:
    """L2: Reject cloud_type=ALL when networkVolumeId is set.

    RunPod requires cloud_type=SECURE for volume-attached providers.
    Using ALL wastes 50%% of fallback attempts because ALL only attempts
    COMMUNITY first and never retries with SECURE on failure.
    """
    if cloud_type is None:
        cloud_type = config.get("cloud_type") or config.get("cloudType")
    if cloud_type is None:
        return
    cloud_type_upper = str(cloud_type).upper()
    if cloud_type_upper != "ALL":
        return
    has_volume = bool(
        config.get("networkVolumeId")
        or config.get("network_volume_id")
        or config.get("volumeId")
        or config.get("volume_id")
    )
    if has_volume:
        raise ValueError(
            "cloud_type=ALL is not permitted with networkVolumeId; "
            "RunPod requires cloud_type=SECURE for volume-attached providers"
        )


def validate_provider_registration_config(
    *,
    provider_type: ProviderType | None,
    endpoint_id: str | None,
    cloud_type: str | None,
    config: Mapping[str, Any],
) -> None:
    """Validate provider config invariants that are shared across API surfaces."""

    declaration = _declaration(provider_type)
    if (
        provider_type is not None
        and declaration is not None
        and declaration.validate_endpoint is not None
    ):
        declaration.validate_endpoint(provider_type.value, endpoint_id)
    validate_provider_serialized_config(config)
    _validate_cloud_type_volume(cloud_type, config)
    _validate_gpu_config(config)
    _validate_cost_config(config)
    _validate_url_config(provider_type, endpoint_id, config)
    _validate_pod_lease_serve_config(config)
    _validate_self_hosted_config(provider_type, config)


def _validate_url_config(
    provider_type: ProviderType | None,
    endpoint_id: str | None,
    config: Mapping[str, Any],
) -> None:
    """Let the adapter that owns the provider type validate its URL-bearing config."""

    declaration = _declaration(provider_type)
    if declaration is not None and declaration.validate_url is not None:
        declaration.validate_url(_type_value(provider_type), endpoint_id, config)


def _validate_self_hosted_config(
    provider_type: ProviderType | None,
    config: Mapping[str, Any],
) -> None:
    raw_profile = config.get("self_hosted")
    if raw_profile is None:
        return
    if provider_type is not None and provider_type.value not in _declared_types(
        "self_hosted_types"
    ):
        raise ValueError("config.self_hosted is only valid for public_endpoint providers")
    try:
        SelfHostedProfile.model_validate(raw_profile)
    except ValidationError as exc:
        # The ValidationError's own text includes input values; render loc and msg only.
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
            for error in exc.errors(include_input=False, include_url=False)
        )
        raise ValueError(f"config.self_hosted is invalid: {problems}") from None


def _validate_gpu_config(config: Mapping[str, Any]) -> None:
    for key in _SINGLE_GPU_CONFIG_KEYS:
        if key not in config or config[key] is None:
            continue
        value = config[key]
        if not isinstance(value, str):
            raise ValueError(f"config.{key} must be a string")
        validate_canonical_gpu_name(value)

    for key in _LIST_GPU_CONFIG_KEYS:
        if key not in config or config[key] is None:
            continue
        value = config[key]
        if not isinstance(value, list):
            raise ValueError(f"config.{key} must be a list of canonical GPU names")
        if not value:
            raise ValueError(f"config.{key} must include at least one GPU name")
        if not all(isinstance(item, str) for item in value):
            raise ValueError(f"config.{key} must contain only strings")
        validate_canonical_gpu_names(value)


def _validate_cost_config(config: Mapping[str, Any]) -> None:
    raw_cost = config.get("cost")
    if raw_cost is None:
        return
    if not isinstance(raw_cost, Mapping):
        raise ValueError("config.cost must be an object")

    raw_mode = raw_cost.get("mode")
    mode = _parse_cost_mode(raw_mode) if raw_mode is not None else None
    if mode is not None:
        for key in _REQUIRED_COST_KEYS_BY_MODE[mode]:
            _require_non_negative_decimal(raw_cost, key)
        return

    for key in (
        "per_second_active",
        "per_request",
        "per_million_input_tokens",
        "per_million_output_tokens",
    ):
        if key in raw_cost:
            _require_non_negative_decimal(raw_cost, key)


def _parse_cost_mode(value: object) -> CostMode:
    if isinstance(value, CostMode):
        return value
    if isinstance(value, str):
        try:
            return CostMode(value)
        except ValueError as exc:
            allowed = ", ".join(cost_mode.value for cost_mode in CostMode)
            raise ValueError(f"config.cost.mode must be one of: {allowed}") from exc
    raise ValueError("config.cost.mode must be a string")


def _require_non_negative_decimal(cost: Mapping[str, Any], key: str) -> None:
    if key not in cost:
        raise ValueError(f"config.cost.mode requires config.cost.{key}")
    value = cost[key]
    if isinstance(value, bool):
        raise ValueError(f"config.cost.{key} must be a non-negative decimal")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"config.cost.{key} must be a non-negative decimal") from exc
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError(f"config.cost.{key} must be a non-negative decimal")


_POD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")
_TEMPLATE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,63}$")


def _validate_template_id(config: Mapping[str, Any]) -> None:
    value = config.get("template_id")
    if value is not None and (
        not isinstance(value, str) or _TEMPLATE_ID_RE.fullmatch(value) is None
    ):
        raise ValueError(f"config.template_id must match {_TEMPLATE_ID_RE.pattern}")


def _validate_serve_selection_config(config: Mapping[str, Any]) -> None:
    engine = config.get("engine")
    if engine is not None and (
        not isinstance(engine, str) or engine not in {"vllm", "llama.cpp", "sglang"}
    ):
        raise ValueError("config.engine must be 'vllm', 'llama.cpp', or 'sglang'")
    variant = config.get("variant")
    if variant is not None and (
        not isinstance(variant, str) or not variant.strip() or variant != variant.strip()
    ):
        raise ValueError("config.variant must be a non-empty string without whitespace")
    gpu_count = config.get("gpu_count")
    if gpu_count is not None and (
        isinstance(gpu_count, bool) or not isinstance(gpu_count, int) or gpu_count < 1
    ):
        raise ValueError("config.gpu_count must be an integer greater than or equal to 1")


def _validate_pod_lease_serve_config(config: Mapping[str, Any]) -> None:
    """Validate the keys the lease readiness/teardown hooks and serve-model write."""

    _validate_serve_selection_config(config)
    _validate_template_id(config)
    _validate_warm_cache(config)

    value = config.get("startup_timeout_s")
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or not 60 <= value <= 7_200
    ):
        raise ValueError("config.startup_timeout_s must be an integer between 60 and 7200")

    readiness_path = config.get("readiness_path")
    if readiness_path is not None and (
        not isinstance(readiness_path, str)
        or not readiness_path.startswith("/")
        or len(readiness_path) > 128
    ):
        raise ValueError(
            "config.readiness_path must be a string beginning with '/' and at most 128 characters"
        )

    port = config.get("openai_proxy_port")
    if port is not None:
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65_535:
            raise ValueError("config.openai_proxy_port must be an integer between 1 and 65535")
        ports = config.get("ports")
        http_ports: list[object] = []
        if isinstance(ports, Mapping):
            raw = ports.get("http")
            http_ports = list(raw) if isinstance(raw, list) else [raw]
        if port not in http_ports:
            raise ValueError("config.openai_proxy_port must be listed in config.ports.http")

    pod_id = config.get("active_pod_id")
    if pod_id is not None and (not isinstance(pod_id, str) or not _POD_ID_RE.match(pod_id)):
        raise ValueError(f"config.active_pod_id must match {_POD_ID_RE.pattern}")

    lease_id = config.get("active_lease_id")
    if lease_id is not None and (not isinstance(lease_id, str) or not lease_id.strip()):
        raise ValueError("config.active_lease_id must be a non-empty string")

    start_cmd = config.get("docker_start_cmd")
    if start_cmd is not None and (
        not isinstance(start_cmd, list)
        or not all(isinstance(item, str) and item.strip() for item in start_cmd)
    ):
        raise ValueError("config.docker_start_cmd must be a list of non-empty strings")

    entrypoint = config.get("docker_entrypoint")
    if entrypoint is not None and (
        not isinstance(entrypoint, list)
        or not all(isinstance(item, str) and item.strip() for item in entrypoint)
    ):
        raise ValueError("config.docker_entrypoint must be a list of non-empty strings")

    endpoint_auth = config.get("endpoint_auth")
    if endpoint_auth is not None and not isinstance(endpoint_auth, bool):
        raise ValueError("config.endpoint_auth must be a boolean")


def _validate_warm_cache(config: Mapping[str, Any]) -> None:
    """Validate a verified model-cache record retained in provider config."""

    value = config.get("warm_cache")
    if value is None:
        return
    if not isinstance(value, Mapping):
        raise ValueError("config.warm_cache must be an object")
    for key in ("variant", "verified_at", "volume_id"):
        item = value.get(key)
        if not isinstance(item, str) or not item.strip() or item != item.strip():
            raise ValueError(f"config.warm_cache.{key} must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value["verified_at"])
    except ValueError as exc:
        raise ValueError("config.warm_cache.verified_at must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("config.warm_cache.verified_at must include a timezone")


def warm_cache_matches(config: Mapping[str, Any], *, variant: str | None) -> bool:
    """Return whether config's verified cache belongs to this variant and volume."""

    volume_id = config.get("network_volume_id")
    warm_cache = config.get("warm_cache")
    return bool(
        isinstance(volume_id, str)
        and volume_id
        and variant is not None
        and isinstance(warm_cache, Mapping)
        and warm_cache.get("variant") == variant
        and warm_cache.get("volume_id") == volume_id
    )


def _validate_secret_free_config(value: object) -> object:
    if value is None or not isinstance(value, Mapping):
        return value
    try:
        validate_provider_serialized_config(value)
    except ValueError:
        if isinstance(value, dict):
            redacted = redact_provider_serialized_config(value)
            value.clear()
            value.update(redacted)
        raise
    return value


class ProviderCreate(PitwallModel):
    """Request body for POST /v1/admin/providers."""

    capability_id: Annotated[str, Field(min_length=1)]
    name: Annotated[str, Field(min_length=1)]
    adapter_id: ProviderAdapterId = ProviderAdapterId.RUNPOD
    credential_ref: CredentialReferenceName = "RUNPOD_API_KEY"
    provider_type: ProviderType
    runpod_endpoint_id: str | None = None
    runpod_template_id: str | None = None
    region: str | None = None
    cloud_type: str | None = None
    config: StoredJsonObject = Field(default_factory=dict)
    priority: Priority = 0
    enabled: bool = True
    health_status: str = "unknown"
    consecutive_failures: NonNegativeInt = 0
    cooldown_trips: NonNegativeInt = 0
    cold_start_p50_ms: NonNegativeInt | None = None
    cold_start_p95_ms: NonNegativeInt | None = None
    recent_error_rate: float = Field(default=0, ge=0, le=1)
    source: CapabilitySource = CapabilitySource.API

    @field_validator("config", mode="before")
    @classmethod
    def _reject_credential_values(cls, value: object) -> object:
        return _validate_secret_free_config(value)

    @model_validator(mode="after")
    def _validate_registration_config(self) -> ProviderCreate:
        if "credential_ref" not in self.model_fields_set:
            self.credential_ref = default_credential_reference(self.adapter_id)
        validate_provider_registration_config(
            provider_type=self.provider_type,
            endpoint_id=self.runpod_endpoint_id,
            cloud_type=self.cloud_type,
            config=self.config,
        )
        validate_provider_storage_payload(self.model_dump(mode="json"))
        return self


class ProviderPatch(PitwallModel):
    """Request body for PATCH /v1/admin/providers/{id}.

    All fields are optional — only supplied fields are merged.
    """

    name: Annotated[str, Field(min_length=1)] | None = None
    adapter_id: ProviderAdapterId | None = None
    credential_ref: CredentialReferenceName | None = None
    provider_type: ProviderType | None = None
    runpod_endpoint_id: str | None = None
    runpod_template_id: str | None = None
    region: str | None = None
    cloud_type: str | None = None
    config: StoredJsonObject | None = None
    priority: Priority | None = None
    enabled: bool | None = None
    health_status: str | None = None
    consecutive_failures: NonNegativeInt | None = None
    cooldown_trips: NonNegativeInt | None = None
    cold_start_p50_ms: NonNegativeInt | None = None
    cold_start_p95_ms: NonNegativeInt | None = None
    recent_error_rate: float | None = Field(default=None, ge=0, le=1)

    @field_validator("config", mode="before")
    @classmethod
    def _reject_credential_values(cls, value: object) -> object:
        return _validate_secret_free_config(value)

    @model_validator(mode="after")
    def _validate_registration_config(self) -> ProviderPatch:
        if self.config is not None:
            validate_provider_registration_config(
                provider_type=self.provider_type,
                endpoint_id=self.runpod_endpoint_id,
                cloud_type=self.cloud_type,
                config=self.config,
            )
        validate_provider_storage_payload(self.model_dump(mode="json", exclude_none=True))
        return self


class ProviderListFilter(PitwallModel):
    """Query parameters for GET /v1/providers."""

    capability_id: str | None = None
    enabled: bool | None = None
    provider_type: ProviderType | None = None


class ProviderResponse(PitwallModel):
    """Response body for GET /v1/providers."""

    id: str
    capability_id: str
    name: str
    adapter_id: ProviderAdapterId
    credential_ref: CredentialReferenceName
    provider_type: ProviderType
    runpod_endpoint_id: str | None = None
    runpod_template_id: str | None = None
    region: str | None = None
    cloud_type: str | None = None
    config: JsonObject = Field(default_factory=dict)
    priority: Priority
    enabled: bool = True
    health_status: str = "unknown"
    consecutive_failures: NonNegativeInt = 0
    cooldown_trips: NonNegativeInt = 0
    cold_start_p50_ms: NonNegativeInt | None = None
    cold_start_p95_ms: NonNegativeInt | None = None
    recent_error_rate: float = Field(default=0, ge=0, le=1)
    cooldown_until: str | None = None
    source: CapabilitySource = CapabilitySource.API
    last_applied_yaml_hash: str | None = None
    updated_at: str


class ProviderHealthResponse(PitwallModel):
    """Response body for GET /v1/providers/{id}/health."""

    id: str
    name: str
    health_status: str
    consecutive_failures: NonNegativeInt = 0
    cooldown_trips: NonNegativeInt = 0
    cooldown_until: str | None = None
    recent_error_rate: float
    updated_at: str


class ProviderHibernateResponse(PitwallModel):
    """Response body for POST /v1/admin/providers/{id}/hibernate."""

    id: str
    name: str
    health_status: str
    cooldown_until: str | None = None
    enabled: bool


class EndpointCostConfig(PitwallModel):
    """Cost configuration for endpoint registration.

    Fields:
        per_second_active: Cost per second when container is active (container-seconds).
            Used for serverless_queue and serverless_lb provider types.
        per_request: Flat cost per request. Used for public_endpoint provider types.
        per_million_input_tokens: Cost per million input tokens. Used for per_token cost mode.
        per_million_output_tokens: Cost per million output tokens. Used for per_token cost mode.
    """

    mode: CostMode | None = Field(default=None, description="Cost estimator mode")
    per_second_active: float | None = Field(
        default=None,
        ge=0,
        description="Cost per active container-second (USD)",
    )
    per_request: float | None = Field(
        default=None,
        ge=0,
        description="Flat cost per request (USD)",
    )
    per_million_input_tokens: float | None = Field(
        default=None,
        ge=0,
        description="Cost per million input tokens (USD)",
    )
    per_million_output_tokens: float | None = Field(
        default=None,
        ge=0,
        description="Cost per million output tokens (USD)",
    )

    @model_validator(mode="after")
    def _validate_cost_mode_requirements(self) -> EndpointCostConfig:
        if self.mode is None:
            return self
        cost_values = self.model_dump(mode="python", exclude_none=True)
        for key in _REQUIRED_COST_KEYS_BY_MODE[self.mode]:
            _require_non_negative_decimal(cost_values, key)
        return self


class EndpointWorkersConfig(PitwallModel):
    """Worker scaling configuration for serverless endpoints.

    Fields:
        workers_min: Minimum number of always-on workers. Set to 0 to hibernate.
            Default is 0 for hibernated endpoints, 1 for minimum always-on.
        workers_max: Maximum number of workers the endpoint can scale to.
            Controls the rate limit ceiling: max(base_limit, workers_max x per_worker_limit).
    """

    workers_min: NonNegativeInt = Field(
        default=0,
        description="Minimum always-on worker count (0 = hibernated)",
    )
    workers_max: NonNegativeInt | None = Field(
        default=None,
        ge=0,
        description="Maximum worker count for auto-scaling",
    )


class EndpointRegistrationConfig(PitwallModel):
    """Configuration fields for endpoint registration.

    This schema explicitly defines the fields used when registering
    a RunPod endpoint with Pitwall.

    Fields:
        gpu_class: Canonical RunPod GPU type ID (e.g., "NVIDIA H100 NVL", "RTX 4090").
            Must match RunPod's gpuTypeId exactly. Used for routing and scoring.
        cost: Cost parameters for the endpoint. Mode is determined by capability cost_mode.
        workers: Worker scaling settings for serverless endpoints.
        idle_timeout_minutes: Idle timeout before container scales to zero.
            Applies to serverless_queue and serverless_lb provider types.
        flash_boot_verified: Whether FlashBoot has been verified in the RunPod console.
            FlashBoot reduces cold-start by ~35s but has a runpodctl 0.x regression
            that silently no-ops on create and fails on update. Must be verified manually.
        max_payload_mb: Maximum payload size in megabytes. Defaults to 30 for LB endpoints.
        request_timeout_s: Request timeout in seconds. Default is 330 for LB endpoints.
        custom_paths: Custom HTTP paths exposed by the endpoint workers.
            Maps path name to route (e.g., {"embed": "/embed", "health": "/ping"}).
        lb_base_url: Base URL for load-balancing serverless endpoints.
            Auto-constructed from endpoint_id if not provided.
        openai_base_url: OpenAI-compatible base URL.
            Must be composed from endpoint_id and provider_type if provided.
    """

    gpu_class: Annotated[
        str,
        Field(min_length=1, description="Canonical RunPod GPU type ID"),
    ]
    cost: EndpointCostConfig = Field(default_factory=EndpointCostConfig)
    workers: EndpointWorkersConfig = Field(default_factory=EndpointWorkersConfig)
    idle_timeout_minutes: Annotated[
        int,
        Field(
            ge=0,
            le=60,
            description="Idle timeout in minutes before scale-to-zero (0-60)",
        ),
    ] = 0
    flash_boot_verified: bool = Field(
        default=False,
        description="Whether FlashBoot setting has been verified in RunPod console",
    )
    max_payload_mb: Annotated[
        int,
        Field(ge=1, le=1024, description="Maximum request payload size in MB"),
    ] = 30
    request_timeout_s: Annotated[
        int,
        Field(ge=1, le=900, description="Request timeout in seconds"),
    ] = 330
    custom_paths: JsonObject = Field(
        default_factory=dict,
        description="Custom path mappings (e.g., {'embed': '/embed'})",
    )
    lb_base_url: str | None = Field(
        default=None,
        description="LB endpoint base URL (auto-constructed if not provided)",
    )
    openai_base_url: str | None = Field(
        default=None,
        description="OpenAI-compatible endpoint base URL",
    )

    @field_validator("gpu_class")
    @classmethod
    def _validate_gpu_class(cls, gpu_class: str) -> str:
        return validate_canonical_gpu_name(gpu_class)


class EndpointRegistrationRequest(PitwallModel):
    """Request body for endpoint registration CLI and API.

    This schema consolidates the fields required to register a RunPod
    endpoint with Pitwall: endpoint ID, provider type, cost parameters,
    GPU class, worker settings, idle timeout, and FlashBoot verification.

    Fields:
        endpoint_id: RunPod endpoint ID (e.g., "eptest00000000").
            For serverless_lb, this is the LB endpoint ID used in URLs like
            https://{endpoint_id}.api.runpod.ai.
        provider_type: RunPod surface type (serverless_queue, serverless_lb,
            public_endpoint, pod_lease).
        capability_id: ID of the capability this endpoint fulfills.
        name: Human-readable name for this provider.
        region: RunPod region ID (e.g., "US-KS-2", "US-CA-2").
        config: Endpoint configuration including gpu_class, cost, workers, etc.
        priority: Routing priority (lower = preferred). Default is 0.
    """

    endpoint_id: Annotated[
        str,
        Field(min_length=1, description="RunPod endpoint ID"),
    ]
    provider_type: ProviderType
    capability_id: Annotated[
        str,
        Field(min_length=1, description="Capability ID this endpoint fulfills"),
    ]
    name: Annotated[
        str,
        Field(min_length=1, description="Human-readable provider name"),
    ]
    region: str | None = Field(
        default=None,
        description="RunPod region ID (e.g., US-KS-2)",
    )
    config: EndpointRegistrationConfig
    priority: Priority = Field(
        default=0,
        description="Routing priority (lower = preferred)",
    )

    @model_validator(mode="after")
    def _validate_registration_config(self) -> EndpointRegistrationRequest:
        config = self.config.model_dump(mode="python", exclude_none=True)
        validate_provider_registration_config(
            provider_type=self.provider_type,
            endpoint_id=self.endpoint_id,
            cloud_type=None,
            config=config,
        )
        if self.config.lb_base_url is not None:
            if self.provider_type.value not in _declared_types("lb_url_types"):
                raise ValueError("config.lb_base_url is only valid for serverless_lb providers")
            expected = expected_lb_base_url(self.endpoint_id)
            if self.config.lb_base_url.rstrip("/") != expected:
                raise ValueError(f"config.lb_base_url must be {expected!r}")
        return self


__all__ = [
    "EndpointCostConfig",
    "EndpointRegistrationConfig",
    "EndpointRegistrationRequest",
    "expected_lb_base_url",
    "expected_openai_base_url",
    "ProviderCreate",
    "ProviderPatch",
    "ProviderListFilter",
    "ProviderResponse",
    "ProviderHealthResponse",
    "ProviderHibernateResponse",
    "validate_openai_provider_type",
    "validate_provider_registration_config",
]
