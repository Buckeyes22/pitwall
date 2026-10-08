"""Pod lease launch assembly for RunPod providers."""

from __future__ import annotations

import asyncio
import datetime as dt
import functools
import hashlib
import hmac
import json
import logging
import math
import os
import re
import shlex
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, cast

from pydantic import TypeAdapter

from pitwall.api.admin.kill_switch import enforce_kill_switch_admission
from pitwall.api.exceptions import (
    CapabilityNotFound,
    IdempotencyConflict,
    IdempotencyMismatch,
    LeaseLaunchInProgress,
    PitwallApiError,
    PreSpendPayloadRejected,
    ProviderNotFound,
)
from pitwall.api.leases.teardown import lock_provider_for_arming
from pitwall.core.enums import LeaseRenewalPolicy, LeaseState, ProviderAdapterId, ProviderType
from pitwall.core.models import (
    Capability,
    Lease,
    LeaseEndpoints,
    LeaseReadiness,
    Provider,
    Workload,
)
from pitwall.cost.budget_gate import BudgetAdmission, BudgetGate, BudgetNotConfigured
from pitwall.cost.estimator import GpuHourPricing, PerSecondPricing, parse_pricing_model
from pitwall.db.repository import (
    CapabilityRepository,
    LeaseRepository,
    ProviderRepository,
    WorkloadRepository,
    insert_audit,
)
from pitwall.providers.interface import (
    CredentialReference,
    ProviderOperationContext,
    ProvisionRequest,
    resolve_adapter_credentials,
)
from pitwall.providers.provisioning import LAUNCH_REQUEST_DIGEST_KEY as PROVISION_REQUEST_DIGEST_KEY
from pitwall.providers.provisioning import (
    ProvisionReplayConflict,
    ProvisionReplayFailed,
    ProvisionReplayInProgress,
    fallback_lease_rate_per_second,
    lease_rate_per_second,
)
from pitwall.providers.registry import get_default_registry
from pitwall.runpod_client.pods import (
    NoCapacityError,
    ProviderAttachHangRecoveryRequested,
    ProviderFallbackRequested,
    _create_pod_with_fallback,
    _terminate_pod_sync_for_auth,
    create_pod_with_fallback,
    terminate_pod_sync,
)
from pitwall.runpod_client.templates import (
    TEMPLATE_ENV_KEYS,
    TemplateNotFoundError,
    _lookup_cached,
    config_sha,
    ensure_template,
    get_image_ref_from_env,
    get_registry_auth_id_from_env,
    get_template,
    non_secret_env_keys,
    normalize_template_name,
    template_account_digest,
)
from pitwall.runpod_client.workloads import WorkloadConfig
from pitwall.runpod_credentials import RUNPOD_API_KEY_ENV, resolve_runpod_api_key
from pitwall.security.pre_spend import (
    PreSpendDecision,
    get_pre_spend_inspection_service,
)
from pitwall.security.redaction import redact_text
from pitwall.staging_store import StagingStore, get_staging_store

log = logging.getLogger("pitwall.api.leases.launch")

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CONFIG_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_CONFIG_KEY_SEPARATOR_RE = re.compile(r"[^A-Za-z0-9]+")
_CREDENTIAL_REFERENCE_SUFFIXES = ("_env", "_ref", "_reference")
_CREDENTIAL_KEY_MARKERS = (
    "access_key",
    "api_key",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)
_PITWALL_IDENTITY_ENV_KEYS = frozenset(
    {
        "PITWALL_CAPABILITY",
        "PITWALL_CAPABILITY_ID",
        "PITWALL_CAPABILITY_NAME",
        "PITWALL_PROVIDER",
        "PITWALL_PROVIDER_ID",
        "PITWALL_PROVIDER_NAME",
        "PITWALL_PROVIDER_TYPE",
        "PITWALL_REQUEST_ID",
        "HF_TOKEN",
    }
)
_PITWALL_STORAGE_CREDENTIAL_ENV_KEYS = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "R2_ACCESS_KEY",
        "R2_SECRET_KEY",
        "R2_SESSION_TOKEN",
        "R2_CREDENTIAL_EXPIRES_AT",
        "R2_CREDENTIAL_TTL_SECONDS",
    }
)
_FORWARDED_PROCESS_ENV_KEYS = (
    "REDIS_URL",
    "LANGFUSE_HOST",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "R2_ENDPOINT",
    "R2_BUCKET_STAGING",
)
ATTACH_HANG_PROVIDER_COOLDOWN = dt.timedelta(minutes=15)
_JSON_OBJECT_ADAPTER = TypeAdapter(dict[str, Any])


class LaunchConfigError(RuntimeError):
    """Raised when a provider cannot be assembled into a pod launch."""


class InvalidProviderConfig(LaunchConfigError, PitwallApiError):
    """Raised when provider.config has an invalid launch shape."""

    status_code = 422
    error_code = "invalid_provider_config"

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "detail": str(self)}


class ProviderNotPodLease(LaunchConfigError):
    """Raised when launch is attempted for a non pod-lease provider."""


class TemplateImageNotConfigured(LaunchConfigError):
    """Raised when no image ref can be resolved for template creation."""


@dataclass(frozen=True)
class LaunchTemplate:
    """Resolved RunPod template information for a pod lease launch."""

    template_id: str
    template_name: str
    image_ref: str
    registry_auth_id: str | None
    container_disk_gb: int
    volume_mount_path: str


@dataclass(frozen=True)
class LeaseLaunchPlan:
    """Template, env, and RunPod workload shape ready for pod creation."""

    template: LaunchTemplate
    env: dict[str, str]
    workload: WorkloadConfig
    network_volume_id: str | None
    data_center_id: str | None
    volume_attach_timeout_s: float | None
    docker_entrypoint: list[str] | None = None
    docker_start_cmd: list[str] | None = None
    startup_timeout_s: int = 600
    readiness_path: str = "/health"


def _provider_config(provider: Provider | Any) -> Mapping[str, Any]:
    config = getattr(provider, "config", {})
    return config if isinstance(config, Mapping) else {}


def _lease_automation_fields(
    provider: Provider | Any,
) -> tuple[LeaseRenewalPolicy, int | None, Decimal | None]:
    config = _provider_config(provider)
    policy = LeaseRenewalPolicy(str(config.get("renewal_policy", "manual")))
    raw_idle = config.get("idle_timeout_min")
    idle = int(raw_idle) if raw_idle is not None else None
    raw_cap = config.get("max_usd_per_hour")
    cap = Decimal(str(raw_cap)) if raw_cap is not None else None
    return policy, idle, cap


def _docker_start_cmd(provider: Provider | Any) -> list[str] | None:
    """Container start arguments (RunPod ``dockerStartCmd``) from provider config."""

    value = _provider_config(provider).get("docker_start_cmd")
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise InvalidProviderConfig(
            "provider.config['docker_start_cmd'] must be a list of non-empty strings"
        )
    command = list(value)
    config = _provider_config(provider)
    if config.get("endpoint_auth") is not True:
        return command
    if config.get("docker_entrypoint") != ["sh", "-c"]:
        raise InvalidProviderConfig(
            "authenticated serve command requires docker_entrypoint ['sh', '-c']"
        )
    if "--api-key" in command or any(item.startswith("--api-key=") for item in command):
        raise InvalidProviderConfig(
            "authenticated serve command must not provide a literal model API key"
        )
    engine = config.get("engine")
    if engine == "vllm" and tuple(command[:2]) != ("vllm-omni", "serve"):
        command = ["vllm", "serve", *command]
    elif engine == "llama.cpp":
        command = ["/app/llama-server", *command]
    script = f'exec {shlex.join(command)} --api-key "$PITWALL_ENDPOINT_KEY"'
    return [script]


def _docker_entrypoint(provider: Provider | Any) -> list[str] | None:
    """Container entrypoint override from provider config."""

    value = _provider_config(provider).get("docker_entrypoint")
    if value is None:
        return None
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise InvalidProviderConfig(
            "provider.config['docker_entrypoint'] must be a list of non-empty strings"
        )
    return list(value)


def _startup_timeout_s(provider: Provider | Any) -> int:
    value = _provider_config(provider).get("startup_timeout_s", 600)
    if isinstance(value, bool) or not isinstance(value, int) or not 60 <= value <= 7_200:
        raise InvalidProviderConfig(
            "provider.config['startup_timeout_s'] must be an integer between 60 and 7200"
        )
    return value


def _readiness_path(provider: Provider | Any) -> str:
    value = _provider_config(provider).get("readiness_path", "/health")
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 128:
        raise InvalidProviderConfig(
            "provider.config['readiness_path'] must begin with '/' and be at most 128 characters"
        )
    return value


def _required_attr(obj: object, attr: str) -> str:
    value = getattr(obj, attr, None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise InvalidProviderConfig(f"{attr} must be a non-empty string")


def _optional_str(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _config_str(config: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _optional_str(config.get(key))
        if value is not None:
            return value
    return None


def _config_int(config: Mapping[str, Any], key: str, default: int) -> int:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise InvalidProviderConfig(f"provider.config[{key!r}] must be an integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = int(value)
        except ValueError as exc:
            raise InvalidProviderConfig(f"provider.config[{key!r}] must be an integer") from exc
    else:
        raise InvalidProviderConfig(f"provider.config[{key!r}] must be an integer")
    if parsed <= 0:
        raise InvalidProviderConfig(f"provider.config[{key!r}] must be > 0")
    return parsed


def _config_float(config: Mapping[str, Any], key: str) -> float | None:
    value = config.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidProviderConfig(f"provider.config[{key!r}] must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise InvalidProviderConfig(f"provider.config[{key!r}] must be a number") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise InvalidProviderConfig(f"provider.config[{key!r}] must be >= 0")
    return parsed


def _config_str_list(config: Mapping[str, Any], *keys: str) -> list[str] | None:
    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            items = [item.strip() for item in value.split(",") if item.strip()]
        elif isinstance(value, list | tuple):
            items = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        else:
            raise InvalidProviderConfig(
                f"provider.config[{key!r}] must be a string list or comma-separated string"
            )
        if not items:
            raise InvalidProviderConfig(f"provider.config[{key!r}] must not be empty")
        return items
    return None


def _provider_type_value(provider: Provider | Any) -> str:
    provider_type = getattr(provider, "provider_type", None)
    if isinstance(provider_type, ProviderType):
        return provider_type.value
    if isinstance(provider_type, str):
        return provider_type
    value = getattr(provider_type, "value", None)
    if isinstance(value, str):
        return value
    raise InvalidProviderConfig("provider_type must be set")


def _ensure_pod_lease_provider(provider: Provider | Any) -> None:
    provider_type = _provider_type_value(provider)
    if provider_type != ProviderType.POD_LEASE.value:
        provider_id = getattr(provider, "id", "<unknown>")
        raise ProviderNotPodLease(
            f"provider {provider_id!r} has provider_type={provider_type!r}; "
            f"expected {ProviderType.POD_LEASE.value!r}"
        )


def _capability_id(capability: Capability | Any) -> str:
    return _required_attr(capability, "id")


def _capability_name(capability: Capability | Any) -> str:
    return _required_attr(capability, "name")


def _capability_class(capability: Capability | Any) -> str:
    class_value = getattr(capability, "class_", None)
    if class_value is None:
        class_value = getattr(capability, "capability_class", None)
    if isinstance(class_value, str) and class_value.strip():
        return class_value.strip()
    enum_value = getattr(class_value, "value", None)
    if isinstance(enum_value, str) and enum_value.strip():
        return enum_value.strip()
    return _capability_name(capability)


def _provider_id(provider: Provider | Any) -> str:
    return _required_attr(provider, "id")


def _provider_name(provider: Provider | Any) -> str:
    return _required_attr(provider, "name")


def provider_runpod_api_key(provider: Provider | Any) -> str | None:
    """The RunPod key a provider's own ``credential_ref`` names, resolved at the call.

    ``None`` means the provider uses the process credential: the default reference is
    ``RUNPOD_API_KEY`` itself, which the RunPod client already resolves (environment, then
    ``runpodctl``). Any other reference is read from the environment and returned so the
    caller passes it explicitly; an unset variable raises ``CredentialResolutionError``
    naming only the reference.
    """
    from pitwall.providers.runpod import RunPodCredentials  # the adapter imports this module

    reference = getattr(provider, "credential_ref", None)
    if not isinstance(reference, str) or reference == RUNPOD_API_KEY_ENV:
        return None
    credentials = resolve_adapter_credentials(
        CredentialReference(reference), RunPodCredentials, adapter_id="runpod"
    )
    return credentials.api_key.get_secret_value()


def _template_name_for_provider(
    capability: Capability | Any,
    provider: Provider | Any,
) -> str:
    config = _provider_config(provider)
    configured = _config_str(config, "template_name")
    if configured is not None:
        return configured
    return f"pitwall-{_capability_name(capability)}-{_provider_name(provider)}"


def _image_ref_for_provider(provider: Provider | Any) -> str:
    config = _provider_config(provider)
    configured = _config_str(config, "image_ref", "worker_image", "image")
    if configured is not None:
        return configured
    try:
        return get_image_ref_from_env()
    except RuntimeError as exc:
        raise TemplateImageNotConfigured(str(exc)) from exc


def _volume_mount_path(provider: Provider | Any) -> str:
    config = _provider_config(provider)
    return _config_str(config, "volume_mount_path", "volume_mount") or "/workspace"


def _container_disk_gb(provider: Provider | Any) -> int:
    return _config_int(_provider_config(provider), "container_disk_gb", 50)


def _network_volume_id(provider: Provider | Any) -> str | None:
    config = _provider_config(provider)
    configured = _config_str(config, "network_volume_id", "volume_id")
    if configured is not None:
        return configured
    return _optional_str(os.environ.get("RUNPOD_NETWORK_VOLUME_ID"))


def _data_center_id(provider: Provider | Any) -> str | None:
    """Resolve the datacenter to pin, if any.

    A network volume lives in exactly one datacenter, so a volume-backed provider's
    region wins. Without a volume there is nothing to constrain, and an explicitly
    configured datacenter must still be honoured — silently ignoring it placed pods
    wherever RunPod chose while the operator believed they had pinned a region.
    """
    if _network_volume_id(provider) is not None:
        region = _optional_str(getattr(provider, "region", None))
        if region is not None:
            return region
    config = _provider_config(provider)
    configured = _config_str(config, "data_center_id", "datacenter_id")
    if configured is not None:
        return configured
    return _optional_str(os.environ.get("RUNPOD_DATA_CENTER_ID"))


def _cloud_type(provider: Provider | Any) -> str:
    value = _optional_str(getattr(provider, "cloud_type", None))
    if value is None:
        value = _config_str(_provider_config(provider), "cloud_type")
    if value is not None:
        return value.upper()
    return "SECURE"


def _gpu_type_priority_mode(config: Mapping[str, Any]) -> Literal["custom", "availability"]:
    raw = config.get("gpu_type_priority_mode", config.get("gpu_selection_priority", "custom"))
    if not isinstance(raw, str):
        raise InvalidProviderConfig("provider.config gpu priority mode must be a string")
    normalized = raw.strip().lower()
    if normalized not in {"custom", "availability"}:
        raise InvalidProviderConfig(
            "provider.config gpu priority mode must be 'custom' or 'availability'"
        )
    return "availability" if normalized == "availability" else "custom"


def _data_center_priority_mode(config: Mapping[str, Any]) -> Literal["custom", "availability"]:
    raw = config.get("data_center_priority", "custom")
    if not isinstance(raw, str):
        raise InvalidProviderConfig("provider.config data_center_priority must be a string")
    normalized = raw.strip().lower()
    if normalized not in {"custom", "availability"}:
        raise InvalidProviderConfig(
            "provider.config data_center_priority must be 'custom' or 'availability'"
        )
    return "availability" if normalized == "availability" else "custom"


def _ports_for_workload(provider: Provider | Any) -> str | None:
    ports = _provider_config(provider).get("ports")
    if ports is None:
        return None
    if isinstance(ports, str):
        return ports.strip() or None
    if not isinstance(ports, Mapping):
        raise InvalidProviderConfig("provider.config['ports'] must be a string or mapping")

    rendered: list[str] = []
    for protocol in ("http", "tcp"):
        values = ports.get(protocol)
        if values is None:
            continue
        if isinstance(values, int):
            rendered.append(f"{values}/{protocol}")
            continue
        if isinstance(values, list | tuple):
            for value in values:
                if not isinstance(value, int):
                    raise InvalidProviderConfig(
                        f"provider.config['ports'][{protocol!r}] must contain integers"
                    )
                rendered.append(f"{value}/{protocol}")
            continue
        raise InvalidProviderConfig(
            f"provider.config['ports'][{protocol!r}] must be an integer or integer list"
        )
    return ",".join(rendered) or None


def _workload_config_for_provider(
    capability: Capability | Any,
    provider: Provider | Any,
) -> WorkloadConfig:
    config = _provider_config(provider)
    gpu_types = _config_str_list(config, "gpu_types", "gpu_type_priority")
    if gpu_types is None:
        raise InvalidProviderConfig(
            "provider.config must include 'gpu_types' or 'gpu_type_priority'"
        )
    return WorkloadConfig(
        name=_provider_name(provider),
        capability=_capability_name(capability),
        template_name=_template_name_for_provider(capability, provider),
        gpu_types=gpu_types,
        gpu_count=_config_int(config, "gpu_count", 1),
        container_disk_gb=_config_int(config, "container_disk_gb", 50),
        min_vcpu=_config_int(config, "min_vcpu", 4),
        min_memory_gb=_config_int(config, "min_memory_gb", 16),
        cloud_type=_cloud_type(provider),
        gpu_type_priority=_gpu_type_priority_mode(config),
        data_center_priority=_data_center_priority_mode(config),
        allowed_cuda_versions=_config_str_list(config, "allowed_cuda_versions", "cuda_versions"),
        ports=_ports_for_workload(provider),
    )


def _max_cost_per_hr(provider: Provider | Any) -> float | None:
    config = _provider_config(provider)
    constraints = config.get("constraints")
    sources = [constraints, config] if isinstance(constraints, Mapping) else [config]
    for source in sources:
        for key in ("max_cost_per_hr", "max_cost_per_hour"):
            value = _config_float(source, key)
            if value is not None:
                return value
    return None


def _provider_attach_timeout_s(provider: Provider | Any) -> float | None:
    config = _provider_config(provider)
    constraints = config.get("constraints")
    sources = [constraints, config] if isinstance(constraints, Mapping) else [config]
    for source in sources:
        for key in ("max_attach_hang_s", "volume_attach_timeout_s", "attach_timeout_s"):
            value = _config_float(source, key)
            if value is not None:
                return value
    return None


def _lease_id_for_launch(provider: Provider | Any) -> str:
    """Generate a unique lease ID from provider and a UUID suffix."""
    safe_id = _provider_id(provider).replace("-", "_")
    return f"lease_{safe_id}_{uuid.uuid4().hex[:12]}"


def _expiry_for_lease(provider: Provider | Any, created_at: dt.datetime) -> dt.datetime:
    """Calculate lease expiry time from provider config or default."""
    config = _provider_config(provider)
    ttl_ms = config.get("lease_ttl_ms") or config.get("ttl_ms") or 7200000
    ttl_s = int(ttl_ms) / 1000 if isinstance(ttl_ms, (int, float)) else 7200
    return created_at + dt.timedelta(seconds=ttl_s)


def _planned_endpoints_for_provider(provider: Provider | Any) -> LeaseEndpoints:
    """Construct planned LeaseEndpoints from provider port config."""
    config = _provider_config(provider)
    ports = config.get("ports") or {}
    http_endpoints: dict[str, str] = {}
    tcp_endpoints: dict[str, Any] = {}

    if isinstance(ports, Mapping):
        for protocol in ("http", "tcp"):
            values = ports.get(protocol)
            if values is None:
                continue
            if isinstance(values, int):
                values = [values]
            if isinstance(values, (list, tuple)):
                for port in values:
                    if not isinstance(port, int):
                        continue
                    if protocol == "http":
                        http_endpoints[str(port)] = f"https://{{pod_id}}-{port}.proxy.runpod.net"
                    else:
                        tcp_endpoints[str(port)] = {
                            "host": "{pod_id}.proxy.runpod.net",
                            "port": port,
                        }

    return LeaseEndpoints(http=http_endpoints, tcp=tcp_endpoints)


def _lease_readiness_from_ready_pod(pod: Mapping[str, Any]) -> LeaseReadiness:
    readiness_json = pod.get("readiness")
    if not isinstance(readiness_json, Mapping):
        pod_id = pod.get("id") or "<unknown>"
        raise LaunchConfigError(f"ready pod {pod_id!r} did not include readiness signals")

    readiness = LeaseReadiness.model_validate(dict(readiness_json))
    if not readiness.has_active_signals:
        pod_id = pod.get("id") or "<unknown>"
        raise LaunchConfigError(f"ready pod {pod_id!r} has incomplete readiness signals")
    return readiness


async def _persist_ready_lease(
    pool: Any,
    *,
    lease_id: str,
    ready_pod: Mapping[str, Any],
) -> None:
    if not hasattr(pool, "acquire"):
        return

    lease_repo = LeaseRepository(pool)
    readiness = _lease_readiness_from_ready_pod(ready_pod)
    await lease_repo.update_state(lease_id, LeaseState.WAITING_RUNTIME.value)
    await lease_repo.update_state(lease_id, LeaseState.WAITING_PROBE.value)
    await lease_repo.update_readiness(lease_id, readiness)
    ready_at = readiness.probe_passed_at
    assert ready_at is not None
    if hasattr(lease_repo, "mark_ready"):
        await lease_repo.mark_ready(lease_id, ready_at=ready_at)
    await lease_repo.update_state(lease_id, LeaseState.ACTIVE.value)
    if hasattr(lease_repo, "mark_linked_workload_running"):
        await lease_repo.mark_linked_workload_running(lease_id, started_at=ready_at)


async def arm_serve_provider(
    pool: Any,
    *,
    provider: Provider | Any,
    lease_id: str,
    pod_id: str,
) -> bool:
    """Point the provider's OpenAI proxy at the pod that just became ACTIVE.

    Only providers that declare ``openai_proxy_port`` take part; the proxy URL is
    derived later from ``active_pod_id`` + ``openai_proxy_port`` (never stored).
    Returns True when the provider was armed.
    """

    config = dict(_provider_config(provider))
    if not hasattr(pool, "acquire"):
        return False

    provider_id = _provider_id(provider)
    acquire = getattr(pool, "acquire", None)
    if not callable(acquire) and config.get("openai_proxy_port") is None:
        # No transaction to lock in (fake pools): the snapshot is all there is.
        return False
    repo = ProviderRepository(pool)
    if callable(acquire):
        async with acquire() as conn, conn.transaction():
            # Decide and build the new config from the provider row locked here, not from the
            # snapshot the launch read earlier: another arm or disarm may have changed it since.
            live = await lock_provider_for_arming(conn, provider_id)
            if live is None:
                return False
            live_config, old_health = live
            if live_config.get("openai_proxy_port") is None:
                return False
            new_config = {**live_config, "active_pod_id": pod_id, "active_lease_id": lease_id}
            await repo.patch(provider_id, config=new_config, health_status="healthy", conn=conn)
            await insert_audit(
                pool,
                actor="system:lease",
                action="lease_ready",
                entity_type="provider",
                entity_id=provider_id,
                old_value={"config": live_config, "health_status": old_health},
                new_value={"config": new_config, "health_status": "healthy"},
                change_reason=f"lease {lease_id} ready on pod {pod_id}",
                conn=conn,
            )
    else:
        old_health = str(getattr(provider, "health_status", "unknown"))
        new_config = {**config, "active_pod_id": pod_id, "active_lease_id": lease_id}
        await repo.patch(provider_id, config=new_config, health_status="healthy")
        await insert_audit(
            pool,
            actor="system:lease",
            action="lease_ready",
            entity_type="provider",
            entity_id=provider_id,
            old_value={"config": config, "health_status": old_health},
            new_value={"config": new_config, "health_status": "healthy"},
            change_reason=f"lease {lease_id} ready on pod {pod_id}",
        )
    return True


async def _upsert_initial_lease(pool: Any, lease: Lease) -> None:
    async with pool.acquire() as conn:
        await conn.fetchrow(
            """
            INSERT INTO pitwall.leases
                (id, provider_id, workload_id, external_resource_id, runpod_pod_id,
                 state, created_at,
                 expires_at, renewal_policy, auto_teardown_on_expiry,
                 endpoints, readiness, cost_accrued_usd, last_health_at,
                 idle_timeout_min, max_usd_per_hour)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb, $12::jsonb,
                    $13, $14, $15, $16)
            ON CONFLICT (id) DO UPDATE SET
                provider_id = EXCLUDED.provider_id,
                workload_id = EXCLUDED.workload_id,
                external_resource_id = EXCLUDED.external_resource_id,
                runpod_pod_id = EXCLUDED.runpod_pod_id,
                state = EXCLUDED.state,
                created_at = EXCLUDED.created_at,
                expires_at = EXCLUDED.expires_at,
                renewal_policy = EXCLUDED.renewal_policy,
                auto_teardown_on_expiry = EXCLUDED.auto_teardown_on_expiry,
                endpoints = EXCLUDED.endpoints,
                readiness = EXCLUDED.readiness,
                cost_accrued_usd = EXCLUDED.cost_accrued_usd,
                last_health_at = EXCLUDED.last_health_at,
                idle_timeout_min = EXCLUDED.idle_timeout_min,
                max_usd_per_hour = EXCLUDED.max_usd_per_hour,
                terminated_at = NULL,
                terminated_reason = NULL
            RETURNING *
            """,
            lease.id,
            lease.provider_id,
            lease.workload_id,
            lease.external_resource_id,
            lease.runpod_pod_id,
            lease.state.value if hasattr(lease.state, "value") else lease.state,
            lease.created_at,
            lease.expires_at,
            lease.renewal_policy.value
            if hasattr(lease.renewal_policy, "value")
            else lease.renewal_policy,
            lease.auto_teardown_on_expiry,
            lease.endpoints.model_dump_json() if lease.endpoints is not None else None,
            lease.readiness.model_dump_json() if lease.readiness is not None else None,
            lease.cost_accrued_usd,
            lease.last_health_at,
            lease.idle_timeout_min,
            lease.max_usd_per_hour,
        )


def _coerce_env_mapping(
    raw_env: object, *, source: str, allow_launch_secrets: bool = False
) -> dict[str, str]:
    if raw_env is None:
        return {}
    if not isinstance(raw_env, Mapping):
        raise InvalidProviderConfig(f"{source} must be a mapping")
    env: dict[str, str] = {}
    for raw_key, raw_value in raw_env.items():
        if not isinstance(raw_key, str) or not _ENV_KEY_RE.fullmatch(raw_key):
            raise InvalidProviderConfig(f"{source} contains invalid env key {raw_key!r}")
        launch_only_hf = allow_launch_secrets and raw_key == "HF_TOKEN"
        if raw_key in _PITWALL_IDENTITY_ENV_KEYS and not launch_only_hf:
            raise InvalidProviderConfig(
                f"{source} cannot override Pitwall launch identity key {raw_key!r}"
            )
        if raw_key in _PITWALL_STORAGE_CREDENTIAL_ENV_KEYS:
            raise InvalidProviderConfig(
                f"{source} cannot inject Pitwall-managed storage credential key {raw_key!r}"
            )
        if raw_value is not None:
            env[raw_key] = str(raw_value)
    return env


def _env_for_pod(
    capability: Capability | Any,
    provider: Provider | Any,
    *,
    request_id: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    staging_store: StagingStore | None = None,
) -> dict[str, str]:
    """Return per-launch env overrides injected into the RunPod pod."""

    config = _provider_config(provider)
    env = _coerce_env_mapping(config.get("env_vars"), source="provider.config['env_vars']")

    for key in _FORWARDED_PROCESS_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            env[key] = value

    env.update((staging_store or get_staging_store()).vend_pod_credentials())

    env.update(
        {
            "PITWALL_CAPABILITY": _capability_class(capability),
            "PITWALL_CAPABILITY_ID": _capability_id(capability),
            "PITWALL_CAPABILITY_NAME": _capability_name(capability),
            "PITWALL_PROVIDER": _provider_name(provider),
            "PITWALL_PROVIDER_ID": _provider_id(provider),
            "PITWALL_PROVIDER_NAME": _provider_name(provider),
            "PITWALL_PROVIDER_TYPE": _provider_type_value(provider),
        }
    )
    if request_id is not None and request_id.strip():
        env["PITWALL_REQUEST_ID"] = request_id.strip()

    env.update(_coerce_env_mapping(extra_env, source="extra_env", allow_launch_secrets=True))
    return env


async def ensure_launch_template(
    pool: Any,
    capability: Capability | Any,
    provider: Provider | Any,
    *,
    dry_run: bool = False,
    api_key: str | None = None,
    graphql_url: str | None = None,
    rest_api_url: str | None = None,
) -> LaunchTemplate:
    """Resolve/create the RunPod template for a pod-lease provider."""

    _ensure_pod_lease_provider(provider)
    configured_id = _config_str(_provider_config(provider), "template_id")
    if configured_id is not None:
        if dry_run:
            return LaunchTemplate(
                template_id=configured_id,
                template_name=_template_name_for_provider(capability, provider),
                image_ref=_image_ref_for_provider(provider),
                registry_auth_id=None,
                container_disk_gb=_container_disk_gb(provider),
                volume_mount_path=_volume_mount_path(provider),
            )
        try:
            get_template_kwargs: dict[str, Any] = {}
            if api_key is not None:
                get_template_kwargs["api_key"] = api_key
            if rest_api_url is not None:
                get_template_kwargs["rest_api_url"] = rest_api_url
            supplied = await get_template(configured_id, **get_template_kwargs)
        except (TemplateNotFoundError, RuntimeError, ValueError) as exc:
            raise InvalidProviderConfig(
                f"provider.config['template_id'] could not resolve pod template {configured_id!r}"
            ) from exc
        if supplied.is_serverless is not False:
            raise InvalidProviderConfig(
                "provider.config['template_id'] must reference a non-serverless pod template"
            )
        config = _provider_config(provider)
        disk = _config_int(config, "container_disk_gb", supplied.container_disk_in_gb)
        mount = _config_str(config, "volume_mount_path", "volume_mount")
        return LaunchTemplate(
            template_id=configured_id,
            template_name=supplied.name,
            image_ref=supplied.image_name,
            registry_auth_id=None,
            container_disk_gb=disk,
            volume_mount_path=mount or supplied.volume_mount_path or "/workspace",
        )
    image_ref = _image_ref_for_provider(provider)
    template_name = _template_name_for_provider(capability, provider)
    registry_auth_id = get_registry_auth_id_from_env(image_ref)
    container_disk_gb = _container_disk_gb(provider)
    volume_mount_path = _volume_mount_path(provider)
    docker_start_cmd = _docker_start_cmd(provider)
    ports = _ports_for_workload(provider)
    configured_env = _coerce_env_mapping(
        _provider_config(provider).get("env_vars"), source="provider.config['env_vars']"
    )
    if dry_run:
        cached_template_id = await _lookup_cached(
            pool,
            normalize_template_name(template_name),
            config_sha(
                image_ref,
                docker_start_cmd=docker_start_cmd or (),
                ports=ports,
                env_keys=non_secret_env_keys((*TEMPLATE_ENV_KEYS, *configured_env)),
                container_disk_gb=container_disk_gb,
                registry_auth_id=registry_auth_id,
                account_digest=template_account_digest(api_key),
            ),
        )
        return LaunchTemplate(
            template_id=cached_template_id or "dry-run",
            template_name=template_name,
            image_ref=image_ref,
            registry_auth_id=registry_auth_id,
            container_disk_gb=container_disk_gb,
            volume_mount_path=volume_mount_path,
        )
    template_kwargs: dict[str, Any] = {}
    if api_key is not None:
        template_kwargs["api_key"] = api_key
    if graphql_url is not None:
        template_kwargs["graphql_url"] = graphql_url
    if rest_api_url is not None:
        template_kwargs["rest_api_url"] = rest_api_url
    template_id = await ensure_template(
        pool,
        image_ref,
        template_name=template_name,
        registry_auth_id=registry_auth_id,
        container_disk_gb=container_disk_gb,
        volume_mount_path=volume_mount_path,
        docker_start_cmd=docker_start_cmd or (),
        ports=ports,
        env=configured_env,
        **template_kwargs,
    )
    return LaunchTemplate(
        template_id=template_id,
        template_name=template_name,
        image_ref=image_ref,
        registry_auth_id=registry_auth_id,
        container_disk_gb=container_disk_gb,
        volume_mount_path=volume_mount_path,
    )


async def prepare_lease_launch(
    pool: Any,
    capability: Capability | Any,
    provider: Provider | Any,
    *,
    dry_run: bool = False,
    request_id: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    staging_store: StagingStore | None = None,
    api_key: str | None = None,
    graphql_url: str | None = None,
    rest_api_url: str | None = None,
) -> LeaseLaunchPlan:
    """Assemble template, env, workload, and placement inputs for pod creation."""

    template = await ensure_launch_template(
        pool,
        capability,
        provider,
        dry_run=dry_run,
        api_key=api_key,
        graphql_url=graphql_url,
        rest_api_url=rest_api_url,
    )
    return LeaseLaunchPlan(
        template=template,
        env=_env_for_pod(
            capability,
            provider,
            request_id=request_id,
            extra_env=extra_env,
            staging_store=staging_store,
        ),
        workload=_workload_config_for_provider(capability, provider),
        network_volume_id=_network_volume_id(provider),
        data_center_id=_data_center_id(provider),
        volume_attach_timeout_s=_provider_attach_timeout_s(provider),
        docker_entrypoint=_docker_entrypoint(provider),
        docker_start_cmd=_docker_start_cmd(provider),
        startup_timeout_s=_startup_timeout_s(provider),
        readiness_path=_readiness_path(provider),
    )


def _lease_ttl_seconds(provider: Provider | Any) -> Decimal:
    config = _provider_config(provider)
    ttl_ms = config.get("lease_ttl_ms") or config.get("ttl_ms") or 7_200_000
    if isinstance(ttl_ms, bool) or not isinstance(ttl_ms, int | float):
        return Decimal(7_200)
    return Decimal(str(ttl_ms)) / Decimal(1_000)


def estimate_lease_launch_cost(
    capability: Capability,
    provider: Provider | Any,
    payload: Mapping[str, Any] | None = None,
) -> Decimal:
    """Estimate the budget reservation for a pod-lease launch.

    A lease accrues ``rate × elapsed`` until teardown, so the reservation covers
    the whole TTL at the ceiling rate (bid rate when configured). Other pricing reserves
    its lease rate (``lease_rate_per_second``), else ``FALLBACK_LEASE_USD_PER_HOUR``, for
    the TTL: the same rule ``lease_reservation`` applies to Lambda Cloud and Vast.
    """

    provider_cost = _provider_config(provider)
    pricing = parse_pricing_model(provider_cost, cost_mode=capability.cost_mode)
    if isinstance(pricing, PerSecondPricing):
        ceiling = pricing.rate_per_second
        if pricing.bid_rate_per_second is not None:
            ceiling = max(ceiling, pricing.bid_rate_per_second)
        return (ceiling * _lease_ttl_seconds(provider)).quantize(Decimal("0.000001"))
    if isinstance(pricing, GpuHourPricing):
        ceiling = pricing.per_second_active
        cost = provider_cost.get("cost")
        if isinstance(cost, Mapping):
            bid_rate = cost.get("bid_rate_per_second")
            if bid_rate is not None:
                ceiling = max(ceiling, Decimal(str(bid_rate)))
        return (ceiling * _lease_ttl_seconds(provider)).quantize(Decimal("0.000001"))

    # Any other pricing settles at its lease rate, else the fallback (teardown's
    # ``settlement_rate_per_second``), so it reserves that rate for the whole TTL too.
    rate = lease_rate_per_second(pricing)
    rate = rate if rate is not None else fallback_lease_rate_per_second()
    return (rate * _lease_ttl_seconds(provider)).quantize(Decimal("0.000001"))


async def admit_lease_launch(
    pool: Any,
    capability: Capability,
    provider: Provider | Any,
    *,
    budget_gate: Any | None = None,
    payload: Mapping[str, Any] | None = None,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
) -> BudgetAdmission:
    """Admit a pod-lease launch through the account budget gate.

    The admission says whether this call inserted the workload. A keyed admission records
    ``request_fingerprint`` on the new workload inside the admission transaction, so a
    same-key request that waited on the budget lock always sees the digest it must match.
    """

    _ensure_pod_lease_provider(provider)
    estimate_usd = estimate_lease_launch_cost(capability, provider, payload)
    gate = budget_gate if budget_gate is not None else BudgetGate(pool)
    kwargs: dict[str, Any] = {
        "capability_id": _capability_id(capability),
        "provider_id": _provider_id(provider),
        "estimate_usd": estimate_usd,
        "workload_type": "inference",
        "idempotency_key": idempotency_key,
    }
    admission_method = getattr(gate, "try_launch_admission", None)
    if not callable(admission_method):
        # A gate without the admission API cannot report a key hit; it admits fresh.
        return BudgetAdmission(workload_id=str(await gate.try_launch(**kwargs)), is_new=True)
    if idempotency_key is not None and request_fingerprint is not None:
        document = json.dumps({LAUNCH_REQUEST_DIGEST_KEY: request_fingerprint})

        async def record_request_digest(conn: Any, workload_id: str) -> None:
            await conn.execute(
                "UPDATE pitwall.workloads SET input = $2::jsonb WHERE id = $1",
                workload_id,
                document,
            )

        kwargs["after_new_admission"] = record_request_digest
    admitted = await admission_method(**kwargs)
    return BudgetAdmission(workload_id=str(admitted.workload_id), is_new=bool(admitted.is_new))


#: Workload ``input`` key holding the SHA-256 of the request a keyed lease launch admitted
#: (shared with the Lambda Cloud and Vast admission, ``admit_provision``).
LAUNCH_REQUEST_DIGEST_KEY = PROVISION_REQUEST_DIGEST_KEY
_LAUNCH_CREDENTIAL_KEY_CONTEXT = b"pitwall-lease-launch-v1"
_LAUNCH_IN_FLIGHT_STATES = frozenset({"queued", "running"})
#: Cost provenance of a launch failure proven to have created no pod.
_PRECREATION_PROVENANCE = "lease_launch_precreation_failure"


def launch_request_fingerprint(document: Mapping[str, Any]) -> str:
    """SHA-256 of a canonical request document (sorted keys, compact separators)."""
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def routed_lease_fingerprint(capability_id: str, provider_id: str | None) -> str:
    """The digest of a ``POST /v1/leases`` / ``pitwall_lease_pod`` request.

    It covers what the caller asked for (the resolved capability and the optional provider
    pin), not the planner's choice, so a retry the planner would route differently still
    replays the first launch.
    """
    return launch_request_fingerprint(
        {"surface": "lease", "capability_id": capability_id, "provider_id": provider_id}
    )


def default_launch_fingerprint(
    capability: Capability | Any,
    provider: Provider | Any,
    *,
    payload: Mapping[str, Any] | None,
    extra_env: Mapping[str, str] | None,
    api_key: str | None = None,
) -> str:
    """The digest of a launch whose caller supplied none: what gets launched.

    It covers the capability, the provider and its launch config, and the payload. Launch
    credentials (``extra_env``) keep only their names in clear; their values enter through
    one HMAC-SHA256 keyed by SHA-256 of ``pitwall-lease-launch-v1`` and the RunPod API key,
    so a changed value is a different request and the stored digest is not an unkeyed hash
    of a credential.
    """
    document: dict[str, Any] = {
        "surface": "launch",
        "capability_id": _capability_id(capability),
        "provider_id": _provider_id(provider),
        "provider_config": dict(_provider_config(provider)),
        "payload": dict(payload or {}),
    }
    if extra_env:
        key = api_key or resolve_runpod_api_key(os.environ)[0]
        if not key:
            raise LaunchConfigError("an idempotent launch with credentials needs a RunPod API key")
        digest_key = hashlib.sha256(_LAUNCH_CREDENTIAL_KEY_CONTEXT + key.encode("utf-8")).digest()
        values = json.dumps(dict(extra_env), sort_keys=True, separators=(",", ":"))
        document["extra_env_keys"] = sorted(extra_env)
        document["extra_env_hmac"] = hmac.new(
            digest_key, values.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    return launch_request_fingerprint(document)


async def replay_idempotent_launch(
    workload: Workload,
    *,
    lease_repo: LeaseRepository | Any,
    idempotency_key: str,
    request_fingerprint: str,
    legacy_identity: tuple[str, str] | None = None,
) -> Lease:
    """Return the lease a keyed launch recorded, or refuse; never launch again.

    * A workload with a digest must carry this request's digest, else ``idempotency_mismatch``.
    * A workload without one was admitted before digests (or by another surface). It replays
      only when ``legacy_identity`` (capability id, provider id) names it: a RunPod lease
      workload of that capability and provider with no payload. Anything else is
      ``idempotency_mismatch``.
    * Its lease, in any state, is the replay: the lease billing the workload, or the lease a
      completed Lambda Cloud or Vast provision names in its result.
    * No lease while the workload is open: the first launch is in flight, or stopped before it
      recorded the lease and is left to the reconciler (``mutation_in_progress``; retry the
      same key later).
    * No lease and the workload closed: the launch ended without a lease (a failed or
      unknown-outcome create the reconciler settled). Launching again could leave a second
      pod, so the key is spent (``idempotency_conflict``; use a new key). A failure proven to
      have created no pod released the key instead, so its retry launches.
    """
    recorded = (workload.input or {}).get(LAUNCH_REQUEST_DIGEST_KEY)
    if isinstance(recorded, str):
        if not hmac.compare_digest(recorded, request_fingerprint):
            raise IdempotencyMismatch(workload.id)
    elif legacy_identity is None or (
        workload.capability_id,
        workload.provider_id,
        workload.type,
        workload.input,
    ) != (*legacy_identity, "inference", None):
        raise IdempotencyMismatch(workload.id)
    lease = await lease_repo.get_by_workload(workload.id)
    if lease is None:
        provisioned = (workload.result or {}).get("lease_id")
        if isinstance(provisioned, str) and provisioned:
            lease = await lease_repo.get(provisioned)
    if lease is not None:
        return cast(Lease, lease)
    state = workload.state.value if hasattr(workload.state, "value") else str(workload.state)
    if state in _LAUNCH_IN_FLIGHT_STATES:
        raise LeaseLaunchInProgress(workload.id)
    raise IdempotencyConflict(idempotency_key)


def estimate_raw_pod_lease_cost(ttl_minutes: int, max_cost_per_hour: Decimal | None) -> Decimal:
    """Estimate budget reservation for a raw RunPod pod lease.

    Mirrors the lease cost model: rate × TTL. Uses ``max_cost_per_hour``
    when supplied, otherwise a conservative $0.50/hr default so the gate
    still has a ceiling to check. Result is quantized to 6dp.
    """

    if ttl_minutes < 1:
        raise ValueError("ttl_minutes must be >= 1")
    rate = max_cost_per_hour if max_cost_per_hour is not None else Decimal("0.50")
    return (rate * Decimal(ttl_minutes) / Decimal(60)).quantize(Decimal("0.000001"))


def raw_pod_lease(
    *,
    pod_id: str,
    workload_id: str | None,
    ttl_minutes: int,
    max_cost_per_hour: Decimal | None,
    created_at: dt.datetime | None = None,
) -> Lease:
    """The lease row that puts a raw RunPod pod under TTL teardown and the lease sweep.

    ``pitwall_runpod_create_pod`` records it for the pod it created (or, after an
    unknown outcome, for the pod its error names), and the orphaned-workload reaper
    records it for an unknown-outcome pod carrying its attempt marker. ``created_at``
    defaults to now; the reaper passes the attempt's start so the TTL counts from the
    create.
    """
    start = created_at if created_at is not None else dt.datetime.now(dt.UTC)
    return Lease(
        id=f"lease_runpod_{uuid.uuid4().hex[:12]}",
        provider_id="runpod_direct",
        workload_id=workload_id,
        runpod_pod_id=pod_id,
        state=LeaseState.CREATING,
        created_at=start,
        expires_at=start + dt.timedelta(minutes=ttl_minutes),
        renewal_policy=LeaseRenewalPolicy.MANUAL,
        auto_teardown_on_expiry=True,
        max_usd_per_hour=(
            max_cost_per_hour.quantize(Decimal("0.0001")) if max_cost_per_hour is not None else None
        ),
    )


async def preview_raw_pod_lease_budget(
    pool: Any,
    *,
    ttl_minutes: int,
    max_cost_per_hour: Decimal | None,
    budget_gate: Any | None = None,
) -> dict[str, Any]:
    """What the raw-pod admission would decide for this TTL and rate, without reserving budget."""
    estimate = estimate_raw_pod_lease_cost(ttl_minutes, max_cost_per_hour)
    try:
        gate = budget_gate if budget_gate is not None else BudgetGate(pool)
    except BudgetNotConfigured as exc:
        # The apply would be refused the same way; say so instead of failing the preview.
        verdict: dict[str, Any] = {
            "admitted": False,
            "reason": BudgetNotConfigured.error_code,
            "snapshot": {"estimate_usd": str(estimate)},
            "remedy": exc.remedy,
        }
    else:
        verdict = (await gate.evaluate(estimate)).to_dict()
    verdict["estimate_basis"] = (
        "ttl_minutes x max_cost_per_hour"
        if max_cost_per_hour is not None
        else "ttl_minutes x 0.50 USD/hour default"
    )
    return verdict


async def admit_raw_pod_lease(
    pool: Any,
    *,
    ttl_minutes: int,
    max_cost_per_hour: Decimal | None = None,
    idempotency_key: str | None = None,
    budget_gate: Any | None = None,
) -> Any:
    """Admit a raw RunPod pod lease through the account budget gate.

    Thin wrapper over ``BudgetGate.try_launch_admission`` for the MCP
    direct-pod path so that ``pitwall.mcp.tools.runpod_resources`` does not
    import ``pitwall.cost`` directly (see hermetic MCP guard). The caller
    provides the TTL-derived estimate and receives the ``BudgetAdmission``
    (``workload_id`` / ``is_new``) that the lease row links to. ``BudgetRejected``
    propagates unchanged for the MCP error adapter to surface as the ``budget_rejected`` error code.
    """

    estimate = estimate_raw_pod_lease_cost(ttl_minutes, max_cost_per_hour)
    gate = budget_gate if budget_gate is not None else BudgetGate(pool)
    return await gate.try_launch_admission(
        capability_id="runpod_direct",
        provider_id="runpod_direct",
        estimate_usd=estimate,
        workload_type="inference",
        idempotency_key=idempotency_key,
    )


#: Nominal floor estimate for D6(b) non-pod resource-mutation gating.
#:
#: Endpoint/template/volume/registry-auth mutations (and pod update/action/
#: terminate) do not bill continuously, so no lease or workload row is
#: created for them — but an exhausted monthly budget must still block new
#: spend-adjacent writes. This floor keeps that check operative without
#: pretending to price the mutation.
RESOURCE_MUTATION_BUDGET_ESTIMATE_USD = Decimal("0.01")


async def admit_resource_mutation(
    pool: Any,
    *,
    budget_gate: Any | None = None,
) -> None:
    """Gate a non-pod RunPod resource mutation on budget availability.

    D6(b): thin wrapper over ``BudgetGate.check_available`` for the MCP
    direct-resource path so that ``pitwall.mcp.tools.runpod_resources`` does
    not import ``pitwall.cost`` directly (see hermetic MCP guard). Read-only
    check — no workload row is inserted (unlike the pod-lease path, there is
    no lease to link one to). Recording stays with the control-plane
    service's ``config_audit`` write. ``BudgetRejected`` propagates unchanged
    for the MCP error adapter to surface as the ``budget_rejected`` error code.
    """

    gate = budget_gate if budget_gate is not None else BudgetGate(pool)
    await gate.check_available(RESOURCE_MUTATION_BUDGET_ESTIMATE_USD)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def _set_provider_attach_hang_cooldown(
    pool: Any,
    provider: Provider | Any,
    *,
    now: dt.datetime | None = None,
) -> dt.datetime:
    cooldown_until = (now or _utc_now()) + ATTACH_HANG_PROVIDER_COOLDOWN
    if not hasattr(pool, "acquire"):
        log.warning(
            "provider attach-hang cooldown not persisted because pool is unavailable: "
            "provider=%s cooldown_until=%s",
            _provider_id(provider),
            cooldown_until.isoformat(),
        )
        return cooldown_until

    repo = ProviderRepository(pool)
    updated = await repo.patch(_provider_id(provider), cooldown_until=cooldown_until)
    if updated is None:
        log.warning(
            "provider attach-hang cooldown update found no provider row: provider=%s",
            _provider_id(provider),
        )
    return cooldown_until


def _make_pre_lease_persist_callback(
    *,
    pool: Any,
    loop: asyncio.AbstractEventLoop,
    lease_id: str,
    provider: Provider | Any,
    provider_id: str,
    workload_id: str,
    created_at: dt.datetime,
    expiry: dt.datetime,
    planned_endpoints: LeaseEndpoints | None,
) -> Callable[[dict[str, Any]], None]:
    """Build the ``pre_readiness_callback`` that persists the initial lease row.

    RunPod pod creation runs in a worker thread (``create_pod_with_fallback`` ->
    ``asyncio.to_thread(create_pod_with_fallback_sync, ...)``) and invokes this
    callback from inside that thread, before the readiness wait. Persisting here
    is leak-safety: a crash during the (long) readiness wait still leaves a DB
    record to reconcile and teardown the pod.

    ``pool`` (asyncpg) is bound to ``loop`` — the loop that owns its connections.
    The callback therefore schedules the persist coroutine back onto ``loop``
    rather than running it on a fresh loop, which would raise
    ``ConnectionDoesNotExistError`` (connection belongs to another loop).
    """

    policy, idle, cap = _lease_automation_fields(provider)

    def pre_lease_persist_callback(pod: dict[str, Any]) -> None:
        if not hasattr(pool, "acquire"):
            return
        pod_id = str(pod.get("id")) if pod.get("id") else None
        if not pod_id:
            return

        async def _persist_lease() -> None:
            initial_lease = Lease(
                id=lease_id,
                provider_id=provider_id,
                workload_id=workload_id,
                runpod_pod_id=pod_id,
                state=LeaseState.CREATING,
                created_at=created_at,
                expires_at=expiry,
                renewal_policy=policy,
                idle_timeout_min=idle,
                max_usd_per_hour=cap,
                auto_teardown_on_expiry=True,
                endpoints=planned_endpoints,
            )
            await _upsert_initial_lease(pool, initial_lease)

        # The callback fires from a worker thread (create_pod_with_fallback_sync
        # under asyncio.to_thread). asyncio.run() here would spin up a fresh loop
        # and touch a pool bound to ``loop``, raising ConnectionDoesNotExistError.
        # Schedule the persist back onto the owning loop and block until it lands.
        future = asyncio.run_coroutine_threadsafe(_persist_lease(), loop)
        future.result()

    return pre_lease_persist_callback


def _pod_terminator(api_key: str | None, rest_api_url: str | None) -> Callable[[str], None]:
    """Terminate a pod with the credential and REST URL its create used."""
    if api_key is None and rest_api_url is None:
        return terminate_pod_sync
    return functools.partial(
        _terminate_pod_sync_for_auth, api_key=api_key, rest_api_url=rest_api_url
    )


async def _abandon_on_failure(
    *,
    pool: Any,
    lease_id: str,
    pod_id: str | None,
    terminate: Callable[[str], None],
    operation: Callable[[], Awaitable[None]],
) -> None:
    """Run ``operation``; on any failure terminate the pod it created and re-raise.

    A launch that has already created a pod owns that pod. Returning an error to the
    caller while the pod keeps billing is the worst available outcome, so every failure
    after pod creation must hand the pod back before propagating.
    """
    try:
        await operation()
    except BaseException as exc:
        if pod_id is not None:
            try:
                await _await_cleanup(asyncio.to_thread(terminate, pod_id))
                log.warning("terminated pod %s after a failed launch of lease %s", pod_id, lease_id)
            except Exception:  # reason: cleanup must never mask the original failure
                log.exception("could not terminate pod %s after a failed launch", pod_id)
        try:
            await _await_cleanup(_mark_lease_failed(pool, lease_id=lease_id, error=exc))
        except Exception:  # reason: cleanup must never mask the original failure
            log.exception("could not mark lease %s failed after a failed launch", lease_id)
        raise


def _launch_failure_payload(exc: BaseException) -> dict[str, str]:
    return {"type": type(exc).__name__, "message": redact_text(exc)}


async def _await_cleanup(awaitable: Awaitable[Any]) -> Any:
    """Finish an owned cleanup operation even when the launch was cancelled."""

    task = asyncio.ensure_future(awaitable)
    while not task.done():
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # ``shield`` leaves the owned task alive.  Keep waiting, but stop
            # looping once the task itself is done (including cancellation).
            continue
    if task.cancelled():
        # A cleanup coroutine that was already cancelled must not make this
        # helper spin forever.  The launch's original failure remains the
        # authoritative error, so consume this cleanup cancellation.
        return None
    result = task.result()
    # The caller's cancellation is intentionally consumed while owned cleanup
    # completes; the surrounding launch path re-raises its original error.
    return result


async def _mark_workload_failed(
    pool: Any,
    *,
    workload_id: str,
    error: BaseException,
    cost_actual_usd: Decimal | None,
    provenance: str,
) -> bool:
    """Terminalize an admitted launch workload when no lease owns it."""

    if not hasattr(pool, "acquire"):
        return False
    now = dt.datetime.now(dt.UTC)
    patch: dict[str, Any] = {
        "completed_at": now,
        "error": _launch_failure_payload(error),
    }
    if cost_actual_usd is not None:
        patch.update(
            {
                "cost_actual_usd": cost_actual_usd,
                "cost_actual_provenance": provenance,
                "cost_reconciled_at": now,
            }
        )
    transitioned = await WorkloadRepository(pool).guarded_transition(
        workload_id,
        {"queued", "running"},
        "failed",
        patch=patch,
    )
    return transitioned is not None


async def _mark_lease_failed(
    pool: Any,
    *,
    lease_id: str,
    error: BaseException | None = None,
) -> Literal["marked", "terminal", "missing"]:
    if not hasattr(pool, "acquire"):
        return "missing"
    now = dt.datetime.now(dt.UTC)
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "UPDATE pitwall.leases SET state = 'failed', terminated_at = $2, "
            "terminated_reason = 'launch_failed_after_create' WHERE id = $1 "
            "AND state NOT IN ('stopped', 'failed', 'expired') "
            "RETURNING workload_id, cost_accrued_usd",
            lease_id,
            now,
        )
        if row is None:
            existing = await conn.fetchrow(
                "SELECT state FROM pitwall.leases WHERE id = $1 FOR UPDATE", lease_id
            )
            if existing is None:
                return "missing"
            if existing["state"] in {"stopped", "failed", "expired"}:
                return "terminal"
            return "missing"
        workload_id = row.get("workload_id")
        if workload_id is not None:
            error_payload = _launch_failure_payload(error) if error is not None else None
            await conn.execute(
                """
                UPDATE pitwall.workloads
                SET state = 'failed',
                    completed_at = COALESCE(completed_at, $2),
                    error = COALESCE($3::jsonb, error),
                    cost_actual_usd = COALESCE(cost_actual_usd, $4),
                    cost_actual_provenance = CASE
                        WHEN cost_actual_usd IS NULL AND $4 IS NOT NULL
                            THEN 'lease_launch_failure'
                        ELSE cost_actual_provenance
                    END,
                    cost_reconciled_at = CASE
                        WHEN cost_actual_usd IS NULL AND $4 IS NOT NULL
                            THEN $2
                        ELSE cost_reconciled_at
                    END
                WHERE id = $1
                  AND state IN ('queued', 'running')
                """,
                workload_id,
                now,
                json.dumps(error_payload) if error_payload is not None else None,
                row.get("cost_accrued_usd"),
            )
    return "marked"


async def _terminalize_failed_launch(
    pool: Any,
    *,
    lease_id: str,
    workload_id: str,
    error: BaseException,
    cost_actual_usd: Decimal | None = None,
    provenance: str = "lease_launch_failure_cost_unknown",
) -> None:
    """Close a failed launch while preserving unknown post-creation cost.

    A failure proven to have created no pod (``lease_launch_precreation_failure`` at $0: a
    template or plan failure, or no capacity before any pod attempt) also releases the
    workload's idempotency key, as the raw-pod path does, so a same-key retry admits and
    launches again instead of being refused.
    """

    try:
        lease_outcome = await _mark_lease_failed(pool, lease_id=lease_id, error=error)
        if lease_outcome == "missing":
            closed = await _mark_workload_failed(
                pool,
                workload_id=workload_id,
                error=error,
                cost_actual_usd=cost_actual_usd,
                provenance=provenance,
            )
            if closed and provenance == _PRECREATION_PROVENANCE and cost_actual_usd == 0:
                await WorkloadRepository(pool).fail_and_release_idempotency_key(
                    workload_id,
                    cost_actual_provenance=provenance,
                    cost_reconciled_at=dt.datetime.now(dt.UTC),
                )
    except Exception:  # reason: preserve the original launch/fallback result
        log.exception("could not terminalize failed launch workload %s", workload_id)


async def _run_launch_runpod_guard(
    provider: Provider | Any,
    capability: Capability,
    *,
    request_id: str | None,
    extra_env: Mapping[str, str] | None,
    payload: Mapping[str, Any] | None,
    idempotency_key: str | None,
    dry_run: bool,
    pre_spend_inspected: bool,
) -> tuple[Provider | Any, Mapping[str, Any] | None, Mapping[str, str] | None]:
    """Reject unauthenticated registry launches and run the pre-spend payload inspection."""
    if (
        not dry_run
        and isinstance(provider, Provider)
        and _provider_config(provider).get("endpoint_auth") is True
        and not (extra_env or {}).get("PITWALL_ENDPOINT_KEY", "").strip()
    ):
        raise InvalidProviderConfig(
            "PITWALL_ENDPOINT_KEY is required for authenticated registry pod launches"
        )
    if not pre_spend_inspected:
        provider, guarded_inputs = _guard_lease_launch_inputs(
            capability=capability,
            provider=provider,
            payload=payload,
            extra_env=extra_env,
            request_id=request_id,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
        )
        payload = guarded_inputs.get("payload")
        extra_env = guarded_inputs.get("extra_env")
    return provider, payload, extra_env


async def _run_launch_runpod_prepare(
    pool: Any,
    capability: Capability,
    provider: Provider | Any,
    *,
    workload_id: str | None,
    dry_run: bool,
    request_id: str | None,
    extra_env: Mapping[str, str] | None,
    api_key: str | None,
    graphql_url: str | None,
    rest_api_url: str | None,
) -> Any:
    """Prepare the template and launch plan; a failure closes the admitted workload."""
    try:
        return await prepare_lease_launch(
            pool,
            capability,
            provider,
            dry_run=dry_run,
            request_id=request_id,
            extra_env=extra_env,
            api_key=api_key,
            graphql_url=graphql_url,
            rest_api_url=rest_api_url,
        )
    except BaseException as exc:
        if workload_id is not None:
            await _await_cleanup(
                _terminalize_failed_launch(
                    pool,
                    lease_id=_lease_id_for_launch(provider),
                    workload_id=workload_id,
                    error=exc,
                    cost_actual_usd=Decimal("0"),
                    provenance=_PRECREATION_PROVENANCE,
                )
            )
        raise


def _run_launch_runpod_response(
    capability: Capability,
    provider: Provider | Any,
    plan: Any,
    *,
    workload_id: str | None,
    dry_run: bool,
) -> dict[str, Any]:
    return {
        "backend": "runpod",
        "dry_run": dry_run,
        "capability_id": _capability_id(capability),
        "capability": _capability_name(capability),
        "provider_id": _provider_id(provider),
        "provider": _provider_name(provider),
        "external_resource_id": None,
        "workload_id": workload_id,
        "template_id": plan.template.template_id,
        "template_name": plan.template.template_name,
        "image_ref": plan.template.image_ref,
        "network_volume_id": plan.network_volume_id,
        "data_center_id": plan.data_center_id,
    }


async def _run_launch_runpod_create_pod(
    pool: Any,
    provider: Provider | Any,
    plan: Any,
    *,
    lease_id: str,
    workload_id: str,
    created_at: dt.datetime,
    api_key: str | None,
    rest_api_url: str | None,
    rest_v1_api_url: str | None,
) -> dict[str, Any]:
    """Create the pod, persisting the lease before readiness so a crash cannot orphan it."""
    expiry = _expiry_for_lease(provider, created_at)
    planned_endpoints = _planned_endpoints_for_provider(provider)
    pre_lease_persist_callback = _make_pre_lease_persist_callback(
        pool=pool,
        loop=asyncio.get_running_loop(),
        lease_id=lease_id,
        provider=provider,
        provider_id=_provider_id(provider),
        workload_id=workload_id,
        created_at=created_at,
        expiry=expiry,
        planned_endpoints=planned_endpoints,
    )
    pod_name = f"pitwall-{_provider_name(provider)}-{plan.template.template_id[:8]}"
    create_kwargs: dict[str, Any] = {
        "name": pod_name,
        "template_id": plan.template.template_id,
        "image_name": plan.template.image_ref,
        "workload": plan.workload,
        "env": plan.env,
        "network_volume_id": plan.network_volume_id,
        "data_center_id": plan.data_center_id,
        "max_cost_per_hr": _max_cost_per_hr(provider),
        "volume_attach_timeout_s": plan.volume_attach_timeout_s,
        "docker_entrypoint": plan.docker_entrypoint,
        "docker_start_cmd": plan.docker_start_cmd,
        "startup_timeout_s": plan.startup_timeout_s,
        "readiness_path": plan.readiness_path,
        "pre_readiness_callback": pre_lease_persist_callback,
    }
    if api_key is not None or rest_api_url is not None or rest_v1_api_url is not None:
        return await _create_pod_with_fallback(
            **create_kwargs,
            api_key=api_key,
            rest_api_url=rest_api_url,
            rest_v1_api_url=rest_v1_api_url,
        )
    return await create_pod_with_fallback(**create_kwargs)


def _fallback_response(response: dict[str, Any], reason: BaseException) -> dict[str, Any]:
    response.update(
        {
            "dry_run": False,
            "pod_id": None,
            "lease_id": None,
            "provider_fallback": True,
            "provider_fallback_reason": str(reason),
        }
    )
    return response


async def _run_launch_runpod_create_failed(
    pool: Any,
    provider: Provider | Any,
    exc: BaseException,
    *,
    lease_id: str,
    workload_id: str,
    response: dict[str, Any],
) -> dict[str, Any] | None:
    """Close the lease and workload after a failed create.

    Provider fallback and attach-hang recovery return the fallback response; for every
    other failure the cleanup runs and ``None`` tells the caller to re-raise.
    """
    if isinstance(exc, ProviderAttachHangRecoveryRequested):
        cooldown_until = await _set_provider_attach_hang_cooldown(pool, provider)
        await _await_cleanup(
            _terminalize_failed_launch(pool, lease_id=lease_id, workload_id=workload_id, error=exc)
        )
        log.warning(
            "pod lease provider attach hang recovered: provider=%s pod=%s "
            "attach_timeout_s=%s cooldown_until=%s reason=%s",
            _provider_id(provider),
            exc.pod_id,
            exc.attach_timeout_s,
            cooldown_until.isoformat(),
            exc,
        )
        fallback = _fallback_response(response, exc)
        fallback["provider_cooldown_until"] = cooldown_until.isoformat()
        return fallback
    if isinstance(exc, ProviderFallbackRequested):
        await _await_cleanup(
            _terminalize_failed_launch(pool, lease_id=lease_id, workload_id=workload_id, error=exc)
        )
        log.warning(
            "pod lease provider fallback requested: provider=%s reason=%s",
            _provider_id(provider),
            exc,
        )
        return _fallback_response(response, exc)
    cost_kwargs: dict[str, Any] = {}
    if isinstance(exc, NoCapacityError):
        attempted_paid_pod = getattr(exc, "pod_attempts", 0) > 0 or getattr(
            exc, "ambiguous_create", False
        )
        cost_kwargs = {
            "cost_actual_usd": None if attempted_paid_pod else Decimal("0"),
            "provenance": (
                "lease_launch_cost_unknown_after_pod_attempt"
                if attempted_paid_pod
                else _PRECREATION_PROVENANCE
            ),
        }
    await _await_cleanup(
        _terminalize_failed_launch(
            pool, lease_id=lease_id, workload_id=workload_id, error=exc, **cost_kwargs
        )
    )
    return None


async def _replay_runpod_launch(
    pool: Any,
    capability: Capability,
    provider: Provider | Any,
    *,
    workload_id: str,
    idempotency_key: str,
    request_fingerprint: str,
) -> dict[str, Any]:
    """The launch response for the lease an earlier same-key launch recorded."""
    workload = await WorkloadRepository(pool).get(workload_id)
    if workload is None:
        raise LeaseLaunchInProgress(workload_id)
    lease = await replay_idempotent_launch(
        workload,
        lease_repo=LeaseRepository(pool),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        legacy_identity=(_capability_id(capability), _provider_id(provider)),
    )
    pod_id = lease.runpod_pod_id or lease.external_resource_id
    log.info(
        "pod lease launch replayed: key hit on workload %s returns lease %s",
        workload.id,
        lease.id,
    )
    return {
        "backend": "runpod",
        "dry_run": False,
        "replayed": True,
        "capability_id": _capability_id(capability),
        "capability": _capability_name(capability),
        "provider_id": lease.provider_id,
        "provider": _provider_name(provider)
        if lease.provider_id == _provider_id(provider)
        else None,
        "external_resource_id": pod_id,
        "workload_id": workload.id,
        "pod_id": pod_id,
        "lease_id": lease.id,
    }


async def _run_launch_runpod_admit(
    pool: Any,
    capability: Capability,
    provider: Provider | Any,
    *,
    payload: Mapping[str, Any] | None,
    extra_env: Mapping[str, str] | None,
    budget_gate: Any | None,
    idempotency_key: str | None,
    request_fingerprint: str | None,
    api_key: str | None,
) -> tuple[str, dict[str, Any] | None]:
    """Admit the launch; on a key hit also return the replay, so nothing is launched."""
    await enforce_kill_switch_admission(pool)
    fingerprint: str | None = None
    if idempotency_key is not None:
        fingerprint = request_fingerprint or default_launch_fingerprint(
            capability, provider, payload=payload, extra_env=extra_env, api_key=api_key
        )
    admission = await admit_lease_launch(
        pool,
        capability,
        provider,
        budget_gate=budget_gate,
        payload=payload,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    if admission.is_new or idempotency_key is None or fingerprint is None:
        return admission.workload_id, None
    # A key hit: no template, no pod, no second budget reservation.
    replay = await _replay_runpod_launch(
        pool,
        capability,
        provider,
        workload_id=admission.workload_id,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
    )
    return admission.workload_id, replay


async def _run_launch_runpod(
    pool: Any,
    capability: Capability,
    provider: Provider | Any,
    *,
    request_id: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    payload: Mapping[str, Any] | None = None,
    budget_gate: Any | None = None,
    idempotency_key: str | None = None,
    dry_run: bool = False,
    api_key: str | None = None,
    graphql_url: str | None = None,
    rest_api_url: str | None = None,
    rest_v1_api_url: str | None = None,
    request_fingerprint: str | None = None,
    _pre_spend_inspected: bool = False,
) -> dict[str, Any]:
    provider, payload, extra_env = await _run_launch_runpod_guard(
        provider,
        capability,
        request_id=request_id,
        extra_env=extra_env,
        payload=payload,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
        pre_spend_inspected=_pre_spend_inspected,
    )
    if api_key is None:
        api_key = provider_runpod_api_key(provider)

    workload_id: str | None = None
    if not dry_run:
        workload_id, replay = await _run_launch_runpod_admit(
            pool,
            capability,
            provider,
            payload=payload,
            extra_env=extra_env,
            budget_gate=budget_gate,
            idempotency_key=idempotency_key,
            request_fingerprint=request_fingerprint,
            api_key=api_key,
        )
        if replay is not None:
            return replay

    plan = await _run_launch_runpod_prepare(
        pool,
        capability,
        provider,
        workload_id=workload_id,
        dry_run=dry_run,
        request_id=request_id,
        extra_env=extra_env,
        api_key=api_key,
        graphql_url=graphql_url,
        rest_api_url=rest_api_url,
    )
    response = _run_launch_runpod_response(
        capability, provider, plan, workload_id=workload_id, dry_run=dry_run
    )
    if dry_run or workload_id is None:
        response["pod_id"] = None
        return response

    lease_id = _lease_id_for_launch(provider)
    try:
        ready_pod = await _run_launch_runpod_create_pod(
            pool,
            provider,
            plan,
            lease_id=lease_id,
            workload_id=workload_id,
            created_at=dt.datetime.now(dt.UTC),
            api_key=api_key,
            rest_api_url=rest_api_url,
            rest_v1_api_url=rest_v1_api_url,
        )
    except BaseException as exc:
        fallback = await _run_launch_runpod_create_failed(
            pool, provider, exc, lease_id=lease_id, workload_id=workload_id, response=response
        )
        if fallback is None:
            raise
        return fallback

    pod_id = str(ready_pod.get("id")) if ready_pod.get("id") else None

    async def _finish() -> None:
        await _persist_ready_lease(pool, lease_id=lease_id, ready_pod=ready_pod)
        if pod_id is not None:
            await arm_serve_provider(pool, provider=provider, lease_id=lease_id, pod_id=pod_id)

    await _abandon_on_failure(
        pool=pool,
        lease_id=lease_id,
        pod_id=pod_id,
        terminate=_pod_terminator(api_key, rest_api_url),
        operation=_finish,
    )
    response.update(
        {
            "dry_run": False,
            "external_resource_id": pod_id,
            "pod_id": pod_id,
            "pod_name": ready_pod.get("name"),
            "lease_id": lease_id,
        }
    )
    return response


async def run_launch(
    *,
    pool: Any,
    capability: Capability,
    provider: Provider | Any,
    request_id: str | None = None,
    extra_env: Mapping[str, str] | None = None,
    payload: Mapping[str, Any] | None = None,
    budget_gate: Any | None = None,
    idempotency_key: str | None = None,
    dry_run: bool = False,
    api_key: str | None = None,
    graphql_url: str | None = None,
    rest_api_url: str | None = None,
    rest_v1_api_url: str | None = None,
    request_fingerprint: str | None = None,
    _pre_spend_inspected: bool = False,
) -> dict[str, Any]:
    """Launch or dry-run compute through the resolved provider adapter.

    A repeated ``idempotency_key`` never launches again: a RunPod launch whose key already
    admitted a workload returns that workload's lease (``replayed: true``) or refuses
    (``replay_idempotent_launch``). ``request_fingerprint`` is the caller's digest of its
    request; without one the launch inputs decide (``default_launch_fingerprint``).
    """

    provider, guarded_inputs = _guard_lease_launch_inputs(
        capability=capability,
        provider=provider,
        payload=payload,
        extra_env=extra_env,
        request_id=request_id,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
        record=not _pre_spend_inspected,
    )
    payload = guarded_inputs.get("payload")
    extra_env = guarded_inputs.get("extra_env")
    log.info(
        "pod lease launch requested: capability=%s provider=%s dry_run=%s",
        _capability_name(capability),
        _provider_name(provider),
        dry_run,
    )
    if isinstance(provider, Provider) and provider.adapter_id is not ProviderAdapterId.RUNPOD:
        return await _run_launch_registered_compute(
            pool=pool,
            capability=capability,
            provider=provider,
            request_id=request_id,
            extra_env=extra_env,
            payload=payload,
            budget_gate=budget_gate,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
            _pre_spend_inspected=True,
            request_fingerprint=request_fingerprint,
        )

    return await _run_launch_runpod(
        pool,
        capability,
        provider,
        request_id=request_id,
        extra_env=extra_env,
        payload=payload,
        budget_gate=budget_gate,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
        api_key=api_key,
        graphql_url=graphql_url,
        rest_api_url=rest_api_url,
        rest_v1_api_url=rest_v1_api_url,
        request_fingerprint=request_fingerprint,
        _pre_spend_inspected=True,
    )


async def _run_launch_registered_compute(
    *,
    pool: Any,
    capability: Capability,
    provider: Provider,
    request_id: str | None,
    extra_env: Mapping[str, str] | None,
    payload: Mapping[str, Any] | None,
    budget_gate: Any | None,
    idempotency_key: str | None,
    dry_run: bool,
    _pre_spend_inspected: bool,
    request_fingerprint: str | None = None,
) -> dict[str, Any]:
    """Apply common guard/budget inputs and invoke one non-RunPod compute adapter.

    A same-key replay the adapter refuses is a typed launch error: in progress is
    ``mutation_in_progress``, an earlier failure ``idempotency_conflict``, and a different
    request ``idempotency_mismatch``.
    """

    if not _pre_spend_inspected:
        provider, guarded_inputs = _guard_lease_launch_inputs(
            capability=capability,
            provider=provider,
            payload=payload,
            extra_env=extra_env,
            request_id=request_id,
            idempotency_key=idempotency_key,
            dry_run=dry_run,
        )
        payload = guarded_inputs.get("payload")
        extra_env = guarded_inputs.get("extra_env")
    if not dry_run:
        await enforce_kill_switch_admission(pool)

    adapter = get_default_registry().lookup_compute(provider.adapter_id.value)
    gate = None if dry_run else (budget_gate if budget_gate is not None else BudgetGate(pool))
    try:
        result = await adapter.provision(
            ProvisionRequest(
                context=ProviderOperationContext(pool=pool),
                capability=capability,
                provider_record=provider,
                credentials=CredentialReference(provider.credential_ref),
                request_id=request_id,
                extra_env=extra_env,
                payload=dict(payload or {}),
                budget_gate=gate,
                idempotency_key=idempotency_key,
                dry_run=dry_run,
                request_fingerprint=request_fingerprint,
            )
        )
    except ProvisionReplayInProgress as exc:
        raise LeaseLaunchInProgress(exc.workload_id) from exc
    except ProvisionReplayFailed as exc:
        raise IdempotencyConflict(idempotency_key or "") from exc
    except ProvisionReplayConflict as exc:
        raise IdempotencyMismatch(exc.workload_id) from exc
    response = dict(result.raw)
    response.update(
        {
            "backend": provider.adapter_id.value,
            "dry_run": dry_run,
            "capability_id": capability.id,
            "capability": capability.name,
            "provider_id": provider.id,
            "provider": provider.name,
            "external_resource_id": result.external_id,
            "pod_id": None,
            "lease_id": result.lease_id,
        }
    )
    return response


def _guard_lease_launch_inputs(
    *,
    capability: Capability | Any | None = None,
    provider: Provider | Any,
    payload: Mapping[str, Any] | None,
    extra_env: Mapping[str, str] | None = None,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    dry_run: bool = False,
    record: bool = True,
) -> tuple[Provider | Any, dict[str, Any]]:
    candidate: dict[str, Any] = {}
    launch_credentials: dict[str, str] = {}
    if payload:
        candidate["payload"] = dict(payload)
    if extra_env:
        launch_credentials = {
            key: value
            for key, value in extra_env.items()
            if key in {"HF_TOKEN", "PITWALL_ENDPOINT_KEY"}
        }
        inspectable_extra_env = {
            key: value
            for key, value in extra_env.items()
            if key not in {"HF_TOKEN", "PITWALL_ENDPOINT_KEY"}
        }
        if inspectable_extra_env:
            candidate["extra_env"] = inspectable_extra_env
    if capability is not None:
        candidate["capability"] = {
            "id": _capability_id(capability),
            "name": _capability_name(capability),
            "class": _capability_class(capability),
        }
    provider_identity: dict[str, Any] = {
        "id": _provider_id(provider),
        "name": _provider_name(provider),
        "type": _provider_type_value(provider),
    }
    for field_name in ("region", "cloud_type"):
        value = getattr(provider, field_name, None)
        if value is not None:
            provider_identity[field_name] = value
    candidate["provider"] = provider_identity
    if request_id is not None:
        candidate["request_id"] = request_id
    if idempotency_key is not None:
        candidate["idempotency_key"] = idempotency_key
    provider_config = dict(_provider_config(provider))
    if provider_config:
        # Provider configuration supplies image, argv, placement, volume, port,
        # and environment values to the eventual adapter.  Normalize JSON
        # scalars such as Decimal without invoking Provider's output redaction
        # serializer; the inspection must see the actual egress value.
        candidate["provider_config"] = _mask_provider_credential_references(
            _JSON_OBJECT_ADAPTER.dump_python(
                provider_config,
                mode="json",
            )
        )
    if not candidate:
        return provider, ({"extra_env": dict(extra_env)} if extra_env else {})

    inspection_service = get_pre_spend_inspection_service()
    inspect = inspection_service.preview if dry_run or not record else inspection_service.inspect
    guardrail = inspect(
        candidate,
        validate_redacted=lambda value: _require_lease_launch_inputs(
            value,
            provider=provider,
        ),
    )
    if guardrail.decision == PreSpendDecision.BLOCK:
        raise PreSpendPayloadRejected(
            decision=guardrail.decision.value,
            findings=[finding.to_dict() for finding in guardrail.findings],
        )
    if guardrail.decision == PreSpendDecision.REDACT:
        guarded = _require_lease_launch_inputs(
            guardrail.redacted_payload,
            provider=provider,
        )
        for identity_field in (
            "capability",
            "provider",
            "request_id",
            "idempotency_key",
        ):
            if guarded.pop(identity_field, None) != candidate.get(identity_field):
                raise PreSpendPayloadRejected(
                    decision=guardrail.decision.value,
                    findings=[finding.to_dict() for finding in guardrail.findings],
                )
        guarded_config = guarded.pop("provider_config", None)
        if guarded_config is not None:
            original_normalized = candidate["provider_config"]
            assert isinstance(original_normalized, dict)
            original_non_env = {
                key: value for key, value in original_normalized.items() if key != "env_vars"
            }
            guarded_non_env = {
                key: value for key, value in guarded_config.items() if key != "env_vars"
            }
            # Rewriting an image, argv, resource identity, or placement value
            # could change provider intent.  Only a typed environment mapping
            # has a schema-preserving redaction path here.
            if original_non_env != guarded_non_env:
                raise PreSpendPayloadRejected(
                    decision=guardrail.decision.value,
                    findings=[finding.to_dict() for finding in guardrail.findings],
                )
            original_env = original_normalized.get("env_vars")
            guarded_env = guarded_config.get("env_vars")
            if guarded_env != original_env:
                if not isinstance(provider, Provider):
                    raise PreSpendPayloadRejected(
                        decision=guardrail.decision.value,
                        findings=[finding.to_dict() for finding in guardrail.findings],
                    )
                provider = provider.model_copy(
                    update={
                        "config": {
                            **provider.config,
                            "env_vars": guarded_env,
                        }
                    }
                )
        if launch_credentials:
            guarded["extra_env"] = {
                **guarded.get("extra_env", {}),
                **launch_credentials,
            }
        return provider, guarded
    if launch_credentials:
        candidate["extra_env"] = {
            **candidate.get("extra_env", {}),
            **launch_credentials,
        }
    return provider, {
        key: value for key, value in candidate.items() if key in {"payload", "extra_env"}
    }


def _mask_provider_credential_references(value: Any) -> Any:
    """Exclude validated reference names, never adjacent provider metadata."""
    if isinstance(value, dict):
        masked: dict[str, Any] = {}
        for key, item in value.items():
            normalized = (
                _CONFIG_KEY_SEPARATOR_RE.sub(
                    "_",
                    _CONFIG_CAMEL_BOUNDARY_RE.sub("_", key.strip()),
                )
                .strip("_")
                .casefold()
            )
            is_reference = any(
                normalized.endswith(suffix)
                and any(marker in normalized[: -len(suffix)] for marker in _CREDENTIAL_KEY_MARKERS)
                for suffix in _CREDENTIAL_REFERENCE_SUFFIXES
            )
            if is_reference and isinstance(item, str) and _ENV_KEY_RE.fullmatch(item):
                masked[key] = ""
            else:
                masked[key] = _mask_provider_credential_references(item)
        return masked
    if isinstance(value, list):
        return [_mask_provider_credential_references(item) for item in value]
    return value


def _require_lease_launch_inputs(
    value: object,
    *,
    provider: Provider | Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("lease launch inputs must be a JSON object")
    payload = value.get("payload")
    provider_config = value.get("provider_config")
    extra_env = value.get("extra_env")
    if payload is not None and not isinstance(payload, dict):
        raise ValueError("lease launch payload must be a JSON object")
    if provider_config is not None and not isinstance(provider_config, dict):
        raise ValueError("lease provider configuration must be a JSON object")
    configured_env = provider_config.get("env_vars") if isinstance(provider_config, dict) else None
    if configured_env is not None and (
        not isinstance(configured_env, dict)
        or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in configured_env.items()
        )
    ):
        raise ValueError("lease launch environment must be a string mapping")
    if extra_env is not None and (
        not isinstance(extra_env, dict)
        or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in extra_env.items()
        )
    ):
        raise ValueError("extra lease environment must be a string mapping")
    return value


@dataclass(frozen=True)
class RoutedLease:
    """A lease launched (or previewed) through the production planner.

    A replay (``replayed``) carries the plan the first launch recorded, which may still be
    unset while that launch is finishing, and no provider snapshot.
    """

    capability: Capability
    provider: Provider | None
    route_plan: Any
    launch_result: dict[str, Any]
    lease: Lease | None
    replayed: bool = False

    def route_plan_fields(self) -> dict[str, Any]:
        """``route_plan_id`` and ``route_plan`` for the lease response."""
        if self.route_plan is None:
            return {"route_plan_id": None, "route_plan": None}
        return {"route_plan_id": self.route_plan.plan_id, "route_plan": self.route_plan.to_dict()}


def _recorded_route_plan(workload: Workload | None) -> Any:
    if workload is None or workload.route_plan_id is None or workload.route_plan is None:
        return None
    from pitwall.routing.production import PersistedRoutePlan

    return PersistedRoutePlan(plan_id=workload.route_plan_id, document=workload.route_plan)


async def create_routed_lease(
    *,
    pool: Any,
    capability_repo: CapabilityRepository | Any,
    provider_repo: ProviderRepository | Any,
    lease_repo: LeaseRepository | Any,
    workload_repo: WorkloadRepository | Any,
    routing_service: Any,
    capability_ref: str,
    provider_id: str | None,
    idempotency_key: str | None,
    dry_run: bool,
) -> RoutedLease:
    """The one lease-creation path REST and MCP share.

    The capability resolves by public name, then registry id. The production planner picks
    the provider (a preview for a dry run), the launch runs, and a launched lease's workload
    records the plan. Nothing launches when the capability or selected provider is missing.
    """
    from pitwall.api.routes.routing import _api_error
    from pitwall.resolver.exceptions import ResolverError
    from pitwall.routing.production import RoutePlanningError, RoutingOperation

    capability = await capability_repo.get_by_name(capability_ref)
    if capability is None:
        capability = await capability_repo.get(capability_ref)
    if capability is None:
        raise CapabilityNotFound(capability_ref)

    fingerprint = (
        routed_lease_fingerprint(capability.id, provider_id)
        if idempotency_key is not None
        else None
    )
    if idempotency_key is not None and fingerprint is not None and not dry_run:
        existing = await workload_repo.get_by_idempotency_key(idempotency_key)
        # A workload admitted before digests falls through to the launch path's own replay.
        if existing is not None and (existing.input or {}).get(LAUNCH_REQUEST_DIGEST_KEY):
            # A repeated key: no planning, no launch, no plan rewrite.
            replayed_lease = await replay_idempotent_launch(
                existing,
                lease_repo=lease_repo,
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
            )
            return RoutedLease(
                capability,
                None,
                _recorded_route_plan(existing),
                {"replayed": True, "lease_id": replayed_lease.id, "workload_id": existing.id},
                replayed_lease,
                replayed=True,
            )

    try:
        if dry_run:
            route_plan = await routing_service.preview(
                capability_id=capability_ref,
                payload={},
                operation=RoutingOperation.COMPUTE,
                provider_id=provider_id,
            )
        else:
            route_plan, _safe_payload = await routing_service.plan_execution(
                capability_id=capability_ref,
                payload={},
                operation=RoutingOperation.COMPUTE,
                provider_id=provider_id,
            )
    except (ResolverError, RoutePlanningError) as exc:
        # The same typed codes the routing routes return; never an unhandled 500.
        raise _api_error(exc, capability_ref) from exc
    provider = await provider_repo.get(route_plan.selected_provider_id)
    if provider is None:
        raise ProviderNotFound(route_plan.selected_provider_id)

    result = await run_launch(
        pool=pool,
        capability=capability,
        provider=provider,
        idempotency_key=idempotency_key,
        dry_run=dry_run,
        request_fingerprint=fingerprint,
    )
    if dry_run:
        return RoutedLease(capability, provider, route_plan, result, None)

    lease_id = result.get("lease_id")
    lease = await lease_repo.get(lease_id) if lease_id else None
    if lease is None:
        raise RuntimeError(f"lease launch persisted no lease row (lease_id={lease_id!r})")
    # Every lease launched now links its workload. A Lambda Cloud or Vast lease provisioned
    # before leases were linked has none; the provision result names its workload instead.
    workload_id = lease.workload_id or _optional_str(result.get("workload_id"))
    if workload_id is None:
        raise RuntimeError("lease launch persisted no linked workload")
    if result.get("replayed") is True or result.get("idempotent_replay") is True:
        # A same-key launch's lease (a lost admission race, or an adapter replay): its plan.
        recorded = await workload_repo.get(workload_id)
        return RoutedLease(
            capability, provider, _recorded_route_plan(recorded), result, lease, replayed=True
        )
    await workload_repo.attach_route_plan(
        workload_id,
        route_plan_id=route_plan.plan_id,
        route_plan=route_plan.to_dict(),
    )
    return RoutedLease(capability, provider, route_plan, result, lease)


__all__ = [
    "InvalidProviderConfig",
    "RoutedLease",
    "create_routed_lease",
    "LaunchConfigError",
    "LaunchTemplate",
    "LeaseLaunchPlan",
    "ProviderNotPodLease",
    "TemplateImageNotConfigured",
    "LAUNCH_REQUEST_DIGEST_KEY",
    "admit_lease_launch",
    "arm_serve_provider",
    "default_launch_fingerprint",
    "launch_request_fingerprint",
    "replay_idempotent_launch",
    "routed_lease_fingerprint",
    "ensure_launch_template",
    "estimate_lease_launch_cost",
    "prepare_lease_launch",
    "run_launch",
]
