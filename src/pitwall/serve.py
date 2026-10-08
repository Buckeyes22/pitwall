"""Launch and verify OpenAI-compatible model servers on pod leases."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import math
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal
from typing import Annotated, Any, Literal, Protocol, overload

import httpx
from pydantic import Field, field_validator, model_validator

from pitwall.api.admin.kill_switch import CloudKillSwitch, KillSwitchEngaged
from pitwall.api.exceptions import (
    LeaseLaunchInProgress,
    LeaseNotServing,
    LeaseStateConflict,
    PreSpendPayloadRejected,
    ServeBudgetExhausted,
    ServeCapExceeded,
    ServeConflict,
    ServeInvalidGpuClass,
    ServeKillSwitchEngaged,
    ServeLaunchFailed,
    ServeNoServeHistory,
    ServePriceUnknown,
    ServeRateRequired,
    ServeStalePrice,
    ServeTemplateInvalid,
    ServeTtlBelowStartup,
    ServeUnknownVariant,
    ServeVerificationFailed,
    ServeWarmFailed,
)
from pitwall.api.leases.launch import (
    InvalidProviderConfig,
    estimate_lease_launch_cost,
    launch_request_fingerprint,
    replay_idempotent_launch,
    run_launch,
)
from pitwall.api.leases.teardown import run_teardown
from pitwall.api.provider_schemas import (
    validate_provider_registration_config,
    warm_cache_matches,
)
from pitwall.config import PitwallSettings
from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    CostMode,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderType,
)
from pitwall.core.ids import ulid_new
from pitwall.core.models import Capability, Lease, PitwallModel, Provider
from pitwall.cost.budget_gate import BudgetRejected
from pitwall.db.repository import (
    CapabilityRepository,
    LeaseRepository,
    ProviderRepository,
    WorkloadRepository,
)
from pitwall.leases.events import build_lease_ready_event, publish_lease_event
from pitwall.leases.state import ACTIVE_LEASE_STATES
from pitwall.models.errors import UnknownVariant as CatalogueUnknownVariant
from pitwall.models.fit import FitVerdict
from pitwall.models.lookup import CatalogueLookup, CompanionInfo, Engine, VariantInfo
from pitwall.models.prices import (
    GpuPriceFreshness,
    GpuPriceSnapshot,
    gpu_price_freshness,
    load_gpu_price_snapshot,
)
from pitwall.models.schema import Variant
from pitwall.providers.selfhosted import (
    ReadinessObservation,
    SelfHostedProfile,
    oracle_for,
    self_hosted_profile,
    self_hosted_state,
)
from pitwall.redis_env import optional_redis_from_env
from pitwall.resolver.provider_urls import public_endpoint_url
from pitwall.routing.openai import pod_lease_base_url
from pitwall.runpod_client.gpu import (
    GPU_VRAM_GB,
    NonCanonicalGPUNameError,
    validate_canonical_gpu_name,
)
from pitwall.runpod_client.pods import RunPodError
from pitwall.runpod_client.templates import validate_image_ref
from pitwall.security.pre_spend import (
    PreSpendDecision,
    get_pre_spend_inspection_service,
)
from pitwall.security.redaction import contains_redactable_secret

log = logging.getLogger(__name__)

SERVE_PROVIDER_PREFIX = "serve-"
SERVE_HTTP_PORT = 8000
ENDPOINT_KEY_ENV = "PITWALL_ENDPOINT_KEY"
DEFAULT_TTL_MINUTES = 120
DEFAULT_STARTUP_TIMEOUT_S = 1_800
VERIFY_REQUEST_TIMEOUT_S = 10.0
VERIFY_RETRY_INTERVAL_S = 5.0
VERIFY_TOTAL_TIMEOUT_S = 60.0
WARM_DIAGNOSTIC_TIMEOUT_S = 0.1
ENGINE_READINESS_PATH: Mapping[Engine, str] = {
    "vllm": "/health",
    "llama.cpp": "/health",
    "sglang": "/health_generate",
}


def _authenticated_start_shape(command: Sequence[str]) -> tuple[list[str], list[str]]:
    """Mark a generated command for the shell entrypoint used by endpoint auth.

    The launch adapter adds the shell expansion at the final pod-create boundary.
    Keeping the raw command in the provider record avoids replay wrapping and keeps
    the secret out of persisted state; only the pod's launch environment contains it.
    """

    if not command or any(not isinstance(item, str) or not item.strip() for item in command):
        raise ServeTemplateInvalid("serve command must contain non-empty arguments")
    if any(item == "--api-key" or item.startswith("--api-key=") for item in command):
        raise ServeTemplateInvalid("serve command must not provide a literal model API key")
    return list(command), ["sh", "-c"]


class NoCatalogue:
    """Catalogue implementation for installations without model dossiers."""

    def variant(self, model_id: str, variant_id: str | None) -> VariantInfo | None:
        return None


class PlanCatalogue(CatalogueLookup, Protocol):
    """Catalogue operations needed by the database-free planner."""

    def dossier_variant(self, model_id: str, variant_id: str | None) -> Variant: ...


CapabilityName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
SafeToken = Annotated[str, Field(pattern=r"^[^\x00-\x20\x7f]+$")]
EnvironmentValue = Annotated[str, Field(pattern=r"^[^\x00-\x1f\x7f]*$")]


class ServeRequest(PitwallModel):
    capability_name: CapabilityName
    model: SafeToken | None = None
    gpu_class: Annotated[str, Field(min_length=1)] | None = None
    gpu_count: Annotated[int, Field(ge=1)] = 1
    engine: Engine | None = None
    variant: SafeToken | None = None
    template_id: Annotated[str, Field(min_length=2, max_length=64)] | None = None
    ttl_minutes: Annotated[int, Field(ge=1, le=10_080)] = DEFAULT_TTL_MINUTES
    idle_timeout_min: Annotated[int, Field(ge=5, le=10_080)] | None = None
    max_usd_per_hour: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=4)
    renewal_policy: LeaseRenewalPolicy | None = None
    image: SafeToken | None = None
    served_model_name: SafeToken | None = None
    datacenter: SafeToken | None = None
    network_volume_id: SafeToken | None = None
    container_disk_gb: Annotated[int, Field(ge=1)] | None = None
    rate_per_second: Decimal | None = Field(default=None, ge=0)
    price_source: Literal["live", "fallback"] | None = None
    gated: bool = False
    env: dict[SafeToken, EnvironmentValue] = Field(default_factory=dict)
    start_args: list[SafeToken] = Field(default_factory=list)
    dry_run: bool = False
    idempotency_key: SafeToken | None = None

    @model_validator(mode="after")
    def _default_renewal_policy(self) -> ServeRequest:
        if self.renewal_policy is None:
            object.__setattr__(
                self,
                "renewal_policy",
                LeaseRenewalPolicy.ACTIVITY
                if self.idle_timeout_min is not None
                else LeaseRenewalPolicy.MANUAL,
            )
        return self

    @field_validator("image")
    @classmethod
    def _validate_image(cls, value: str | None) -> str | None:
        return validate_image_ref(value) if value is not None else None

    @field_validator("start_args")
    @classmethod
    def _reject_secret_start_args(cls, values: list[str]) -> list[str]:
        if any(item == "--api-key" or item.startswith("--api-key=") for item in values):
            raise ValueError("start_args cannot provide a model API key")
        if any(contains_redactable_secret(value) for value in values):
            raise ValueError(
                "start_args cannot contain secret-like values; use launch-only credentials"
            )
        return values

    @property
    def served_model_id(self) -> str:
        if self.served_model_name is not None:
            return self.served_model_name
        if self.model is None:
            raise ServeNoServeHistory(self.capability_name)
        return self.model


class ServeResult(PitwallModel):
    capability: str
    lease_id: str | None
    expires_at: str | None
    model_id: str
    proxy_base_url: str
    engine: Engine
    variant: str | None
    gpu_count: int
    workload_id: str | None
    template_id: str | None
    provider_id: str
    dry_run: bool
    created: bool | None
    cost_estimate_usd: str | None
    price_age_seconds: int | None = None
    price_source: Literal["live", "fallback"] | None = None
    price_stale: bool | None = None
    provider_kind: Literal["pod_lease", "self_hosted"] = "pod_lease"

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def enforce_price_cap(
    *,
    gpu_class: str,
    max_usd_per_hour: Decimal | None,
    snapshot: GpuPriceSnapshot,
    freshness: GpuPriceFreshness | None,
) -> None:
    if max_usd_per_hour is None:
        return
    priced = next((item for item in snapshot.gpu_types if item.id == gpu_class), None)
    price = priced.secure_price if priced is not None else None
    unknown = snapshot.source != "live" or freshness is None or freshness.stale or price is None
    if unknown:
        raise ServePriceUnknown(
            gpu_class=gpu_class,
            max_usd_per_hour=max_usd_per_hour,
        )
    assert price is not None
    if price > max_usd_per_hour:
        raise ServeCapExceeded(
            gpu_class=gpu_class,
            price_usd_per_hour=price,
            max_usd_per_hour=max_usd_per_hour,
        )


class ServePlanRequest(PitwallModel):
    """Catalogue-only inputs that never require registry state."""

    model: SafeToken
    gpu_class: Annotated[str, Field(min_length=1)]
    gpu_count: Annotated[int, Field(ge=1)] = 1
    engine: Engine | None = None
    variant: SafeToken | None = None
    ttl_minutes: Annotated[int, Field(ge=1, le=10_080)] = DEFAULT_TTL_MINUTES
    image: SafeToken | None = None
    served_model_name: SafeToken | None = None
    network_volume_id: SafeToken | None = None
    env: dict[SafeToken, EnvironmentValue] = Field(default_factory=dict)
    start_args: list[SafeToken] = Field(default_factory=list)

    @field_validator("image")
    @classmethod
    def _validate_image(cls, value: str | None) -> str | None:
        return validate_image_ref(value) if value is not None else None

    @field_validator("start_args")
    @classmethod
    def _reject_secret_start_args(cls, values: list[str]) -> list[str]:
        if any(item == "--api-key" or item.startswith("--api-key=") for item in values):
            raise ValueError("start_args cannot provide a model API key")
        if any(contains_redactable_secret(value) for value in values):
            raise ValueError(
                "start_args cannot contain secret-like values; use launch-only credentials"
            )
        return values


class ServePlanResult(PitwallModel):
    """Mutation-free launch plan derived from catalogue and price snapshot data."""

    model_id: str
    engine: Engine
    variant: str | None
    gpu_class: str
    gpu_count: int
    image: str
    argv: list[str]
    volume_cache_env: dict[str, str]
    fit: FitVerdict
    startup_timeout_s: int
    cost_estimate_usd: str | None
    price_source: Literal["live", "fallback"]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class WarmResult(PitwallModel):
    """Result of starting a serve lease solely to populate a network volume."""

    capability: str
    provider_id: str
    lease_id: str | None
    volume_id: str
    datacenter: str | None
    model_id: str
    variant: str | None
    engine: Engine
    gpu_class: str
    gpu_count: int
    price_source: Literal["live", "fallback"] | None
    seconds_to_ready: float | None
    torn_down: bool
    cost_estimate_usd: str | None
    dry_run: bool
    cache_state: Literal["warm", "cold"] = "cold"

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


def launch_shape(
    engine: Engine,
    *,
    model: str,
    served: str,
    gpu_count: int,
    repo: str | None,
    file: str | None,
    flags: Sequence[str],
    companion_flags: Sequence[str],
    start_args: Sequence[str],
) -> list[str]:
    """Build the exact container argument vector for one supported engine."""

    if engine == "vllm":
        result = ["vllm-omni", "serve"] if "--omni" in flags else []
        result.extend(
            [
                model,
                "--served-model-name",
                served,
                "--host",
                "0.0.0.0",
                "--port",
                str(SERVE_HTTP_PORT),
            ]
        )
        if gpu_count > 1:
            result.extend(["--tensor-parallel-size", str(gpu_count)])
    elif engine == "llama.cpp":
        if not repo or not file:
            raise ServeTemplateInvalid("llama.cpp requires catalogue repo and file")
        result = [
            "--hf-repo",
            repo,
            "--hf-file",
            file,
            "--alias",
            served,
            "--host",
            "0.0.0.0",
            "--port",
            str(SERVE_HTTP_PORT),
            "--n-gpu-layers",
            "all",
            "--jinja",
        ]
    else:
        result = [
            "python3",
            "-m",
            "sglang.launch_server",
            "--model-path",
            model,
            "--served-model-name",
            served,
            "--host",
            "0.0.0.0",
            "--port",
            str(SERVE_HTTP_PORT),
        ]
        if gpu_count > 1:
            result.extend(["--tp", str(gpu_count)])
    return [*result, *flags, *companion_flags, *start_args]


def _resolved_variant(
    request: ServeRequest,
    catalogue: CatalogueLookup,
) -> VariantInfo | None:
    model = request.model
    if model is None:
        raise ServeNoServeHistory(request.capability_name)
    try:
        info = catalogue.variant(model, request.variant)
    except CatalogueUnknownVariant as exc:
        raise ServeUnknownVariant(model, request.variant) from exc
    if request.variant is not None and info is None:
        raise ServeUnknownVariant(model, request.variant)
    return info


def _canonical_variant_id(request: ServeRequest, info: VariantInfo | None) -> str | None:
    """Return the selected catalogue ID, including an omitted default selection."""
    if request.variant is not None or info is None:
        return request.variant
    return info.variant_id


def _companion_flags(
    engine: Engine,
    companions: Sequence[CompanionInfo],
) -> tuple[str, ...]:
    result: list[str] = []
    for companion in companions:
        if engine == "sglang":
            raise ValueError(f"unsupported companion flags for {engine}/{companion.kind}")
        if engine == "vllm":
            if (
                companion.kind not in ("mtp", "draft")
                or len(companion.flags) != 2
                or companion.flags[0] != "--speculative-config"
            ):
                raise ValueError(f"unsupported companion flags for {engine}/{companion.kind}")
            try:
                speculative_config = json.loads(companion.flags[1])
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"unsupported companion flags for {engine}/{companion.kind}"
                ) from exc
            if not isinstance(speculative_config, dict):
                raise ValueError(f"unsupported companion flags for {engine}/{companion.kind}")
        elif companion.kind == "mmproj":
            if companion.flags != ("--mmproj", companion.file):
                raise ValueError(f"unsupported companion flags for {engine}/{companion.kind}")
        elif companion.kind in ("mtp", "draft"):
            if companion.flags != (
                "--spec-type",
                "draft-mtp",
                "--spec-draft-n-max",
                "4",
            ):
                raise ValueError(f"unsupported companion flags for {engine}/{companion.kind}")
        else:
            raise ValueError(f"unsupported companion flags for {engine}/{companion.kind}")
        result.extend(companion.flags)
    return tuple(result)


def _cache_env(engine: Engine, *, has_volume: bool) -> dict[str, str]:
    if not has_volume:
        return {}
    if engine in {"vllm", "sglang"}:
        return {"HF_HOME": "/workspace/hf", "HF_HUB_CACHE": "/workspace/hf/hub"}
    return {"LLAMA_CACHE": "/workspace/llama-cache"}


def _startup_timeout(info: VariantInfo | None) -> int:
    if info is None:
        return DEFAULT_STARTUP_TIMEOUT_S
    startup_min = info.startup_min if info.startup_min is not None else 30
    return max(900, startup_min * 60)


def enforce_ttl_above_startup(ttl_minutes: int, info: VariantInfo | None) -> None:
    """Refuse a lease TTL at or within the model's startup budget; it would expire unused."""
    startup_s = _startup_timeout(info)
    if ttl_minutes * 60 <= startup_s:
        raise ServeTtlBelowStartup(
            f"the model can take {math.ceil(startup_s / 60)} min to start; "
            "ttl_minutes must exceed it"
        )


def _container_disk_gb(info: VariantInfo | None) -> int:
    if info is None:
        return 50
    return info.container_disk_gb if info.container_disk_gb is not None else 80


def _plan_gpu_class(gpu_class: str) -> str:
    """Resolve a canonical GPU name, including the documented RTX_#### plan shorthand."""
    try:
        return validate_canonical_gpu_name(gpu_class)
    except NonCanonicalGPUNameError:
        prefix = "RTX_"
        candidate = (
            f"NVIDIA GeForce RTX {gpu_class.removeprefix(prefix)}"
            if gpu_class.startswith(prefix)
            else ""
        )
        if candidate in GPU_VRAM_GB:
            return candidate
        raise


def _selected_fit(variant: Variant, *, vram_gb: int | None, gpu_count: int) -> FitVerdict:
    required = variant.min_vram_gb
    if required == "unverified" or vram_gb is None:
        return "no"
    headroom_gb = gpu_count * vram_gb - required
    if headroom_gb < 0:
        return "no"
    if gpu_count > 1:
        return "tp"
    if 0 < headroom_gb * 10 < vram_gb:
        return "tight"
    return "fits"


async def plan_catalogue_model(
    request: ServePlanRequest,
    *,
    settings: PitwallSettings,
    catalogue: PlanCatalogue,
) -> ServePlanResult:
    """Build a catalogue launch plan without opening or mutating the registry."""
    normalized_gpu_class = _plan_gpu_class(request.gpu_class)
    variant = catalogue.dossier_variant(request.model, request.variant)
    info = _resolved_variant(
        ServeRequest(
            capability_name="plan-only",
            model=request.model,
            gpu_class=normalized_gpu_class,
            gpu_count=request.gpu_count,
            engine=request.engine,
            variant=request.variant,
            ttl_minutes=request.ttl_minutes,
            image=request.image,
            served_model_name=request.served_model_name,
            network_volume_id=request.network_volume_id,
            env=request.env,
            start_args=request.start_args,
        ),
        catalogue,
    )
    assert info is not None
    if not info.openai_chat:
        raise ServeUnknownVariant(request.model, request.variant)
    engine: Engine = request.engine or info.engine
    try:
        companion_flags = _companion_flags(engine, info.companions)
    except ValueError as exc:
        raise ServeUnknownVariant(request.model, request.variant) from exc
    served_model_id = request.served_model_name or info.served_model_name or request.model
    image = request.image or info.image
    argv = launch_shape(
        engine,
        model=request.model,
        served=served_model_id,
        gpu_count=request.gpu_count,
        repo=info.repo,
        file=info.file,
        flags=info.flags,
        companion_flags=companion_flags,
        start_args=request.start_args,
    )
    _reject_launch_only_env(info.env, source="catalogue env")
    _reject_launch_only_env(request.env, source="request env")
    has_volume = bool(request.network_volume_id or settings.runpod_network_volume_id)
    snapshot = await load_gpu_price_snapshot(cloud="secure", settings=settings)
    gpu_type = next(
        (item for item in snapshot.gpu_types if item.id == normalized_gpu_class),
        None,
    )
    vram_gb = (
        gpu_type.memory_in_gb if gpu_type is not None else GPU_VRAM_GB.get(normalized_gpu_class)
    )
    price_per_hour = gpu_type.secure_price if gpu_type is not None else None
    estimate = (
        price_per_hour * request.gpu_count * Decimal(request.ttl_minutes) / Decimal(60)
        if price_per_hour is not None
        else None
    )
    return ServePlanResult(
        model_id=served_model_id,
        engine=engine,
        variant=variant.id,
        gpu_class=normalized_gpu_class,
        gpu_count=request.gpu_count,
        image=image,
        argv=argv,
        volume_cache_env=_cache_env(engine, has_volume=has_volume),
        fit=_selected_fit(variant, vram_gb=vram_gb, gpu_count=request.gpu_count),
        startup_timeout_s=_startup_timeout(info),
        cost_estimate_usd=str(estimate) if estimate is not None else None,
        price_source=snapshot.source,
    )


def _proxy_base_url(base_url: str, capability_name: str) -> str:
    return f"{base_url.rstrip('/')}/v1/openai/{capability_name}/v1"


def _optional_config_string(config: Mapping[str, Any], key: str) -> str | None:
    value = config.get(key)
    return value if isinstance(value, str) and value else None


def _history_gpu_class(config: Mapping[str, Any]) -> str | None:
    gpu_types = _history_gpu_types(config)
    if not gpu_types:
        return None
    return gpu_types[0]


def _history_gpu_types(config: Mapping[str, Any]) -> list[str]:
    value = config.get("gpu_types")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _history_docker_start_cmd(config: Mapping[str, Any]) -> list[str] | None:
    value = config.get("docker_start_cmd")
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(item, str) and item for item in value):
        return None
    return list(value)


def _history_ttl_minutes(config: Mapping[str, Any]) -> int | None:
    raw = config.get("lease_ttl_ms")
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return None
    minutes, remainder = divmod(raw, 60_000)
    return minutes if remainder == 0 and 1 <= minutes <= 10_080 else None


def _restore_serve_history(
    request: ServeRequest,
    provider: Provider | None,
) -> ServeRequest:
    if request.model is not None and request.gpu_class is not None:
        return request
    if provider is None:
        raise ServeNoServeHistory(request.capability_name)
    config = provider.config
    model = request.model or _optional_config_string(config, "model")
    gpu_class = request.gpu_class or _history_gpu_class(config)
    if model is None or gpu_class is None:
        raise ServeNoServeHistory(request.capability_name)

    restored: dict[str, Any] = request.model_dump()
    restored.update(model=model, gpu_class=gpu_class)
    mappings = {
        "variant": "variant",
        "engine": "engine",
        "image": "image_ref",
        "served_model_name": "served_model_name",
        "datacenter": "data_center_id",
        "network_volume_id": "network_volume_id",
        "container_disk_gb": "container_disk_gb",
        "idle_timeout_min": "idle_timeout_min",
        "max_usd_per_hour": "max_usd_per_hour",
    }
    for field, key in mappings.items():
        if field not in request.model_fields_set and key in config:
            restored[field] = config[key]
    if "gpu_count" not in request.model_fields_set and "gpu_count" in config:
        restored["gpu_count"] = config["gpu_count"]
    if (
        "renewal_policy" not in request.model_fields_set
        and "idle_timeout_min" not in request.model_fields_set
        and "renewal_policy" in config
    ):
        restored["renewal_policy"] = config["renewal_policy"]
    if "ttl_minutes" not in request.model_fields_set:
        history_ttl = _history_ttl_minutes(config)
        if history_ttl is not None:
            restored["ttl_minutes"] = history_ttl
    if "env" not in request.model_fields_set:
        history_env = config.get("env_vars")
        if isinstance(history_env, dict):
            restored["env"] = history_env
    if "start_args" not in request.model_fields_set:
        history_start_args = config.get("start_args")
        if isinstance(history_start_args, list):
            restored["start_args"] = history_start_args
    return ServeRequest.model_validate(restored)


def _existing_rate(config: Mapping[str, Any]) -> Decimal | None:
    cost = config.get("cost")
    if not isinstance(cost, Mapping):
        return None
    value = cost.get("per_second_active")
    if value is None or isinstance(value, bool):
        return None
    try:
        rate = Decimal(str(value))
    except ValueError, TypeError, ArithmeticError:
        return None
    return rate if rate.is_finite() and rate >= 0 else None


def _rate_from_price(hourly_usd: Decimal) -> Decimal:
    """Convert an hourly catalogue price into a per-second lease rate.

    Rounded up at six decimal places so the recorded rate never bills less than the
    pod actually costs.
    """
    return (hourly_usd / Decimal(3600)).quantize(Decimal("0.000001"), rounding=ROUND_UP)


RUNPOD_CUDA_VERSIONS: tuple[str, ...] = (
    "12.0",
    "12.1",
    "12.2",
    "12.3",
    "12.4",
    "12.5",
    "12.6",
    "12.7",
    "12.8",
    "12.9",
    "13.0",
    "13.1",
    "13.2",
)
"""Driver CUDA versions RunPod has offered (live gpuTypes, 2026-08-30 through 2026-09-21).

Used only when a live offer is unavailable, so a floor never collapses to one exact version.
"""


def cuda_allow_list(min_cuda: str | None, offered: Sequence[str] | None) -> list[str] | None:
    """The ``allowedCudaVersions`` set for a variant floor; RunPod treats it as exact.

    Returns None when the variant has no floor. Uses the live offer when known and every
    known RunPod version otherwise. Raises before launch when nothing meets the floor.
    """
    if min_cuda is None:
        return None
    candidates = RUNPOD_CUDA_VERSIONS if offered is None else tuple(offered)
    allowed = _allowed_cuda_versions(min_cuda, candidates)
    if not allowed:
        raise ServeTemplateInvalid(f"no CUDA version at or above {min_cuda} is offered")
    return allowed


def _allowed_cuda_versions(min_cuda: str | None, offered: Sequence[str]) -> list[str] | None:
    """Return the offered driver versions that satisfy ``min_cuda``.

    ``offered`` comes from the live GPU catalogue, which reports exactly which driver
    versions a GPU type is available with. An empty result is deliberate: callers
    must reject a known incompatible catalogue rather than drop the CUDA constraint.
    """
    if not min_cuda:
        return None
    floor = tuple(int(part) for part in min_cuda.split("."))
    satisfying = [
        version for version in offered if tuple(int(part) for part in version.split(".")) >= floor
    ]
    return satisfying


def _market_cuda_versions(market: object, gpu_type_id: str) -> tuple[str, ...] | None:
    """Read available CUDA versions for one GPU from the REST market read.

    The GraphQL price rows do not carry CUDA availability.  Keep this lookup
    explicitly tied to the REST v2 catalogue so an unavailable CUDA floor is
    never silently converted into an unconstrained pod request.
    """
    for gpu in getattr(market, "gpus", ()):
        if getattr(gpu, "gpu_type_id", None) != gpu_type_id:
            continue
        rest = getattr(gpu, "rest_v2", None)
        if rest is None:
            return None
        versions = getattr(rest, "cuda_versions", None)
        if versions is None:
            return None
        return tuple(version for version, available in versions if available)
    return None


async def _live_cuda_versions(
    market_service: Any,
    settings: PitwallSettings,
    gpu_type_id: str,
) -> tuple[str, ...] | None:
    """Read REST CUDA availability, owning a short-lived service when needed."""
    owned_service = market_service is None
    if owned_service:
        from pitwall.runpod_market import build_configured_runpod_market_service

        market_service = build_configured_runpod_market_service(settings)
    try:
        market = await market_service.read()
    except Exception as exc:  # reason: any read failure falls back to the safe floor
        log.warning("CUDA availability read failed: error_type=%s", type(exc).__name__)
        return None
    finally:
        if owned_service:
            await market_service.aclose()
    return _market_cuda_versions(market, gpu_type_id)


def _reject_launch_only_env(env: Mapping[str, str], *, source: str) -> None:
    launch_only = {key for key in ("HF_TOKEN", ENDPOINT_KEY_ENV) if key in env}
    if launch_only:
        names = ", ".join(sorted(launch_only))
        raise ServeTemplateInvalid(f"{source} cannot contain launch-only {names}")


def _provider_config(
    request: ServeRequest,
    *,
    info: VariantInfo | None,
    engine: Engine,
    image: str,
    docker_start_cmd: Sequence[str],
    gpu_types: Sequence[str],
    restoring_history: bool,
    served_model_id: str,
    existing: Mapping[str, Any],
    settings: PitwallSettings,
    cloud_type: str | None,
    price: Decimal | None,
    offered_cuda_versions: Sequence[str] | None = None,
    refresh_rate: bool = False,
    docker_entrypoint: Sequence[str] = ("sh", "-c"),
) -> dict[str, Any]:
    catalogue_env = dict(info.env) if info else {}
    _reject_launch_only_env(catalogue_env, source="catalogue env")
    _reject_launch_only_env(request.env, source="request env")

    rate = request.rate_per_second
    if rate is None and not refresh_rate:
        rate = _existing_rate(existing)
    if rate is None and price is not None:
        rate = _rate_from_price(price)
        log.info("defaulted rate_per_second=%s from the live price %s/hr", rate, price)
    if rate is None:
        raise ServeRateRequired(
            "rate_per_second is required and no live price was available for this GPU class"
        )

    network_volume_id = request.network_volume_id or _optional_config_string(
        existing, "network_volume_id"
    )
    if network_volume_id is None:
        network_volume_id = settings.runpod_network_volume_id or None
    data_center_id = request.datacenter or settings.runpod_data_center_id or None
    persisted_env = (
        dict(request.env)
        if restoring_history
        else {
            **_cache_env(engine, has_volume=network_volume_id is not None),
            **catalogue_env,
            **request.env,
        }
    )
    assert request.renewal_policy is not None

    config: dict[str, Any] = {}
    for key in ("active_pod_id", "active_lease_id", "warm_cache"):
        if key in existing:
            config[key] = existing[key]
    config.update(
        {
            "engine": engine,
            "image_ref": image,
            "gpu_types": list(gpu_types),
            "gpu_count": request.gpu_count,
            "container_disk_gb": request.container_disk_gb or _container_disk_gb(info),
            "startup_timeout_s": (
                existing.get("startup_timeout_s", DEFAULT_STARTUP_TIMEOUT_S)
                if restoring_history
                else _startup_timeout(info)
            ),
            "ports": {"http": [SERVE_HTTP_PORT]},
            "lease_ttl_ms": request.ttl_minutes * 60_000,
            "idle_timeout_min": request.idle_timeout_min,
            "max_usd_per_hour": (
                str(request.max_usd_per_hour) if request.max_usd_per_hour is not None else None
            ),
            "renewal_policy": request.renewal_policy.value,
            "env_vars": persisted_env,
            "docker_start_cmd": list(docker_start_cmd),
            "docker_entrypoint": list(docker_entrypoint),
            "endpoint_auth": True,
            "api_key_env": ENDPOINT_KEY_ENV,
            "start_args": list(request.start_args),
            "readiness_path": (
                existing.get("readiness_path", ENGINE_READINESS_PATH[engine])
                if restoring_history
                else ENGINE_READINESS_PATH[engine]
            ),
            "openai_proxy_port": SERVE_HTTP_PORT,
            "supports_streaming": True,
            "cost": {"per_second_active": str(rate)},
        }
    )
    allowed_cuda_versions = cuda_allow_list(
        info.min_cuda if info is not None else None, offered_cuda_versions
    )
    optional_values = {
        "model": request.model,
        "served_model_name": served_model_id,
        "variant": request.variant,
        "template_id": request.template_id,
        "data_center_id": data_center_id,
        "network_volume_id": network_volume_id,
        "allowed_cuda_versions": allowed_cuda_versions,
    }
    config.update({key: value for key, value in optional_values.items() if value is not None})
    validate_provider_registration_config(
        provider_type=ProviderType.POD_LEASE,
        endpoint_id=None,
        cloud_type=cloud_type,
        config=config,
    )
    return config


def _launch_only_env(
    request: ServeRequest,
    info: VariantInfo | None,
    settings: PitwallSettings,
) -> dict[str, str]:
    if not (request.gated or (info is not None and info.gated)):
        return (
            {}
            if request.dry_run or not settings.pitwall_endpoint_key.strip()
            else {ENDPOINT_KEY_ENV: settings.pitwall_endpoint_key}
        )
    if not settings.pitwall_hf_token:
        raise ServeLaunchFailed("gated model launch requires PITWALL_HF_TOKEN")
    launch_env = {"HF_TOKEN": settings.pitwall_hf_token}
    if not request.dry_run and settings.pitwall_endpoint_key.strip():
        launch_env[ENDPOINT_KEY_ENV] = settings.pitwall_endpoint_key
    return launch_env


async def verify_served_model(
    models_url: str,
    served_model_id: str,
    *,
    headers: Mapping[str, str] | None = None,
    total_timeout_s: float | None = None,
) -> list[str]:
    """Poll a pod's model inventory within the verification deadline.

    The deadline is ``VERIFY_TOTAL_TIMEOUT_S`` unless ``total_timeout_s`` shortens it (a
    replay checks once instead of waiting out a first serve's readiness).
    """

    total = VERIFY_TOTAL_TIMEOUT_S if total_timeout_s is None else total_timeout_s
    deadline = asyncio.get_running_loop().time() + total
    observed: list[str] = []
    async with httpx.AsyncClient() as client:
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ServeVerificationFailed(served_model_id, observed)
            try:
                response = await client.get(
                    models_url,
                    headers=headers,
                    timeout=min(VERIFY_REQUEST_TIMEOUT_S, remaining),
                )
            except httpx.HTTPError:
                response = None
            if response is not None and response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError:
                    payload = {}
                data = payload.get("data") if isinstance(payload, dict) else None
                observed = [
                    item["id"]
                    for item in data or []
                    if isinstance(item, dict) and isinstance(item.get("id"), str)
                ]
                if served_model_id in observed:
                    return observed
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise ServeVerificationFailed(served_model_id, observed)
            await asyncio.sleep(min(VERIFY_RETRY_INTERVAL_S, remaining))


def _result(
    request: ServeRequest,
    *,
    served_model_id: str,
    provider: Provider,
    engine: Engine,
    variant: str | None,
    gpu_count: int,
    base_url: str,
    lease_id: str | None,
    expires_at: str | None,
    workload_id: str | None,
    template_id: str | None,
    dry_run: bool,
    created: bool,
    cost_estimate_usd: str | None,
    price_freshness: GpuPriceFreshness | None = None,
) -> ServeResult:
    return ServeResult(
        capability=request.capability_name,
        lease_id=lease_id,
        expires_at=expires_at,
        model_id=served_model_id,
        proxy_base_url=_proxy_base_url(base_url, request.capability_name),
        engine=engine,
        variant=variant,
        gpu_count=gpu_count,
        workload_id=workload_id,
        template_id=template_id,
        provider_id=provider.id,
        dry_run=dry_run,
        created=created,
        cost_estimate_usd=cost_estimate_usd,
        price_age_seconds=price_freshness.age_seconds if price_freshness is not None else None,
        price_source=price_freshness.source if price_freshness is not None else None,
        price_stale=price_freshness.stale if price_freshness is not None else None,
    )


async def _publish_ready(
    *,
    pool: Any,
    redis: Any,
    result: ServeResult,
    lease: Lease,
    capability: Capability,
) -> ServeResult:
    event = build_lease_ready_event(
        lease=lease,
        capability=capability,
        served_model_id=result.model_id,
        variant=result.variant,
        proxy_base_url=result.proxy_base_url,
        created=result.created,
    )
    await publish_lease_event(pool, redis, event)
    return result


def _replay_selection(provider: Provider) -> tuple[Engine, str | None, int]:
    raw_engine = provider.config.get("engine")
    engine: Engine = raw_engine if raw_engine in ("vllm", "llama.cpp", "sglang") else "vllm"
    raw_variant = provider.config.get("variant")
    variant = raw_variant if isinstance(raw_variant, str) else None
    raw_gpu_count = provider.config.get("gpu_count")
    gpu_count = (
        raw_gpu_count
        if isinstance(raw_gpu_count, int) and not isinstance(raw_gpu_count, bool)
        else 1
    )
    return engine, variant, gpu_count


ToolCallingState = Literal["enabled", "disabled", "unknown"]


async def probe_tool_calling(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    headers: Mapping[str, str],
    model_id: str,
    prompt: str,
    max_tokens: int,
) -> ToolCallingState:
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "pitwall_probe",
                    "description": "Return readiness",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "tool_choice": "auto",
    }
    try:
        response = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
        )
    except httpx.HTTPError:
        return "unknown"
    if 200 <= response.status_code < 300:
        return "enabled"
    if response.status_code in {400, 422}:
        return "disabled"
    return "unknown"


@dataclass(frozen=True)
class WarmOutcome:
    resident_before: bool | None
    ready: bool
    elapsed_s: float
    observation: ReadinessObservation


def _provider_base_url(provider: Provider) -> str:
    configured = provider.config.get("base_url")
    if isinstance(configured, str) and configured:
        return configured.rstrip("/")
    return public_endpoint_url(provider).rstrip("/")


def _provider_headers(provider: Provider) -> dict[str, str]:
    env_name = provider.config.get("api_key_env")
    if not isinstance(env_name, str) or not env_name:
        return {}
    value = os.environ.get(env_name)
    return {"Authorization": f"Bearer {value}"} if value else {}


async def warm_self_hosted(
    *,
    provider: Provider,
    profile: SelfHostedProfile,
    capability: Capability,
    client: httpx.AsyncClient,
    settings: PitwallSettings,
) -> WarmOutcome:
    del settings
    model_id = capability.served_model_id
    if model_id is None:
        raise ServeWarmFailed(provider_id=provider.id, model_id="", state="absent")
    base_url = _provider_base_url(provider)
    headers = _provider_headers(provider)
    oracle = oracle_for(profile)
    before = await oracle.observe(
        client=client,
        base_url=base_url,
        headers=headers,
        model_id=model_id,
    )
    resident_before = (
        None if profile.readiness.kind == "http-health" else before.models.get(model_id) == "ready"
    )
    started = asyncio.get_running_loop().time()
    try:
        async with asyncio.timeout(profile.cold_start_timeout_s):
            if not resident_before and profile.warmup is not None:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json={
                        "model": model_id,
                        "messages": [{"role": "user", "content": profile.warmup.prompt}],
                        "max_tokens": profile.warmup.max_tokens,
                    },
                )
                response.raise_for_status()
            elif not resident_before:
                await client.get(f"{base_url}/models", headers=headers)
            observation = await oracle.observe(
                client=client,
                base_url=base_url,
                headers=headers,
                model_id=model_id,
            )
    except (TimeoutError, httpx.HTTPError) as exc:
        try:
            async with asyncio.timeout(WARM_DIAGNOSTIC_TIMEOUT_S):
                observation = await oracle.observe(
                    client=client,
                    base_url=base_url,
                    headers=headers,
                    model_id=model_id,
                )
        except TimeoutError, httpx.HTTPError:
            observation = ReadinessObservation(
                state="unreachable",
                models={},
                observed_at=dt.datetime.now(dt.UTC),
                latency_ms=0,
            )
        raise ServeWarmFailed(
            provider_id=provider.id,
            model_id=model_id,
            state=observation.state,
        ) from exc
    if observation.state != "ready":
        raise ServeWarmFailed(
            provider_id=provider.id,
            model_id=model_id,
            state=observation.state,
        )
    return WarmOutcome(
        resident_before=resident_before,
        ready=True,
        elapsed_s=asyncio.get_running_loop().time() - started,
        observation=observation,
    )


def _is_runpod_query_error(exc: Exception) -> bool:
    """Return whether an untyped RunPod SDK exception is a GraphQL query error."""
    import runpod.error as rp_error  # type: ignore[import-untyped]  # noqa: PLC0415  # reason: SDK imported lazily; no stubs

    return isinstance(exc, rp_error.QueryError)


@overload
async def serve_model(
    pool: Any,
    request: ServeRequest,
    *,
    base_url: str,
    settings: PitwallSettings,
    catalogue: CatalogueLookup | None = None,
    redis: Any = None,
    market_service: Any = None,
    http_client: httpx.AsyncClient | None = None,
    warm_only: Literal[False] = False,
) -> ServeResult: ...


@overload
async def serve_model(
    pool: Any,
    request: ServeRequest,
    *,
    base_url: str,
    settings: PitwallSettings,
    catalogue: CatalogueLookup | None = None,
    redis: Any = None,
    market_service: Any = None,
    http_client: httpx.AsyncClient | None = None,
    warm_only: Literal[True],
) -> WarmResult: ...


async def serve_model(
    pool: Any,
    request: ServeRequest,
    *,
    base_url: str,
    settings: PitwallSettings,
    catalogue: CatalogueLookup | None = None,
    redis: Any = None,
    http_client: httpx.AsyncClient | None = None,
    market_service: Any = None,
    warm_only: bool = False,
) -> ServeResult | WarmResult:
    """Get or create registry state, launch a pod, and verify its served model."""

    request = _inspect_request(request)
    # The caller's request, before history restore, is what a repeated key must match.
    fingerprint = (
        serve_request_fingerprint(request)
        if request.idempotency_key is not None and not request.dry_run
        else None
    )
    explicit_fields = request.model_fields_set
    provider_name = f"{SERVE_PROVIDER_PREFIX}{request.capability_name}"
    history_required = request.model is None or request.gpu_class is None
    provider_repo = ProviderRepository(pool)
    capability_repo = CapabilityRepository(pool)
    lease_repo = LeaseRepository(pool)
    provider = (
        await provider_repo.get_by_name(provider_name)
        if history_required and hasattr(provider_repo, "get_by_name")
        else None
    )
    capability = (
        await capability_repo.get_by_name(request.capability_name) if history_required else None
    )
    if capability is not None and history_required and hasattr(provider_repo, "list"):
        warmed = await _serve_self_hosted(
            pool,
            redis,
            provider_repo,
            capability,
            base_url=base_url,
            settings=settings,
            http_client=http_client,
        )
        if warmed is not None:
            return warmed
    request = _restore_serve_history(request, provider)
    started_at = asyncio.get_running_loop().time()
    plan = _resolve_catalogue_plan(
        request,
        provider,
        capability,
        catalogue=catalogue,
        history_required=history_required,
        explicit_fields=explicit_fields,
        settings=settings,
    )
    request = plan.request

    if not history_required:
        provider = await provider_repo.get_by_name(provider_name)
        capability = await capability_repo.get_by_name(request.capability_name)
    if fingerprint is not None and request.idempotency_key is not None:
        existing = await WorkloadRepository(pool).get_by_idempotency_key(request.idempotency_key)
        if existing is not None:
            # A repeated key: no registry write, no price step, no launch.
            keyed_lease = await replay_idempotent_launch(
                existing,
                lease_repo=lease_repo,
                idempotency_key=request.idempotency_key,
                request_fingerprint=fingerprint,
            )
            return await _replay_keyed_lease(
                pool,
                redis,
                request,
                plan,
                provider=provider,
                capability=capability,
                lease=keyed_lease,
                base_url=base_url,
                settings=settings,
            )
    active_lease = (
        await lease_repo.latest_active_for_provider(provider.id) if provider is not None else None
    )
    if active_lease is not None and provider is not None and capability is not None:
        return await _replay_active_lease(
            pool,
            redis,
            request,
            plan,
            provider=provider,
            capability=capability,
            lease=active_lease,
            base_url=base_url,
            settings=settings,
        )

    capability, provider, price_freshness = await _prepare_launch(
        request,
        plan,
        provider_repo=provider_repo,
        capability_repo=capability_repo,
        provider=provider,
        capability=capability,
        provider_name=provider_name,
        explicit_fields=explicit_fields,
        market_service=market_service,
        settings=settings,
    )
    launch = await _launch(
        pool, request, plan, capability, provider, settings, request_fingerprint=fingerprint
    )
    if launch.get("replayed") is True:
        # Lost the admission race to a same-key serve: report its lease, never verify or
        # tear it down from here.
        replayed_lease = await lease_repo.get(_launched_lease_id(launch))
        if replayed_lease is None:
            raise ServeLaunchFailed("the replayed lease was not found")
        return await _replay_keyed_lease(
            pool,
            redis,
            request,
            plan,
            provider=provider,
            capability=capability,
            lease=replayed_lease,
            base_url=base_url,
            settings=settings,
        )

    template_id = _launch_string(launch, "template_id")
    workload_id = _launch_string(launch, "workload_id")
    if request.dry_run:
        return _dry_run_result(
            request,
            plan,
            capability,
            provider,
            base_url=base_url,
            template_id=template_id,
            workload_id=workload_id,
            price_freshness=price_freshness,
            warm_only=warm_only,
        )
    raw_lease_id = _launched_lease_id(launch)
    return await _complete_launch(
        pool,
        redis,
        request,
        plan,
        provider_repo=provider_repo,
        lease_repo=lease_repo,
        provider=provider,
        capability=capability,
        lease_id=raw_lease_id,
        started_at=started_at,
        base_url=base_url,
        settings=settings,
        warm_only=warm_only,
        workload_id=workload_id,
        template_id=template_id,
        price_freshness=price_freshness,
    )


def _launch_string(launch: Mapping[str, Any], key: str) -> str | None:
    value = launch.get(key)
    return value if isinstance(value, str) else None


def _launched_lease_id(launch: Mapping[str, Any]) -> str:
    """Return the launched lease id, refusing fallbacks and missing ids."""
    if launch.get("provider_fallback") is True:
        reason = launch.get("provider_fallback_reason")
        raise ServeLaunchFailed(
            reason if isinstance(reason, str) else "provider fallback requested"
        )
    lease_id = launch.get("lease_id")
    if not isinstance(lease_id, str) or not lease_id:
        raise ServeLaunchFailed("launch did not return a lease_id")
    return lease_id


async def _prepare_launch(
    request: ServeRequest,
    plan: _ServePlan,
    *,
    provider_repo: Any,
    capability_repo: Any,
    provider: Provider | None,
    capability: Capability | None,
    provider_name: str,
    explicit_fields: set[str],
    market_service: Any,
    settings: PitwallSettings,
) -> tuple[Capability, Provider, GpuPriceFreshness | None]:
    """Price the launch, build the provider config, and persist the registry rows."""
    existing_config = provider.config if provider is not None else {}
    refresh_rate = request.rate_per_second is None and "gpu_class" in explicit_fields
    price, price_freshness = await _price_step(
        request,
        plan,
        existing_config=existing_config,
        refresh_rate=refresh_rate,
        settings=settings,
    )
    config = await _build_config(
        request,
        plan,
        provider=provider,
        existing_config=existing_config,
        price=price,
        refresh_rate=refresh_rate,
        explicit_fields=explicit_fields,
        market_service=market_service,
        settings=settings,
    )
    capability, provider = await _persist_registry(
        request,
        plan,
        provider_repo=provider_repo,
        capability_repo=capability_repo,
        provider=provider,
        capability=capability,
        provider_name=provider_name,
        config=config,
    )
    return capability, provider, price_freshness


async def _complete_launch(
    pool: Any,
    redis: Any,
    request: ServeRequest,
    plan: _ServePlan,
    *,
    provider_repo: Any,
    lease_repo: Any,
    provider: Provider,
    capability: Capability,
    lease_id: str,
    started_at: float,
    base_url: str,
    settings: PitwallSettings,
    warm_only: bool,
    workload_id: str | None,
    template_id: str | None,
    price_freshness: GpuPriceFreshness | None,
) -> ServeResult | WarmResult:
    """Verify and publish a launched lease, tearing it down on any failure."""
    async with _PostLaunchGuard(lease_id, pool) as guard:
        guard.step = "lease_not_persisted"
        lease = await lease_repo.get(lease_id)
        if lease is None:
            raise ServeLaunchFailed(f"launched lease {lease_id!r} was not persisted")
        provider = await _verify_launched(
            provider_repo,
            request,
            plan,
            provider,
            lease,
            settings=settings,
            warm_only=warm_only,
            guard=guard,
        )
        if warm_only:
            return await _finish_warm(
                pool,
                request,
                plan,
                provider,
                lease,
                started_at=started_at,
                guard=guard,
            )
        guard.step = "publish"
        return await _publish_ready(
            pool=pool,
            redis=redis,
            result=_result(
                request,
                served_model_id=plan.served_model_id,
                provider=provider,
                engine=plan.engine,
                variant=request.variant,
                gpu_count=request.gpu_count,
                base_url=base_url,
                lease_id=lease.id,
                expires_at=lease.expires_at.isoformat(),
                workload_id=workload_id,
                template_id=template_id,
                dry_run=False,
                created=True,
                cost_estimate_usd=None,
                price_freshness=price_freshness,
            ),
            lease=lease,
            capability=capability,
        )


def _inspect_request(request: ServeRequest) -> ServeRequest:
    """Run the pre-spend guardrail over the request payload."""
    request_payload = request.model_dump(mode="json", exclude_unset=True)
    guardrail = get_pre_spend_inspection_service().inspect(
        request_payload,
        validate_redacted=ServeRequest.model_validate,
    )
    if guardrail.decision == PreSpendDecision.BLOCK:
        raise PreSpendPayloadRejected(
            decision=guardrail.decision.value,
            findings=[finding.to_dict() for finding in guardrail.findings],
        )
    if guardrail.decision == PreSpendDecision.REDACT:
        return ServeRequest.model_validate(guardrail.redacted_payload)
    return request


async def _record_self_hosted_telemetry(
    provider_repo: Any,
    selected: Provider,
    profile: SelfHostedProfile,
    capability: Capability,
    outcome: WarmOutcome,
    *,
    client: httpx.AsyncClient,
    settings: PitwallSettings,
) -> None:
    """Probe tool calling after a warm and persist best-effort telemetry."""
    if profile.warmup is None:
        return
    try:
        async with asyncio.timeout(settings.pitwall_endpoint_probe_timeout_s):
            tool_calling = await probe_tool_calling(
                client=client,
                base_url=_provider_base_url(selected),
                headers=_provider_headers(selected),
                model_id=capability.served_model_id or "",
                prompt=profile.warmup.prompt,
                max_tokens=profile.warmup.max_tokens,
            )
    except TimeoutError:
        tool_calling = "unknown"
    state = self_hosted_state(selected.config.get("self_hosted_state"))
    state["tool_calling"] = tool_calling
    if outcome.resident_before is False:
        state["cold_start_s"] = {"p50": outcome.elapsed_s, "p95": outcome.elapsed_s}
    try:
        await provider_repo.patch(
            selected.id,
            config={**selected.config, "self_hosted_state": state},
        )
    except Exception:  # reason: warm telemetry is best-effort
        log.warning(
            "self-hosted warm telemetry persistence failed: provider=%s",
            selected.id,
            exc_info=True,
        )


async def _serve_self_hosted(
    pool: Any,
    redis: Any,
    provider_repo: Any,
    capability: Capability,
    *,
    base_url: str,
    settings: PitwallSettings,
    http_client: httpx.AsyncClient | None,
) -> ServeResult | None:
    """Warm a registered self-hosted provider when one backs the capability."""
    candidates = await provider_repo.list(capability_id=capability.id, enabled_only=True)
    selected = next(
        (
            item
            for item in candidates
            if item.provider_type == ProviderType.PUBLIC_ENDPOINT
            and self_hosted_profile(item) is not None
        ),
        None,
    )
    if selected is None:
        return None
    profile = self_hosted_profile(selected)
    assert profile is not None
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient()
    try:
        outcome = await warm_self_hosted(
            provider=selected,
            profile=profile,
            capability=capability,
            client=client,
            settings=settings,
        )
        await _record_self_hosted_telemetry(
            provider_repo,
            selected,
            profile,
            capability,
            outcome,
            client=client,
            settings=settings,
        )
    finally:
        if owns_client:
            await client.aclose()
    model_id = capability.served_model_id or ""
    result = ServeResult(
        capability=capability.name,
        lease_id=None,
        expires_at=None,
        model_id=model_id,
        proxy_base_url=_proxy_base_url(base_url, capability.name),
        engine="vllm",
        variant=None,
        gpu_count=1,
        workload_id=None,
        template_id=None,
        provider_id=selected.id,
        provider_kind="self_hosted",
        dry_run=False,
        created=(None if outcome.resident_before is None else not outcome.resident_before),
        cost_estimate_usd=None,
    )
    event = build_lease_ready_event(
        lease=None,
        capability_name=capability.name,
        served_model_id=model_id,
        variant=None,
        proxy_base_url=result.proxy_base_url,
        created=result.created,
    )
    await publish_lease_event(pool, redis, event)
    return result


@dataclass(frozen=True)
class _ServePlan:
    """Catalogue-resolved launch inputs shared by the serve steps."""

    request: ServeRequest
    model: str
    gpu_class: str
    served_model_id: str
    engine: Engine
    image: str
    info: VariantInfo | None
    docker_start_cmd: list[str]
    docker_entrypoint: list[str]
    launch_only_env: dict[str, str]
    history_required: bool


_COMMAND_OVERRIDES = frozenset(
    {"model", "engine", "variant", "gpu_count", "served_model_name", "start_args"}
)


def _resolve_catalogue_plan(
    request: ServeRequest,
    provider: Provider | None,
    capability: Capability | None,
    *,
    catalogue: CatalogueLookup | None,
    history_required: bool,
    explicit_fields: set[str],
    settings: PitwallSettings,
) -> _ServePlan:
    """Resolve the model, variant, image, and launch command from the catalogue."""
    assert request.model is not None
    assert request.gpu_class is not None
    model = request.model
    try:
        normalized_gpu_class = validate_canonical_gpu_name(request.gpu_class)
    except NonCanonicalGPUNameError as exc:
        raise ServeInvalidGpuClass(exc.gpu_name, exc.suggestions) from exc

    catalogue_lookup = catalogue or NoCatalogue()
    info = None if history_required else _resolved_variant(request, catalogue_lookup)
    request = request.model_copy(update={"variant": _canonical_variant_id(request, info)})
    served_model_id = (
        request.served_model_name
        or (capability.served_model_id if history_required and capability is not None else None)
        or (info.served_model_name if info is not None else None)
        or model
    )
    if info is None and request.image is None:
        raise ServeTemplateInvalid("models without a catalogue dossier require image")
    engine: Engine = request.engine or (info.engine if info is not None else "vllm")
    if info is not None and not info.openai_chat:
        raise ServeUnknownVariant(model, request.variant)
    try:
        resolved_companion_flags = _companion_flags(
            engine,
            info.companions if info is not None else (),
        )
    except ValueError as exc:
        raise ServeUnknownVariant(model, request.variant) from exc
    image = request.image or (info.image if info is not None else None)
    if image is None:
        raise ServeTemplateInvalid("serve requires an image")
    enforce_ttl_above_startup(request.ttl_minutes, info)
    _reject_launch_only_env(info.env if info is not None else {}, source="catalogue env")
    _reject_launch_only_env(request.env, source="request env")
    history_command = (
        _history_docker_start_cmd(provider.config)
        if history_required
        and provider is not None
        and not _COMMAND_OVERRIDES.intersection(explicit_fields)
        else None
    )
    raw_docker_start_cmd = history_command or launch_shape(
        engine,
        model=model,
        served=served_model_id,
        gpu_count=request.gpu_count,
        repo=info.repo if info else model,
        file=info.file if info else None,
        flags=info.flags if info else (),
        companion_flags=resolved_companion_flags,
        start_args=request.start_args,
    )
    docker_start_cmd, docker_entrypoint = _authenticated_start_shape(raw_docker_start_cmd)
    return _ServePlan(
        request=request,
        model=model,
        gpu_class=normalized_gpu_class,
        served_model_id=served_model_id,
        engine=engine,
        image=image,
        info=info,
        docker_start_cmd=docker_start_cmd,
        docker_entrypoint=docker_entrypoint,
        launch_only_env=_launch_only_env(request, info, settings),
        history_required=history_required,
    )


def serve_request_fingerprint(request: ServeRequest) -> str:
    """The digest a repeated serve ``idempotency_key`` must match.

    It covers every field the caller set (model, variant, GPU class and count, engine, image,
    TTL, price caps, env, start args, and so on), excluding only the key and ``dry_run``.
    Request env and start args are persisted in clear in the serve provider's config and may
    not carry launch credentials (``_reject_launch_only_env``, the pre-spend redaction, and
    the start-args validator), so a plain SHA-256 over them exposes nothing new.
    """
    return launch_request_fingerprint(
        {
            "surface": "serve",
            "request": request.model_dump(
                mode="json", exclude_unset=True, exclude={"idempotency_key", "dry_run"}
            ),
        }
    )


#: States a lease passes through before it is ready; a replay waits for the first serve.
_READYING_LEASE_STATES = ACTIVE_LEASE_STATES - {LeaseState.ACTIVE, LeaseState.STOPPING}


async def _replay_keyed_lease(
    pool: Any,
    redis: Any,
    request: ServeRequest,
    plan: _ServePlan,
    *,
    provider: Provider | None,
    capability: Capability | None,
    lease: Lease,
    base_url: str,
    settings: PitwallSettings,
) -> ServeResult:
    """The serve result for the lease a same-key serve launched, or a typed refusal.

    An active lease replays through the verified active-lease replay. A lease still being
    readied (creating, waiting for runtime or probe) is ``mutation_in_progress``: retry the
    same key. A lease that is no longer serving (stopping, stopped, failed, expired) is
    ``lease_state_conflict``: the key is spent, and serving again needs a new key.
    """
    if lease.state in _READYING_LEASE_STATES:
        raise LeaseLaunchInProgress(lease.workload_id, lease_id=lease.id)
    if (
        lease.state == LeaseState.ACTIVE
        and provider is not None
        and capability is not None
        and lease.provider_id == provider.id
    ):
        return await _replay_active_lease(
            pool,
            redis,
            request,
            plan,
            provider=provider,
            capability=capability,
            lease=lease,
            base_url=base_url,
            settings=settings,
        )
    state = lease.state.value if hasattr(lease.state, "value") else str(lease.state)
    raise LeaseStateConflict(lease.id, state, "serve")


async def _replay_active_lease(
    pool: Any,
    redis: Any,
    request: ServeRequest,
    plan: _ServePlan,
    *,
    provider: Provider,
    capability: Capability,
    lease: Lease,
    base_url: str,
    settings: PitwallSettings,
) -> ServeResult:
    """Return the ready result for a lease that is already active and serves the model.

    ``lease.ready`` is published only after ``verify_served_model`` passes. A lease that is
    not active yet, or whose pod does not list the model within one verification request, is
    ``mutation_in_progress`` (the serve that launched it is still readying or verifying it);
    an active lease that does not verify carries a remedy naming ``pitwall_stop_lease``
    (``LeaseNotServing``). The replay publishes ``lease.ready`` only after the verify passes;
    a refused replay publishes nothing and never tears the lease down.
    """
    if capability.served_model_id != plan.served_model_id:
        raise ServeConflict(request.capability_name, capability.served_model_id)
    if settings.pitwall_endpoint_key.strip() and provider.config.get("endpoint_auth") is not True:
        raise ServeLaunchFailed(
            "active registry lease predates endpoint authentication; stop and relaunch it"
        )
    if lease.state != LeaseState.ACTIVE:
        raise LeaseLaunchInProgress(lease.workload_id, lease_id=lease.id)
    armed_provider = provider.model_copy(
        update={
            "config": {
                **provider.config,
                "active_pod_id": lease.runpod_pod_id or lease.external_resource_id,
                "active_lease_id": lease.id,
            }
        }
    )
    pod_base_url = pod_lease_base_url(armed_provider)
    if pod_base_url is None:
        raise ServeLaunchFailed("active lease has no valid pod proxy URL")
    try:
        await verify_served_model(
            f"{pod_base_url}/models",
            plan.served_model_id,
            headers={"Authorization": f"Bearer {settings.pitwall_endpoint_key}"},
            total_timeout_s=VERIFY_REQUEST_TIMEOUT_S,
        )
    except ServeVerificationFailed as exc:
        raise LeaseNotServing(lease.workload_id, lease_id=lease.id) from exc
    replay_engine, replay_variant, replay_gpu_count = _replay_selection(provider)
    return await _publish_ready(
        pool=pool,
        redis=redis,
        result=_result(
            request,
            served_model_id=plan.served_model_id,
            provider=provider,
            engine=replay_engine,
            variant=replay_variant,
            gpu_count=replay_gpu_count,
            base_url=base_url,
            lease_id=lease.id,
            expires_at=lease.expires_at.isoformat(),
            workload_id=None,
            template_id=_optional_config_string(provider.config, "template_id"),
            dry_run=False,
            created=False,
            cost_estimate_usd=None,
            price_freshness=None,
        ),
        lease=lease,
        capability=capability,
    )


async def _price_step(
    request: ServeRequest,
    plan: _ServePlan,
    *,
    existing_config: Mapping[str, Any],
    refresh_rate: bool,
    settings: PitwallSettings,
) -> tuple[Decimal | None, GpuPriceFreshness | None]:
    """Load the GPU price snapshot when a default rate, freshness, or cap needs it."""
    needs_default_rate = request.rate_per_second is None and (
        refresh_rate or _existing_rate(existing_config) is None
    )
    if not (
        needs_default_rate
        or settings.pitwall_price_max_age_s is not None
        or request.max_usd_per_hour is not None
    ):
        return None, None
    snapshot = await load_gpu_price_snapshot(cloud="secure", settings=settings)
    price_freshness = gpu_price_freshness(snapshot, max_age_s=settings.pitwall_price_max_age_s)
    if price_freshness.stale and not request.dry_run:
        raise ServeStalePrice(
            "GPU price snapshot is stale; refresh pricing or use a newer snapshot",
            age_seconds=price_freshness.age_seconds,
            source=price_freshness.source,
            max_age_s=settings.pitwall_price_max_age_s,
        )
    enforce_price_cap(
        gpu_class=plan.gpu_class,
        max_usd_per_hour=request.max_usd_per_hour,
        snapshot=snapshot,
        freshness=price_freshness,
    )
    priced = next((item for item in snapshot.gpu_types if item.id == plan.gpu_class), None)
    price = priced.secure_price if snapshot.source == "live" and priced is not None else None
    return price, price_freshness


async def _build_config(
    request: ServeRequest,
    plan: _ServePlan,
    *,
    provider: Provider | None,
    existing_config: Mapping[str, Any],
    price: Decimal | None,
    refresh_rate: bool,
    explicit_fields: set[str],
    market_service: Any,
    settings: PitwallSettings,
) -> dict[str, Any]:
    """Build the provider config for the launch."""
    history_gpu_types = _history_gpu_types(existing_config) if plan.history_required else []
    gpu_types = (
        history_gpu_types
        if history_gpu_types and "gpu_class" not in explicit_fields
        else [plan.gpu_class]
    )
    offered_cuda_versions: tuple[str, ...] | None = None
    if plan.info is not None and plan.info.min_cuda is not None:
        offered_cuda_versions = await _live_cuda_versions(
            market_service,
            settings,
            plan.gpu_class,
        )
    return _provider_config(
        request,
        info=plan.info,
        engine=plan.engine,
        image=plan.image,
        docker_start_cmd=plan.docker_start_cmd,
        docker_entrypoint=plan.docker_entrypoint,
        gpu_types=gpu_types,
        restoring_history=plan.history_required,
        served_model_id=plan.served_model_id,
        existing=existing_config,
        settings=settings,
        cloud_type=provider.cloud_type if provider is not None else None,
        price=price,
        offered_cuda_versions=offered_cuda_versions,
        refresh_rate=refresh_rate,
    )


async def _persist_registry(
    request: ServeRequest,
    plan: _ServePlan,
    *,
    provider_repo: Any,
    capability_repo: Any,
    provider: Provider | None,
    capability: Capability | None,
    provider_name: str,
    config: dict[str, Any],
) -> tuple[Capability, Provider]:
    """Create or patch the capability and provider rows for this serve."""
    if capability is None:
        now = dt.datetime.now(dt.UTC)
        capability = await capability_repo.create(
            Capability(
                id=f"cap_{ulid_new()}",
                name=request.capability_name,
                version="1.0.0",
                class_=CapabilityClass.LLM,
                cost_mode=CostMode.PER_SECOND,
                source=CapabilitySource.API,
                created_at=now,
                updated_at=now,
            )
        )
    patched_capability = await capability_repo.patch(
        capability.id,
        served_model_id=plan.served_model_id,
    )
    if patched_capability is not None:
        capability = patched_capability
    if provider is None:
        provider = await provider_repo.create(
            Provider(
                id=f"prov_{ulid_new()}",
                capability_id=capability.id,
                name=provider_name,
                provider_type=ProviderType.POD_LEASE,
                config=config,
                priority=0,
                source=CapabilitySource.API,
                updated_at=dt.datetime.now(dt.UTC),
            )
        )
    else:
        patched_provider = await provider_repo.patch(provider.id, config=config)
        if patched_provider is None:
            raise ServeLaunchFailed("serve provider disappeared while being updated")
        provider = patched_provider
    return capability, provider


async def _launch(
    pool: Any,
    request: ServeRequest,
    plan: _ServePlan,
    capability: Capability,
    provider: Provider,
    settings: PitwallSettings,
    *,
    request_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Run the launch and translate launch failures into serve errors."""
    try:
        if not request.dry_run:
            await CloudKillSwitch.ensure_disengaged(pool)
        return await run_launch(
            pool=pool,
            capability=capability,
            provider=provider,
            extra_env=plan.launch_only_env,
            idempotency_key=request.idempotency_key,
            dry_run=request.dry_run,
            rest_v1_api_url=settings.runpod_rest_v1_api_url,
            request_fingerprint=request_fingerprint,
            _pre_spend_inspected=True,
        )
    except BudgetRejected as exc:
        raise ServeBudgetExhausted(
            reason=exc.reason,
            snapshot=exc.snapshot.to_serializable_dict(),
        ) from exc
    except KillSwitchEngaged as exc:
        raise ServeKillSwitchEngaged from exc
    except InvalidProviderConfig as exc:
        raise ServeTemplateInvalid(str(exc)) from exc
    except RunPodError as exc:
        raise ServeLaunchFailed(str(exc)) from exc
    except Exception as exc:  # reason: identify untyped RunPod SDK GraphQL errors
        if _is_runpod_query_error(exc):
            raise ServeLaunchFailed(str(exc)) from exc
        raise


def _warm_result(
    request: ServeRequest,
    plan: _ServePlan,
    provider: Provider,
    *,
    lease_id: str | None,
    volume_id: str | None,
    seconds_to_ready: float | None,
    torn_down: bool,
    cost_estimate_usd: str | None,
    dry_run: bool,
) -> WarmResult:
    return WarmResult(
        capability=request.capability_name,
        provider_id=provider.id,
        lease_id=lease_id,
        volume_id=volume_id or "",
        datacenter=_optional_config_string(provider.config, "data_center_id"),
        model_id=plan.served_model_id,
        variant=request.variant,
        engine=plan.engine,
        gpu_class=plan.gpu_class,
        gpu_count=request.gpu_count,
        price_source=request.price_source,
        seconds_to_ready=seconds_to_ready,
        torn_down=torn_down,
        cost_estimate_usd=cost_estimate_usd,
        dry_run=dry_run,
        cache_state="warm"
        if warm_cache_matches(provider.config, variant=request.variant)
        else "cold",
    )


def _dry_run_result(
    request: ServeRequest,
    plan: _ServePlan,
    capability: Capability,
    provider: Provider,
    *,
    base_url: str,
    template_id: str | None,
    workload_id: str | None,
    price_freshness: GpuPriceFreshness | None,
    warm_only: bool,
) -> ServeResult | WarmResult:
    """Build the result for a dry-run launch, which never creates a lease."""
    estimate = estimate_lease_launch_cost(capability, provider)
    if warm_only:
        return _warm_result(
            request,
            plan,
            provider,
            lease_id=None,
            volume_id=_optional_config_string(provider.config, "network_volume_id"),
            seconds_to_ready=None,
            torn_down=False,
            cost_estimate_usd=str(estimate),
            dry_run=True,
        )
    return _result(
        request,
        served_model_id=plan.served_model_id,
        provider=provider,
        engine=plan.engine,
        variant=request.variant,
        gpu_count=request.gpu_count,
        base_url=base_url,
        lease_id=None,
        expires_at=None,
        workload_id=workload_id,
        template_id=template_id,
        dry_run=True,
        created=True,
        cost_estimate_usd=str(estimate),
        price_freshness=price_freshness,
    )


class _PostLaunchGuard:
    """Tear the launched pod down when any later serve step fails, then re-raise."""

    def __init__(self, lease_id: str, pool: Any) -> None:
        self.lease_id = lease_id
        self.pool = pool
        self.step = "post_launch"

    async def __aenter__(self) -> _PostLaunchGuard:
        return self

    async def __aexit__(
        self, exc_type: object, exc: BaseException | None, tb: object
    ) -> Literal[False]:
        if exc is None:
            return False
        if isinstance(exc, ServeVerificationFailed):
            reason = "served_model_mismatch"
        else:
            outcome = "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
            reason = f"serve_{self.step}_{outcome}"
        await asyncio.shield(self._teardown(reason))
        return False

    async def _teardown(self, reason: str) -> None:
        try:
            async with optional_redis_from_env() as redis_client:
                await run_teardown(
                    self.lease_id,
                    pool=self.pool,
                    redis_client=redis_client,
                    reason=reason,
                )
        except Exception:  # reason: the original serve failure must reach the caller
            log.error(
                "post-launch teardown failed: lease=%s reason=%s",
                self.lease_id,
                reason,
                exc_info=True,
            )


async def _verify_launched(
    provider_repo: Any,
    request: ServeRequest,
    plan: _ServePlan,
    provider: Provider,
    lease: Lease,
    *,
    settings: PitwallSettings,
    warm_only: bool,
    guard: _PostLaunchGuard,
) -> Provider:
    """Verify the launched pod serves the model, then record the warm cache if asked."""
    armed_provider = provider.model_copy(
        update={
            "config": {
                **provider.config,
                "active_pod_id": lease.runpod_pod_id,
                "active_lease_id": lease.id,
            }
        }
    )
    guard.step = "proxy_url"
    pod_base_url = pod_lease_base_url(armed_provider)
    if pod_base_url is None:
        raise ServeLaunchFailed("launched lease has no valid pod proxy URL")
    guard.step = "verify"
    await verify_served_model(
        f"{pod_base_url}/models",
        plan.served_model_id,
        headers={"Authorization": f"Bearer {settings.pitwall_endpoint_key}"},
    )
    if not warm_only:
        return provider
    guard.step = "warm_cache"
    volume_id = _optional_config_string(provider.config, "network_volume_id")
    if volume_id is None or request.variant is None:
        return provider
    warm_config = {
        **provider.config,
        "warm_cache": {
            "variant": request.variant,
            "verified_at": dt.datetime.now(dt.UTC).isoformat(),
            "volume_id": volume_id,
        },
    }
    patched_provider: Provider | None = await provider_repo.patch(provider.id, config=warm_config)
    if patched_provider is None:
        raise ServeLaunchFailed("serve provider disappeared while recording warm cache")
    return patched_provider


async def _finish_warm(
    pool: Any,
    request: ServeRequest,
    plan: _ServePlan,
    provider: Provider,
    lease: Lease,
    *,
    started_at: float,
    guard: _PostLaunchGuard,
) -> WarmResult:
    """Tear the verified warm pod down and report the warm result."""
    guard.step = "warm_teardown"
    async with optional_redis_from_env() as redis_client:
        teardown = await run_teardown(
            lease.id, pool=pool, redis_client=redis_client, reason="warm_complete"
        )
    return _warm_result(
        request,
        plan,
        provider,
        lease_id=lease.id,
        volume_id=_optional_config_string(provider.config, "network_volume_id"),
        seconds_to_ready=asyncio.get_running_loop().time() - started_at,
        torn_down=True,
        cost_estimate_usd=str(teardown.lease.cost_accrued_usd)
        if teardown.lease.cost_accrued_usd is not None
        else None,
        dry_run=False,
    )


__all__ = [
    "CatalogueLookup",
    "CatalogueUnknownVariant",
    "DEFAULT_STARTUP_TIMEOUT_S",
    "DEFAULT_TTL_MINUTES",
    "ENGINE_READINESS_PATH",
    "Engine",
    "NoCatalogue",
    "SERVE_HTTP_PORT",
    "SERVE_PROVIDER_PREFIX",
    "ServeRequest",
    "ServeResult",
    "WarmResult",
    "WarmOutcome",
    "VERIFY_REQUEST_TIMEOUT_S",
    "VERIFY_RETRY_INTERVAL_S",
    "VERIFY_TOTAL_TIMEOUT_S",
    "VariantInfo",
    "enforce_price_cap",
    "launch_shape",
    "serve_model",
    "warm_self_hosted",
    "serve_request_fingerprint",
    "verify_served_model",
]
