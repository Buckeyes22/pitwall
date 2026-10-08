"""RunPod Pod lifecycle client.

Pod create/list/find/delete uses RunPod's documented REST API. The Python SDK's
pod create helper builds GraphQL strings and only exposes ``dockerArgs``; REST
accepts structured JSON including ``dockerEntrypoint`` and ``dockerStartCmd``, which
is safer for command-heavy prewarm jobs and matches the current docs.

"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import os
import shlex
import sys
import time
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Required, TypedDict, Unpack, cast

import anyio
import httpx
from pydantic import BaseModel, ConfigDict, Field

from pitwall.runpod_client.registry import registry_auth_id_from_env
from pitwall.runpod_client.workloads import WorkloadConfig
from pitwall.runpod_credentials import (
    DEFAULT_RUNPOD_REST_URL,
    MISSING_CREDENTIAL_MESSAGE,
    resolve_runpod_api_key,
)
from pitwall.security.redaction import redact_text

log = logging.getLogger("pitwall.runpod_client.pods")

CAPACITY_ERROR_SUBSTRINGS_ENV = "PITWALL_RUNPOD_CAPACITY_ERROR_SUBSTRINGS"
VOLUME_ATTACH_TIMEOUT_ENV = "PITWALL_VOLUME_ATTACH_TIMEOUT_S"
DEFAULT_VOLUME_ATTACH_TIMEOUT_S = 300.0
TRANSIENT_CREATE_RETRIES = 1
TRANSIENT_CREATE_BACKOFF_S = 1.0
DEFAULT_CAPACITY_ERROR_SUBSTRINGS = (
    "no longer any instances available",
    "resourcesunavailable",
    "no instances",
    "insufficient capacity",
    "does not have the resources",
)

#: Phrases that positively identify exhausted stock rather than a server fault.
CAPACITY_PHRASES = (
    "no longer any instances available",
    "resourcesunavailable",
    "no instances",
    "does not have the resources",
    "insufficient capacity",
)

#: Phrases that identify a request that will never succeed as written.
PERMANENT_PHRASES = (
    "not found",
    "does not support",
    "not in input schema",
    "invalid",
)

CapacityErrorMatcher = Callable[[Exception], bool]
PROXY_PROBE_METHOD = "runpod_proxy"
SSH_LOCALHOST_PROBE_METHOD = "ssh_localhost"
POD_READINESS_PROBE_ORDER = (SSH_LOCALHOST_PROBE_METHOD, PROXY_PROBE_METHOD)
DEFAULT_POD_PROBE_TIMEOUT_S = 5.0
DEFAULT_READINESS_PATHS = ("/health", "/status.json", "/")
POD_LOG_DIAGNOSTIC_BYTES = 16 * 1024
POD_LOG_DIAGNOSTIC_TIMEOUT_S = 5.0

# REST v1 pod creation validates this field against a closed enum. The v2
# catalogue can advertise newer driver versions before v1 accepts them.
LEGACY_V1_ALLOWED_CUDA_VERSIONS = frozenset(
    {
        "13.0",
        "12.9",
        "12.8",
        "12.7",
        "12.6",
        "12.5",
        "12.4",
        "12.3",
        "12.2",
        "12.1",
        "12.0",
        "11.8",
    }
)


def _readiness_paths(readiness_path: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((readiness_path, *DEFAULT_READINESS_PATHS)))


@dataclass
class PodProbeResult:
    """Structured result from a pod readiness probe.

    Attributes:
        healthy: True if the probe passed (2xx response).
        method: The probe method used (e.g., "ssh_localhost", "runpod_proxy").
        status_code: HTTP status code if available, None on timeout/connection error.
        error: Error type string if probe failed (e.g., "timeout", "524", "connection_error").
        latency_ms: Observed latency in milliseconds, None if request failed.
    """

    healthy: bool
    method: str
    status_code: int | None = None
    error: str | None = None
    latency_ms: float | None = None


class RunPodError(RuntimeError):
    """Any RunPod API failure that burst_api should surface as 5xx."""


class NoCapacityError(RunPodError):
    """All GPU types in the workload were exhausted (ResourcesUnavailable)."""

    def __init__(
        self,
        message: str,
        *,
        pod_attempts: int = 0,
        ambiguous_create: bool = False,
    ) -> None:
        super().__init__(message)
        # A capacity error can be the final result after an earlier paid pod
        # was created and deleted by a pre-readiness guard.  Callers must not
        # infer zero spend from the exception class alone.
        self.pod_attempts = pod_attempts
        # A legacy create can time out or return a malformed success before a
        # later target reports capacity.  That outcome cannot prove that no
        # billable resource was created.
        self.ambiguous_create = ambiguous_create


class ProviderFallbackRequested(NoCapacityError):
    """The current provider should be skipped after a paid pod pre-wait guard."""


class PodStartupTimeout(RunPodError):
    """RunPod accepted a pod, but it never reached a running runtime state."""


class ContainerStartFailure(RunPodError):
    """The pod exists and bills, but its container cannot start."""


#: How long a pod may report RUNNING with a zero uptime before we call it dead.
CONTAINER_START_GRACE_S = 180.0


class PodVolumeAttachTimeout(PodStartupTimeout):
    """RunPod accepted a volume-attached pod, but the volume attach stayed hung."""

    def __init__(self, pod_id: str, attach_timeout_s: float) -> None:
        super().__init__(
            f"pod {pod_id} volume attach hang exceeded {attach_timeout_s:.0f}s (uptimeInSeconds=0)"
        )
        self.pod_id = pod_id
        self.attach_timeout_s = attach_timeout_s


class ProviderAttachHangRecoveryRequested(ProviderFallbackRequested):
    """The provider should cool down after a zero-uptime volume attach hang."""

    def __init__(self, message: str, *, pod_id: str, attach_timeout_s: float) -> None:
        super().__init__(message)
        self.pod_id = pod_id
        self.attach_timeout_s = attach_timeout_s


class PodStartupFailed(RunPodError):
    """RunPod accepted a pod, but the runtime reached a terminal failed state."""


class RunPodRestError(RunPodError):
    """RunPod REST API returned a non-2xx response."""

    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        super().__init__(f"{method} {path} failed with HTTP {status_code}: {body}")
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body


class V2PodGpuRequest(BaseModel):
    """Strict REST v2 GPU selection for one pod-create attempt."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1)
    count: int = Field(ge=1)
    allowed_cuda_versions: list[str] | None = Field(
        default=None,
        alias="allowedCudaVersions",
    )


class V2PodNetworkMount(BaseModel):
    """Strict REST v2 network-volume mount."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    volume_id: str = Field(alias="volumeId", min_length=1)
    path: str = Field(min_length=1)


class V2PodMounts(BaseModel):
    """Strict subset of REST v2 pod mounts used by Pitwall."""

    model_config = ConfigDict(extra="forbid")

    network: list[V2PodNetworkMount] | None = None


class V2CreatePodRequest(BaseModel):
    """Strict REST v2 pod-create body used by Pitwall."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    image: str = Field(min_length=1)
    cloud: Literal["COMMUNITY", "SECURE"]
    gpu: V2PodGpuRequest
    disk: int = Field(ge=10)
    env: dict[str, str] = Field(default_factory=dict)
    args: str | None = None
    mounts: V2PodMounts | None = None
    ports: list[str] | None = None
    registry: str | None = None
    data_center_ids: list[str] | None = Field(default=None, alias="dataCenterIds")
    template_id: str | None = Field(default=None, alias="templateId")
    start_jupyter: bool = Field(default=False, alias="startJupyter")
    start_ssh: bool = Field(default=False, alias="startSsh")


class V2PodResponse(BaseModel):
    """Validated minimum REST v2 pod response while preserving provider fields."""

    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)


class V2PodActionRequest(BaseModel):
    """Strict REST v2 unified pod lifecycle action."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["start", "stop", "restart", "terminate"]


class _RestAuthKwargs(TypedDict, total=False):
    api_key: str
    rest_api_url: str


class _SdkAuthKwargs(TypedDict, total=False):
    api_key: str


@dataclass
class _ReadinessSignals:
    runtime_seen_at: str | None = None
    port_mappings_seen_at: str | None = None
    probe_passed_at: str | None = None
    probe_method: str | None = None

    @classmethod
    def from_pod(cls, pod: dict[str, Any]) -> _ReadinessSignals:
        readiness = pod.get("readiness")
        if not isinstance(readiness, dict):
            return cls()
        probe_method = readiness.get("probe_method")
        return cls(
            runtime_seen_at=_non_empty_string_or_none(readiness.get("runtime_seen_at")),
            port_mappings_seen_at=_non_empty_string_or_none(readiness.get("port_mappings_seen_at")),
            probe_passed_at=_non_empty_string_or_none(readiness.get("probe_passed_at")),
            probe_method=str(probe_method) if probe_method else None,
        )

    @property
    def complete(self) -> bool:
        return (
            self.runtime_seen_at is not None
            and self.port_mappings_seen_at is not None
            and self.probe_passed_at is not None
        )

    def as_dict(self) -> dict[str, str]:
        values: dict[str, str] = {}
        if self.runtime_seen_at is not None:
            values["runtime_seen_at"] = self.runtime_seen_at
        if self.port_mappings_seen_at is not None:
            values["port_mappings_seen_at"] = self.port_mappings_seen_at
        if self.probe_passed_at is not None:
            values["probe_passed_at"] = self.probe_passed_at
        if self.probe_method is not None:
            values["probe_method"] = self.probe_method
        return values


def _non_empty_string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")


def _require_api_key(api_key: str | None = None) -> str:
    key = api_key
    if key is None:
        key, _source = resolve_runpod_api_key(os.environ)
    if not key:
        raise RunPodError(MISSING_CREDENTIAL_MESSAGE)
    return key


def _sdk(api_key: str | None = None) -> Any:
    """Lazy import + api-key set. Keeps import-light at module load."""
    import runpod  # type: ignore[import-untyped]  # noqa: PLC0415  # reason: SDK import depends on runtime API-key setup

    runpod.api_key = _require_api_key(api_key)
    return runpod


def _rest_base_url(rest_api_url: str | None = None) -> str:
    return (rest_api_url or os.environ.get("RUNPOD_REST_API_URL", DEFAULT_RUNPOD_REST_URL)).rstrip(
        "/"
    )


def _rest_v2_base_url(rest_api_url: str | None = None) -> str:
    """Return the canonical v2 base URL, retaining the old catalogue override."""

    return (
        rest_api_url
        or os.environ.get("RUNPOD_REST_API_URL")
        or os.environ.get("RUNPOD_REST_V2_API_URL")
        or DEFAULT_RUNPOD_REST_URL
    ).rstrip("/")


def _legacy_rest_base_url(rest_api_url: str | None = None) -> str:
    """Return the bounded legacy-v1 base used only for unsupported v2 operations."""

    return (
        rest_api_url or os.environ.get("RUNPOD_REST_V1_API_URL", "https://rest.runpod.io/v1")
    ).rstrip("/")


def _rest_headers(api_key: str | None = None) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_require_api_key(api_key)}",
        "Content-Type": "application/json",
    }


def _rest_auth_kwargs(api_key: str | None, rest_api_url: str | None) -> _RestAuthKwargs:
    kwargs: _RestAuthKwargs = {}
    if api_key is not None:
        kwargs["api_key"] = api_key
    if rest_api_url is not None:
        kwargs["rest_api_url"] = rest_api_url
    return kwargs


def _sdk_auth_kwargs(api_key: str | None) -> _SdkAuthKwargs:
    return {"api_key": api_key} if api_key is not None else {}


def _has_explicit_rest_auth(api_key: str | None, rest_api_url: str | None) -> bool:
    return api_key is not None or rest_api_url is not None


def _get_pod_sync_for_auth(
    pod_id: str,
    *,
    api_key: str | None,
    rest_api_url: str | None,
) -> dict[str, Any] | None:
    if _has_explicit_rest_auth(api_key, rest_api_url):
        return _get_pod_sync(pod_id, **_rest_auth_kwargs(api_key, rest_api_url))
    return get_pod_sync(pod_id)


def _terminate_pod_sync_for_auth(
    pod_id: str,
    *,
    api_key: str | None,
    rest_api_url: str | None,
) -> None:
    if _has_explicit_rest_auth(api_key, rest_api_url):
        _terminate_pod_sync(pod_id, **_rest_auth_kwargs(api_key, rest_api_url))
        return
    terminate_pod_sync(pod_id)


def _rest_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> Any:
    return _request_at_base(
        method,
        path,
        base_url=_rest_base_url(rest_api_url),
        json_body=json_body,
        params=params,
        timeout_s=timeout_s,
        api_key=api_key,
    )


def _legacy_rest_request(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = 60.0,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> Any:
    return _request_at_base(
        method,
        path,
        base_url=_legacy_rest_base_url(rest_api_url),
        json_body=json_body,
        params=params,
        timeout_s=timeout_s,
        api_key=api_key,
    )


def _request_at_base(
    method: str,
    path: str,
    *,
    base_url: str,
    json_body: dict[str, Any] | None,
    params: dict[str, Any] | None,
    timeout_s: float,
    api_key: str | None,
) -> Any:
    """Issue one concrete RunPod REST request with bounded safe retries."""

    key = _require_api_key(api_key)
    url = f"{base_url}/{path.lstrip('/')}"
    normalized_method = method.upper()
    safe_to_repeat = normalized_method in {"GET", "DELETE", "PATCH"}
    attempts = 2
    response: httpx.Response | None = None
    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=timeout_s) as client:
                response = client.request(
                    normalized_method,
                    url,
                    headers={
                        "Authorization": f"Bearer {key}",
                        "Content-Type": "application/json",
                    },
                    json=json_body,
                    params=params,
                )
        except httpx.HTTPError:
            if not safe_to_repeat or attempt + 1 >= attempts:
                raise
            continue
        retryable_status = response.status_code == 429 or (
            safe_to_repeat and response.status_code >= 500
        )
        if not retryable_status or attempt + 1 >= attempts:
            break
    if response is None:
        raise RunPodError(f"{normalized_method} {path} returned no response")
    if response.status_code == 204:
        return {}
    if response.status_code >= 400:
        body = redact_text(response.text[:4096], secrets=(key,))
        raise RunPodRestError(normalized_method, path, response.status_code, body)
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise RunPodError(f"{normalized_method} {path} returned a non-JSON response") from exc


def argv_to_v2_args(argv: Iterable[str]) -> str:
    """Serialize argv for REST v2 without changing shell-visible arguments."""

    values = list(argv)
    if any("\x00" in value for value in values):
        raise RunPodError("RunPod command arguments must not contain NUL bytes")
    encoded = shlex.join(values)
    if shlex.split(encoded) != values:
        raise RunPodError("RunPod command arguments could not be encoded without loss")
    return encoded


def _ports_for_rest(ports: str | None) -> list[str] | None:
    if not ports:
        return None
    return [item.strip() for item in ports.split(",") if item.strip()]


def _cloud_types_for_rest(cloud_type: str, *, network_volume_id: str | None = None) -> list[str]:
    normalized = cloud_type.upper()
    if network_volume_id:
        if normalized == "COMMUNITY":
            raise RunPodError(
                "Network volumes require cloud_type='SECURE' or 'ALL', "
                "not 'COMMUNITY' (RunPod policy)."
            )
        return ["SECURE"]
    if normalized == "ALL":
        return ["COMMUNITY", "SECURE"]
    if normalized in {"COMMUNITY", "SECURE"}:
        return [normalized]
    raise RunPodError(f"unsupported RunPod REST cloud_type {cloud_type!r}")


def _container_registry_auth_id(image_ref: str | None = None) -> str | None:
    return registry_auth_id_from_env(image_ref)


def _normalize_pod(pod: dict[str, Any]) -> dict[str, Any]:
    """Smooth over SDK-vs-REST response shape differences used by callers."""

    if "imageName" not in pod and "image" in pod:
        pod["imageName"] = pod.get("image")
    if "desiredStatus" not in pod and "status" in pod:
        pod["desiredStatus"] = pod.get("status")
    if "costPerHr" not in pod and "cost" in pod:
        pod["costPerHr"] = pod.get("cost")
    if "templateId" not in pod and "template" in pod:
        pod["templateId"] = pod.get("template")
    gpu = pod.get("gpu")
    machine = pod.get("machine")
    if isinstance(gpu, dict):
        pod.setdefault("gpuTypeId", gpu.get("id"))
        pod.setdefault("gpuCount", gpu.get("count"))
        if not isinstance(machine, dict):
            machine = {}
            pod["machine"] = machine
        machine.setdefault("gpuDisplayName", gpu.get("displayName") or gpu.get("id"))
    if isinstance(machine, dict):
        pod.setdefault("gpuTypeId", machine.get("gpuTypeId"))
    mounts = pod.get("mounts")
    if isinstance(mounts, dict):
        network_mounts = mounts.get("network")
        if isinstance(network_mounts, list) and network_mounts:
            first_mount = network_mounts[0]
            if isinstance(first_mount, dict):
                pod.setdefault("networkVolumeId", first_mount.get("volumeId"))
    return pod


def _normalize_v2_pod(pod: dict[str, Any]) -> dict[str, Any]:
    """Validate the stable v2 response identity before compatibility normalization."""

    validated = V2PodResponse.model_validate(pod).model_dump()
    return _normalize_pod(validated)


def _sdk_get_pod_sync(pod_id: str, *, api_key: str | None = None) -> dict[str, Any] | None:
    """Fetch pod details through the SDK/GraphQL path as a runtime fallback."""
    try:
        pod = _sdk(**_sdk_auth_kwargs(api_key)).get_pod(pod_id)
    except Exception as exc:  # noqa: BLE001  # reason: SDK exposes unstable exception types
        log.debug("sdk get_pod(%s) failed: %s", pod_id, exc)
        return None
    return _normalize_pod(pod) if isinstance(pod, dict) else None


def _configured_capacity_error_substrings() -> tuple[str, ...]:
    raw_substrings = os.environ.get(CAPACITY_ERROR_SUBSTRINGS_ENV)
    if raw_substrings is None:
        return DEFAULT_CAPACITY_ERROR_SUBSTRINGS
    return tuple(
        substring.strip()
        for substring in raw_substrings.replace("\n", ",").split(",")
        if substring.strip()
    )


def _coerce_timeout_s(value: object, *, source: str) -> float:
    if value is None or isinstance(value, bool):
        raise RunPodError(f"{source} must be a number of seconds")
    try:
        timeout_s = float(cast(float | int | str, value))
    except (TypeError, ValueError) as exc:
        raise RunPodError(f"{source} must be a number of seconds") from exc
    if not math.isfinite(timeout_s) or timeout_s < 0:
        raise RunPodError(f"{source} must be >= 0")
    return timeout_s


def _volume_attach_timeout_s(override_s: float | None = None) -> float:
    if override_s is not None:
        return _coerce_timeout_s(override_s, source="volume_attach_timeout_s")
    raw_timeout = os.environ.get(VOLUME_ATTACH_TIMEOUT_ENV)
    if raw_timeout is None or not raw_timeout.strip():
        return DEFAULT_VOLUME_ATTACH_TIMEOUT_S
    return _coerce_timeout_s(raw_timeout, source=VOLUME_ATTACH_TIMEOUT_ENV)


def _capacity_error_search_text(exc: Exception) -> str:
    if isinstance(exc, RunPodRestError):
        return f"{exc.body}\n{exc}"
    return str(exc)


def classify_runpod_failure(exc: Exception) -> Literal["capacity", "permanent", "transient"]:
    """Decide what a RunPod failure means for retry.

    RunPod returns 500 for client mistakes and permanent conditions as well as for
    genuine faults, so the status alone is not enough and a broad substring match
    over the body (``"unavailable"``) turns every outage into "no capacity".
    """
    body = _capacity_error_search_text(exc).lower()
    if any(phrase in body for phrase in CAPACITY_PHRASES):
        return "capacity"
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and 400 <= status < 500:
        return "permanent"
    if any(phrase in body for phrase in PERMANENT_PHRASES):
        return "permanent"
    return "transient"


def _substring_capacity_error_matcher(exc: Exception) -> bool:
    message = _capacity_error_search_text(exc).lower()
    return any(
        substring.lower() in message for substring in _configured_capacity_error_substrings()
    )


CAPACITY_ERROR_MATCHERS: list[CapacityErrorMatcher] = [_substring_capacity_error_matcher]


def _log_unmatched_capacity_error(exc: Exception) -> None:
    if isinstance(exc, RunPodRestError):
        log.warning(
            "unmatched RunPod error while checking capacity match: "
            "method=%s path=%s status=%s body=%s",
            exc.method,
            exc.path,
            exc.status_code,
            exc.body,
        )
        return
    log.warning("unmatched RunPod error while checking capacity match: %s", exc)


def _is_capacity_error(
    exc: Exception,
    matchers: Iterable[CapacityErrorMatcher] | None = None,
    *,
    log_unmatched: bool = True,
) -> bool:
    capacity_matchers = CAPACITY_ERROR_MATCHERS if matchers is None else matchers
    if any(matcher(exc) for matcher in capacity_matchers):
        return True
    if classify_runpod_failure(exc) == "capacity":
        return True
    if log_unmatched:
        _log_unmatched_capacity_error(exc)
    return False


def _pod_cost_per_hr(pod: dict[str, Any]) -> Decimal:
    raw_cost = pod.get("costPerHr")
    if raw_cost is None:
        raw_cost = pod.get("cost_per_hr")
    if raw_cost is None:
        return Decimal("0")

    pod_id = str(pod.get("id") or "<unknown>")
    return _money_decimal(raw_cost, field_name="costPerHr", pod_id=pod_id)


def _money_decimal(value: object, *, field_name: str, pod_id: str) -> Decimal:
    if isinstance(value, bool):
        raise RunPodError(f"pod {pod_id} returned invalid {field_name} {value!r}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise RunPodError(f"pod {pod_id} returned invalid {field_name} {value!r}") from exc

    if not parsed.is_finite() or parsed < 0:
        raise RunPodError(f"pod {pod_id} returned invalid {field_name} {value!r}")
    return parsed


def _max_cost_per_hr_decimal(max_cost_per_hr: float) -> Decimal:
    try:
        parsed = Decimal(str(max_cost_per_hr))
    except (InvalidOperation, ValueError) as exc:
        raise RunPodError(f"invalid max_cost_per_hr {max_cost_per_hr!r}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise RunPodError(f"invalid max_cost_per_hr {max_cost_per_hr!r}")
    return parsed


def _pod_has_cost(pod: dict[str, Any]) -> bool:
    return pod.get("costPerHr") is not None or pod.get("cost_per_hr") is not None


def _gate_pod_cost_before_readiness(
    pod_id: str,
    pod: dict[str, Any],
    *,
    max_cost_per_hr: float | None,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> ProviderFallbackRequested | None:
    if max_cost_per_hr is None:
        return None

    try:
        cost_per_hr = _pod_cost_per_hr(pod)
    except RunPodError as exc:
        log.warning("%s; deleting before readiness wait", exc)
        _terminate_pod_sync(pod_id, **_rest_auth_kwargs(api_key, rest_api_url))
        raise
    max_cost = _max_cost_per_hr_decimal(max_cost_per_hr)
    if not cost_per_hr or cost_per_hr <= max_cost:
        return None

    cap_error = ProviderFallbackRequested(
        f"pod {pod_id} cost ${cost_per_hr:.2f}/hr exceeds max ${max_cost:.2f}/hr"
    )
    log.warning("%s; deleting before readiness wait", cap_error)
    _terminate_pod_sync_for_auth(pod_id, api_key=api_key, rest_api_url=rest_api_url)
    return cap_error


def _coerce_non_negative_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or not value.is_integer():
            return None
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = float(stripped)
        except ValueError:
            return None
        if not math.isfinite(parsed) or parsed < 0 or not parsed.is_integer():
            return None
        return int(parsed)
    return None


def _pod_gpu_count(pod: dict[str, Any]) -> int | None:
    for key in ("gpuCount", "gpu_count"):
        count = _coerce_non_negative_int(pod.get(key))
        if count is not None:
            return count

    machine = pod.get("machine")
    if isinstance(machine, dict):
        for key in ("gpuCount", "gpu_count", "gpu_count_allocated"):
            count = _coerce_non_negative_int(machine.get(key))
            if count is not None:
                return count

    gpu = pod.get("gpu")
    if isinstance(gpu, dict):
        count = _coerce_non_negative_int(gpu.get("count"))
        if count is not None:
            return count

    for key in ("gpus", "gpuIds", "gpu_ids"):
        value = pod.get(key)
        if isinstance(value, list | tuple):
            return len(value)
    return None


def _pod_allocated_gpu_type_id(pod: dict[str, Any]) -> str | None:
    for key in ("gpuTypeId", "gpu_type_id"):
        value = _non_empty_string_or_none(pod.get(key))
        if value is not None:
            return value

    machine = pod.get("machine")
    if isinstance(machine, dict):
        for key in ("gpuTypeId", "gpu_type_id"):
            value = _non_empty_string_or_none(machine.get(key))
            if value is not None:
                return value

    gpu = pod.get("gpu")
    if isinstance(gpu, dict):
        for key in ("id", "gpuTypeId", "gpu_type_id"):
            value = _non_empty_string_or_none(gpu.get(key))
            if value is not None:
                return value
    return None


def _has_explicit_empty_gpu_type_id(pod: dict[str, Any]) -> bool:
    if _pod_allocated_gpu_type_id(pod) is not None:
        return False
    if "gpuTypeId" in pod or "gpu_type_id" in pod:
        return True
    machine = pod.get("machine")
    if isinstance(machine, dict) and ("gpuTypeId" in machine or "gpu_type_id" in machine):
        return True
    gpu = pod.get("gpu")
    return isinstance(gpu, dict) and any(key in gpu for key in ("id", "gpuTypeId", "gpu_type_id"))


def _pod_allocation_refresh_needed(pod: dict[str, Any]) -> bool:
    if _pod_gpu_count(pod) is not None or _pod_allocated_gpu_type_id(pod) is not None:
        return False
    return any(key in pod for key in ("gpu", "machine", "gpuCount", "gpu_count"))


def _gate_pod_allocation_before_readiness(
    pod_id: str,
    pod: dict[str, Any],
    *,
    requested_gpu_type_ids: Iterable[str],
) -> ProviderFallbackRequested | None:
    gpu_count = _pod_gpu_count(pod)
    if gpu_count == 0:
        return ProviderFallbackRequested(f"pod {pod_id} allocated zero GPUs")

    if gpu_count is None and _has_explicit_empty_gpu_type_id(pod):
        return ProviderFallbackRequested(f"pod {pod_id} allocated zero GPUs")

    allocated_gpu_type = _pod_allocated_gpu_type_id(pod)
    requested = set(requested_gpu_type_ids)
    if allocated_gpu_type is not None and requested and allocated_gpu_type not in requested:
        return ProviderFallbackRequested(
            f"pod {pod_id} allocated GPU {allocated_gpu_type!r} outside requested "
            f"set {sorted(requested)!r}"
        )
    return None


def _copy_initial_runtime_fields(
    *,
    initial: dict[str, Any],
    refreshed: dict[str, Any],
) -> dict[str, Any]:
    if initial.get("ports") and not refreshed.get("ports"):
        refreshed["ports"] = initial["ports"]
    if initial.get("networkVolumeId") and not refreshed.get("networkVolumeId"):
        refreshed["networkVolumeId"] = initial["networkVolumeId"]
    return refreshed


def _refresh_pod_for_pre_readiness_guards(
    pod_id: str,
    pod: dict[str, Any],
    *,
    max_cost_per_hr: float | None,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> dict[str, Any]:
    needs_cost_refresh = max_cost_per_hr is not None and not _pod_has_cost(pod)
    if not needs_cost_refresh and not _pod_allocation_refresh_needed(pod):
        return pod

    refreshed = _get_pod_sync_for_auth(pod_id, api_key=api_key, rest_api_url=rest_api_url)
    if refreshed is None:
        return pod
    return _copy_initial_runtime_fields(initial=pod, refreshed=refreshed)


def _legacy_v1_create_reasons(
    workload: WorkloadConfig,
    *,
    data_center_id: str | None,
    docker_entrypoint: list[str] | None,
    support_public_ip: bool,
) -> tuple[str, ...]:
    """Return options that REST v2 cannot preserve on pod create."""

    reasons: list[str] = []
    if workload.gpu_type_priority == "availability":
        reasons.append("gpuTypePriority=availability")
    if data_center_id is not None and workload.data_center_priority == "availability":
        reasons.append("dataCenterPriority=availability")
    if workload.min_vcpu is not None:
        reasons.append("minVCPUPerGPU")
    if workload.min_memory_gb is not None:
        reasons.append("minRAMPerGPU")
    if docker_entrypoint is not None:
        reasons.append("dockerEntrypoint")
    if support_public_ip:
        reasons.append("supportPublicIp")
    return tuple(reasons)


def _legacy_v1_create_payload(
    *,
    name: str,
    template_id: str | None,
    image_name: str,
    workload: WorkloadConfig,
    env: dict[str, str],
    cloud_type: str,
    gpu_type_ids: list[str],
    network_volume_id: str | None,
    data_center_id: str | None,
    docker_entrypoint: list[str] | None,
    docker_start_cmd: list[str] | None,
    container_registry_auth_id: str | None,
    support_public_ip: bool,
) -> dict[str, Any]:
    """Build the unchanged legacy-v1 body for explicitly unsupported options."""

    allowed_cuda_versions = _legacy_v1_cuda_versions(workload.allowed_cuda_versions)

    payload: dict[str, Any] = {
        "name": name,
        "imageName": image_name,
        "computeType": "GPU",
        "cloudType": cloud_type,
        "gpuCount": workload.gpu_count,
        "gpuTypeIds": gpu_type_ids,
        "gpuTypePriority": workload.gpu_type_priority,
        "containerDiskInGb": workload.container_disk_gb,
        "supportPublicIp": support_public_ip,
        "env": env,
        "volumeMountPath": "/workspace",
    }
    if workload.min_vcpu is not None:
        payload["minVCPUPerGPU"] = workload.min_vcpu
    if workload.min_memory_gb is not None:
        payload["minRAMPerGPU"] = workload.min_memory_gb
    if allowed_cuda_versions:
        payload["allowedCudaVersions"] = allowed_cuda_versions
    registry_auth_id = container_registry_auth_id or _container_registry_auth_id(image_name)
    if registry_auth_id:
        payload["containerRegistryAuthId"] = registry_auth_id
    if template_id:
        payload["templateId"] = template_id
    if network_volume_id:
        payload["networkVolumeId"] = network_volume_id
    if data_center_id:
        payload["dataCenterIds"] = [data_center_id]
        payload["dataCenterPriority"] = workload.data_center_priority
    if ports := _ports_for_rest(workload.ports):
        payload["ports"] = ports
    if docker_entrypoint is not None:
        payload["dockerEntrypoint"] = docker_entrypoint
    if docker_start_cmd is not None:
        payload["dockerStartCmd"] = docker_start_cmd
    return payload


def _legacy_v1_cuda_versions(versions: list[str] | None) -> list[str] | None:
    """Intersect CUDA constraints with the legacy-v1 create enum.

    A non-empty request that has no representable v1 value fails closed instead
    of silently dropping the placement constraint and launching unconstrained.
    """

    if versions is None:
        return None
    supported = [version for version in versions if version in LEGACY_V1_ALLOWED_CUDA_VERSIONS]
    if not supported:
        raise RunPodError("legacy REST v1 cannot represent any requested allowed CUDA version")
    return supported


def _v2_create_payload(
    *,
    name: str,
    template_id: str | None,
    image_name: str,
    workload: WorkloadConfig,
    env: dict[str, str],
    cloud_type: str,
    gpu_type_id: str,
    network_volume_id: str | None,
    data_center_id: str | None,
    docker_start_cmd: list[str] | None,
    container_registry_auth_id: str | None,
) -> dict[str, Any]:
    """Build and validate one strict REST v2 pod-create body."""

    request = V2CreatePodRequest(
        name=name,
        image=image_name,
        cloud=cast(Literal["COMMUNITY", "SECURE"], cloud_type),
        gpu=V2PodGpuRequest(
            id=gpu_type_id,
            count=workload.gpu_count,
            allowedCudaVersions=workload.allowed_cuda_versions,
        ),
        disk=workload.container_disk_gb,
        env=env,
        args=(argv_to_v2_args(docker_start_cmd) if docker_start_cmd is not None else None),
        mounts=(
            V2PodMounts(network=[V2PodNetworkMount(volumeId=network_volume_id, path="/workspace")])
            if network_volume_id
            else None
        ),
        ports=_ports_for_rest(workload.ports),
        registry=(container_registry_auth_id or _container_registry_auth_id(image_name)),
        dataCenterIds=([data_center_id] if data_center_id else None),
        templateId=template_id,
    )
    return request.model_dump(by_alias=True, exclude_none=True)


class PodCreateArgs(TypedDict, total=False):
    """The one parameter list for the pod-create entry points.

    ``name``, ``template_id``, ``image_name``, ``workload``, and ``env`` are required; every
    other key falls back to ``_POD_CREATE_DEFAULTS``.
    """

    name: Required[str]
    template_id: Required[str | None]
    image_name: Required[str]
    workload: Required[WorkloadConfig]
    env: Required[dict[str, str]]
    cloud_type_override: str | None
    network_volume_id: str | None
    data_center_id: str | None
    docker_entrypoint: list[str] | None
    docker_start_cmd: list[str] | None
    container_registry_auth_id: str | None
    support_public_ip: bool
    max_cost_per_hr: float | None
    max_pod_attempts: int | None
    timeout_per_attempt_s: float
    startup_timeout_s: float
    startup_poll_s: float
    volume_attach_timeout_s: float | None
    readiness_path: str
    pre_readiness_callback: Callable[[dict[str, Any]], None] | None
    wait_for_readiness: bool
    api_key: str | None
    rest_api_url: str | None
    rest_v1_api_url: str | None


_POD_CREATE_DEFAULTS: dict[str, Any] = {
    "cloud_type_override": None,
    "network_volume_id": None,
    "data_center_id": None,
    "docker_entrypoint": None,
    "docker_start_cmd": None,
    "container_registry_auth_id": None,
    "support_public_ip": False,
    "max_cost_per_hr": None,
    "max_pod_attempts": None,
    "timeout_per_attempt_s": 120.0,
    "startup_timeout_s": 600.0,
    "startup_poll_s": 15.0,
    "volume_attach_timeout_s": None,
    "readiness_path": "/health",
    "pre_readiness_callback": None,
    "wait_for_readiness": True,
    "api_key": None,
    "rest_api_url": None,
    "rest_v1_api_url": None,
}


def _create_pod_with_fallback_sync(**args: Unpack[PodCreateArgs]) -> dict[str, Any]:
    """Launch a pod via REST, trying GPU/cloud fallbacks until one succeeds."""

    params: dict[str, Any] = {**_POD_CREATE_DEFAULTS, **args}
    name = params["name"]
    template_id = params["template_id"]
    image_name = params["image_name"]
    workload = params["workload"]
    env = params["env"]
    cloud_type_override = params["cloud_type_override"]
    network_volume_id = params["network_volume_id"]
    data_center_id = params["data_center_id"]
    docker_entrypoint = params["docker_entrypoint"]
    docker_start_cmd = params["docker_start_cmd"]
    container_registry_auth_id = params["container_registry_auth_id"]
    support_public_ip = params["support_public_ip"]
    max_cost_per_hr = params["max_cost_per_hr"]
    max_pod_attempts = params["max_pod_attempts"]
    timeout_per_attempt_s = params["timeout_per_attempt_s"]
    startup_timeout_s = params["startup_timeout_s"]
    startup_poll_s = params["startup_poll_s"]
    volume_attach_timeout_s = params["volume_attach_timeout_s"]
    readiness_path = params["readiness_path"]
    pre_readiness_callback = params["pre_readiness_callback"]
    wait_for_readiness = params["wait_for_readiness"]
    api_key = params["api_key"]
    rest_api_url = params["rest_api_url"]
    rest_v1_api_url = params["rest_v1_api_url"]
    last_err: Exception | None = None
    pod_attempts = 0
    ambiguous_create = False
    legacy_v1_reasons = _legacy_v1_create_reasons(
        workload,
        data_center_id=data_center_id,
        docker_entrypoint=docker_entrypoint,
        support_public_ip=support_public_ip,
    )
    use_legacy_v1 = bool(legacy_v1_reasons)
    if use_legacy_v1:
        log.info(
            "using bounded RunPod REST v1 pod-create path for: %s",
            ", ".join(legacy_v1_reasons),
        )
    gpu_type_attempts = (
        [workload.gpu_types]
        if use_legacy_v1 and workload.gpu_type_priority == "availability"
        else [[gpu_type_id] for gpu_type_id in workload.gpu_types]
    )
    cloud_types_to_try = _cloud_types_for_rest(
        cloud_type_override or workload.cloud_type,
        network_volume_id=network_volume_id,
    )
    for gpu_type_ids in gpu_type_attempts:
        for cloud_type in cloud_types_to_try:
            gpu_label = ", ".join(gpu_type_ids)
            log.info(
                "create_pod attempt: gpu_types=%s priority=%s cloud=%s workload=%s dc=%s vol=%s",
                gpu_label,
                workload.gpu_type_priority,
                cloud_type,
                workload.name,
                data_center_id or "any",
                network_volume_id or "none",
            )
            payload = (
                _legacy_v1_create_payload(
                    name=name,
                    template_id=template_id,
                    image_name=image_name,
                    workload=workload,
                    env=env,
                    cloud_type=cloud_type,
                    gpu_type_ids=gpu_type_ids,
                    network_volume_id=network_volume_id,
                    data_center_id=data_center_id,
                    docker_entrypoint=docker_entrypoint,
                    docker_start_cmd=docker_start_cmd,
                    container_registry_auth_id=container_registry_auth_id,
                    support_public_ip=support_public_ip,
                )
                if use_legacy_v1
                else _v2_create_payload(
                    name=name,
                    template_id=template_id,
                    image_name=image_name,
                    workload=workload,
                    env=env,
                    cloud_type=cloud_type,
                    gpu_type_id=gpu_type_ids[0],
                    network_volume_id=network_volume_id,
                    data_center_id=data_center_id,
                    docker_start_cmd=docker_start_cmd,
                    container_registry_auth_id=container_registry_auth_id,
                )
            )

            skip_create_target = False
            transient_retries = 0
            while True:
                try:
                    raw_pod = (
                        _legacy_rest_request(
                            "POST",
                            "pods",
                            json_body=payload,
                            timeout_s=timeout_per_attempt_s,
                            **_rest_auth_kwargs(api_key, rest_v1_api_url),
                        )
                        if use_legacy_v1
                        else _rest_request(
                            "POST",
                            "pods",
                            json_body=payload,
                            timeout_s=timeout_per_attempt_s,
                            **_rest_auth_kwargs(api_key, rest_api_url),
                        )
                    )
                    if not isinstance(raw_pod, dict):
                        raise RunPodError(
                            f"create pod returned unexpected type: {type(raw_pod).__name__}"
                        )
                    pod = _normalize_pod(raw_pod) if use_legacy_v1 else _normalize_v2_pod(raw_pod)
                    break
                except Exception as exc:  # noqa: BLE001  # reason: API/client errors vary
                    last_err = exc
                    failure_class = classify_runpod_failure(exc)
                    if failure_class == "capacity" or _is_capacity_error(exc, log_unmatched=False):
                        log.info(
                            "no capacity for gpu_types=%s cloud=%s, trying next",
                            gpu_label,
                            cloud_type,
                        )
                        skip_create_target = True
                        break
                    if failure_class == "permanent":
                        raise
                    if not use_legacy_v1 and not (
                        isinstance(exc, RunPodRestError) and exc.status_code == 429
                    ):
                        raise
                    ambiguous_create = True
                    if transient_retries >= TRANSIENT_CREATE_RETRIES:
                        raise
                    transient_retries += 1
                    log.warning(
                        "transient create failure for gpu_types=%s cloud=%s; retrying: %s",
                        gpu_label,
                        cloud_type,
                        exc,
                    )
                    time.sleep(TRANSIENT_CREATE_BACKOFF_S)

            if skip_create_target:
                continue

            if pod and pod.get("id"):
                if network_volume_id:
                    pod.setdefault("networkVolumeId", network_volume_id)
                pod_attempts += 1
                pod_id = str(pod["id"])
                guarded_pod = _refresh_pod_for_pre_readiness_guards(
                    pod_id,
                    pod,
                    max_cost_per_hr=max_cost_per_hr,
                    api_key=api_key,
                    rest_api_url=rest_api_url,
                )
                cap_error = _gate_pod_allocation_before_readiness(
                    pod_id,
                    guarded_pod,
                    requested_gpu_type_ids=gpu_type_ids,
                )
                if cap_error is not None:
                    log.warning("%s; deleting before readiness wait", cap_error)
                    _terminate_pod_sync_for_auth(
                        pod_id,
                        api_key=api_key,
                        rest_api_url=rest_api_url,
                    )
                else:
                    cap_error = _gate_pod_cost_before_readiness(
                        pod_id,
                        guarded_pod,
                        max_cost_per_hr=max_cost_per_hr,
                        api_key=api_key,
                        rest_api_url=rest_api_url,
                    )
                if cap_error is not None:
                    last_err = cap_error
                    if max_pod_attempts is not None and pod_attempts >= max_pod_attempts:
                        raise RunPodError(
                            f"pod attempt limit {max_pod_attempts} reached for "
                            f"workload {workload.name!r}"
                        ) from last_err
                    continue
                log.info(
                    "pod created: id=%s gpu_type=%s cloud=%s dc=%s",
                    pod_id,
                    gpu_label,
                    cloud_type,
                    (guarded_pod.get("machine") or {}).get("dataCenterId", "?"),
                )
                pod_termination_invoked_after_post_create_error = False
                try:
                    if pre_readiness_callback is not None:
                        pre_readiness_callback(guarded_pod)
                    if not wait_for_readiness:
                        log.info("wait_for_readiness=False, returning raw pod after creation")
                        return guarded_pod
                    try:
                        wait_kwargs: dict[str, Any] = {
                            "initial": guarded_pod,
                            "timeout_s": startup_timeout_s,
                            "poll_s": startup_poll_s,
                            "readiness_path": readiness_path,
                        }
                        if volume_attach_timeout_s is not None:
                            wait_kwargs["volume_attach_timeout_s"] = volume_attach_timeout_s
                        if _has_explicit_rest_auth(api_key, rest_api_url):
                            wait_kwargs.update(_rest_auth_kwargs(api_key, rest_api_url))
                            return _wait_for_pod_runtime_sync(pod_id, **wait_kwargs)
                        return wait_for_pod_runtime_sync(pod_id, **wait_kwargs)
                    except PodVolumeAttachTimeout as exc:
                        last_err = ProviderAttachHangRecoveryRequested(
                            str(exc),
                            pod_id=pod_id,
                            attach_timeout_s=exc.attach_timeout_s,
                        )
                        log.warning(
                            "pod %s hit volume attach hang: %s; deleting before provider fallback",
                            pod_id,
                            exc,
                        )
                        pod_termination_invoked_after_post_create_error = True
                        _terminate_pod_sync_for_auth(
                            pod_id,
                            api_key=api_key,
                            rest_api_url=rest_api_url,
                        )
                        raise last_err from exc
                    except PodStartupTimeout as exc:
                        last_err = exc
                        log.warning(
                            "pod %s did not reach readiness: %s; deleting before next GPU",
                            pod_id,
                            exc,
                        )
                        pod_termination_invoked_after_post_create_error = True
                        _terminate_pod_sync_for_auth(
                            pod_id,
                            api_key=api_key,
                            rest_api_url=rest_api_url,
                        )
                        if max_pod_attempts is not None and pod_attempts >= max_pod_attempts:
                            raise RunPodError(
                                f"pod attempt limit {max_pod_attempts} reached for "
                                f"workload {workload.name!r}"
                            ) from last_err
                        continue
                    except PodStartupFailed:
                        log.warning("pod %s failed during startup; deleting", pod_id)
                        pod_termination_invoked_after_post_create_error = True
                        _terminate_pod_sync_for_auth(
                            pod_id,
                            api_key=api_key,
                            rest_api_url=rest_api_url,
                        )
                        raise
                finally:
                    if (
                        sys.exc_info()[0] is not None
                        and not pod_termination_invoked_after_post_create_error
                    ):
                        log.warning(
                            "pod %s failed after creation before readiness completed; deleting",
                            pod_id,
                        )
                        _terminate_pod_sync_for_auth(
                            pod_id,
                            api_key=api_key,
                            rest_api_url=rest_api_url,
                        )
            ambiguous_create = True
            log.warning("create pod returned empty pod for gpu_types=%s", gpu_label)

    if isinstance(last_err, ProviderFallbackRequested):
        raise ProviderFallbackRequested(
            f"provider fallback requested for workload {workload.name!r}: {last_err}",
            pod_attempts=pod_attempts,
            ambiguous_create=ambiguous_create,
        ) from last_err
    raise NoCapacityError(
        f"all GPU types exhausted for workload {workload.name!r}: tried {workload.gpu_types}",
        pod_attempts=pod_attempts,
        ambiguous_create=ambiguous_create,
    ) from last_err


def _public_create_args(params: dict[str, Any]) -> PodCreateArgs:
    """Turn a public wrapper's named parameters into the shared parameter object."""
    return cast(PodCreateArgs, dict(params))


def create_pod_with_fallback_sync(
    *,
    name: str,
    template_id: str | None,
    image_name: str,
    workload: WorkloadConfig,
    env: dict[str, str],
    cloud_type_override: str | None = None,
    network_volume_id: str | None = None,
    data_center_id: str | None = None,
    docker_entrypoint: list[str] | None = None,
    docker_start_cmd: list[str] | None = None,
    container_registry_auth_id: str | None = None,
    support_public_ip: bool = False,
    max_cost_per_hr: float | None = None,
    max_pod_attempts: int | None = None,
    timeout_per_attempt_s: float = 120.0,
    startup_timeout_s: float = 600.0,
    startup_poll_s: float = 15.0,
    volume_attach_timeout_s: float | None = None,
    readiness_path: str = "/health",
    pre_readiness_callback: Callable[[dict[str, Any]], None] | None = None,
    wait_for_readiness: bool = True,
) -> dict[str, Any]:
    """Launch a pod via REST, trying GPU/cloud fallbacks until one succeeds."""

    return _create_pod_with_fallback_sync(**_public_create_args(locals()))


async def _create_pod_with_fallback(**args: Unpack[PodCreateArgs]) -> dict[str, Any]:
    """Async wrapper around the documented REST pod create flow."""

    api_key = args.get("api_key")
    rest_api_url = args.get("rest_api_url")
    rest_v1_api_url = args.get("rest_v1_api_url")
    explicit_auth = _has_explicit_rest_auth(api_key, rest_api_url)
    sync_create = (
        _create_pod_with_fallback_sync
        if explicit_auth or rest_v1_api_url is not None
        else create_pod_with_fallback_sync
    )
    create_kwargs: dict[str, Any] = {
        key: value
        for key, value in args.items()
        if key not in {"api_key", "rest_api_url", "rest_v1_api_url"}
    }
    if explicit_auth:
        create_kwargs.update(_rest_auth_kwargs(api_key, rest_api_url))
    if rest_v1_api_url is not None:
        create_kwargs["rest_v1_api_url"] = rest_v1_api_url
    worker = asyncio.ensure_future(asyncio.to_thread(sync_create, **create_kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # The worker thread cannot be interrupted and may still create a paid pod. Wait for it
        # to settle (its per-attempt timeouts bound it), then terminate whatever it created.
        if explicit_auth:
            await _terminate_orphaned_create(worker, api_key=api_key, rest_api_url=rest_api_url)
        else:
            await _terminate_orphaned_create(worker)
        raise


async def _await_through_cancellation[T](future: asyncio.Future[T]) -> None:
    """Wait until *future* is done, absorbing every cancellation that arrives meanwhile.

    The caller is already handling a cancellation and re-raises it afterwards, so the
    absorbed requests are not lost. Returning early would unwind the caller, and with it
    any lock the caller holds (the control-plane journal's key lock), while the work the
    future stands for is still running.
    """
    # A shielded anyio scope stops level-triggered anyio cancellation (the MCP request
    # scope) from re-cancelling on every loop iteration; the loop absorbs native
    # ``task.cancel()``, which a shield cannot block.
    with anyio.CancelScope(shield=True):
        while not future.done():
            with suppress(asyncio.CancelledError):
                # ``wait`` never takes the future's outcome, so a thread or terminate that
                # raises cannot replace the cancellation the caller re-raises afterwards.
                await asyncio.wait({future})


async def _terminate_orphaned_create(
    worker: asyncio.Future[dict[str, Any]],
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> None:
    """Terminate the pod a cancelled create thread produced, if it produced one.

    The pod is terminated with the create's own ``api_key`` and ``rest_api_url`` (the
    process credential when the create had none), so it is never terminated against another
    account. Neither the wait for the thread nor the terminate can be cut short by a further
    cancellation: the caller re-raises the cancellation once both have finished.
    """
    await _await_through_cancellation(worker)
    if worker.cancelled():
        return
    if worker.exception() is not None:
        log.warning(
            "cancelled pod create failed before producing a pod", exc_info=worker.exception()
        )
        return
    pod = worker.result()
    pod_id = pod.get("id") if isinstance(pod, dict) else None
    if not isinstance(pod_id, str) or not pod_id:
        return
    log.warning("pod create was cancelled after the pod was created; terminating pod %s", pod_id)
    terminate = asyncio.ensure_future(
        _terminate_pod(pod_id, api_key=api_key, rest_api_url=rest_api_url)
        if _has_explicit_rest_auth(api_key, rest_api_url)
        else terminate_pod(pod_id)
    )
    await _await_through_cancellation(terminate)
    if not terminate.cancelled() and terminate.exception() is not None:
        log.warning(
            "cancelled pod create: terminating pod %s failed",
            pod_id,
            exc_info=terminate.exception(),
        )


async def create_pod_with_fallback(
    *,
    name: str,
    template_id: str | None,
    image_name: str,
    workload: WorkloadConfig,
    env: dict[str, str],
    cloud_type_override: str | None = None,
    network_volume_id: str | None = None,
    data_center_id: str | None = None,
    docker_entrypoint: list[str] | None = None,
    docker_start_cmd: list[str] | None = None,
    container_registry_auth_id: str | None = None,
    support_public_ip: bool = False,
    max_cost_per_hr: float | None = None,
    max_pod_attempts: int | None = None,
    timeout_per_attempt_s: float = 120.0,
    startup_timeout_s: float = 600.0,
    startup_poll_s: float = 15.0,
    volume_attach_timeout_s: float | None = None,
    readiness_path: str = "/health",
    pre_readiness_callback: Callable[[dict[str, Any]], None] | None = None,
    wait_for_readiness: bool = True,
) -> dict[str, Any]:
    """Async wrapper around the documented REST pod create flow."""

    return await _create_pod_with_fallback(**_public_create_args(locals()))


def _pod_runtime_state(pod: dict[str, Any]) -> str:
    runtime = pod.get("runtime") or {}
    for key in ("podStatus", "containerStatus", "status"):
        value = runtime.get(key)
        if value:
            return str(value)
    desired = pod.get("desiredStatus")
    if desired:
        return str(desired)
    return "unknown"


def _pod_has_runtime_signal(pod: dict[str, Any]) -> bool:
    runtime = pod.get("runtime")
    return isinstance(runtime, dict) and bool(runtime)


def _pod_has_port_mappings_signal(pod: dict[str, Any]) -> bool:
    if _has_mapping_value(pod.get("portMappings")):
        return True

    runtime = pod.get("runtime")
    if not isinstance(runtime, dict):
        return False
    return _has_mapping_value(runtime.get("portMappings")) or _has_mapping_value(
        runtime.get("ports")
    )


def _has_mapping_value(value: object) -> bool:
    if isinstance(value, dict | list | tuple):
        return bool(value)
    if isinstance(value, str | bytes):
        return bool(value)
    return value is not None


def _pod_has_runtime(pod: dict[str, Any]) -> bool:
    return _pod_has_runtime_signal(pod) and _pod_has_port_mappings_signal(pod)


def _pod_has_network_volume(pod: dict[str, Any]) -> bool:
    for key in ("networkVolumeId", "network_volume_id", "volumeId", "volume_id"):
        value = pod.get(key)
        if isinstance(value, str):
            if value.strip():
                return True
        elif value:
            return True

    network_volume = pod.get("networkVolume") or pod.get("network_volume")
    if isinstance(network_volume, dict):
        for key in ("id", "networkVolumeId", "volumeId"):
            value = network_volume.get(key)
            if isinstance(value, str):
                if value.strip():
                    return True
            elif value:
                return True
        return bool(network_volume)
    if isinstance(network_volume, str):
        return bool(network_volume.strip())
    return bool(network_volume)


def _coerce_uptime_seconds(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            return None
    return None


def _pod_uptime_seconds(pod: dict[str, Any]) -> float | None:
    runtime = pod.get("runtime")
    if isinstance(runtime, dict):
        for key in ("uptimeInSeconds", "uptimeSeconds", "uptime"):
            uptime_s = _coerce_uptime_seconds(runtime.get(key))
            if uptime_s is not None:
                return uptime_s
    for key in ("uptimeSeconds", "uptimeInSeconds", "uptime"):
        uptime_s = _coerce_uptime_seconds(pod.get(key))
        if uptime_s is not None:
            return uptime_s
    return None


def _pod_has_zero_uptime(pod: dict[str, Any]) -> bool:
    uptime_s = _pod_uptime_seconds(pod)
    return uptime_s is not None and uptime_s <= 0


CONTAINER_RESTART_LIMIT = 2
"""Container restarts tolerated while waiting for readiness before the pod is abandoned."""


def _container_start_failure(
    pod: dict[str, Any],
    *,
    seen_uptime: int | None,
) -> str | None:
    """Return why the container cannot start, or None while it still might.

    RunPod reports ``status: RUNNING`` for the machine, not the container. A container
    stuck in a create/fail loop leaves ``runtime.uptime`` at 0 and publishes no runtime
    ports, both visible in the pod record we already poll.
    """
    runtime = pod.get("runtime")
    if not isinstance(runtime, dict):
        return None
    uptime = runtime.get("uptime")
    if not isinstance(uptime, int):
        return None
    if uptime > 0:
        return None
    if seen_uptime is not None and seen_uptime > 0:
        return None
    ports = runtime.get("ports")
    if isinstance(ports, list) and ports:
        return None
    return (
        "container is not running: the pod reports uptime=0 with no published ports, "
        "which means it is failing to start and retrying"
    )


def _pod_http_probe_ports(pod: dict[str, Any]) -> list[int]:
    ports: list[int] = []
    seen: set[int] = set()

    def add_port(value: object) -> None:
        try:
            port = int(str(value).split("/", 1)[0])
        except TypeError, ValueError:
            return
        if port < 1 or port > 65_535 or port in seen:
            return
        seen.add(port)
        ports.append(port)

    for declared in pod.get("ports") or []:
        try:
            raw_port, protocol = str(declared).split("/", 1)
        except TypeError, ValueError:
            continue
        if protocol.lower() == "http":
            add_port(raw_port)

    runtime = pod.get("runtime")
    if isinstance(runtime, dict):
        _append_runtime_http_ports(runtime.get("ports"), add_port)
        _append_port_mapping_ports(runtime.get("portMappings"), add_port)
    _append_port_mapping_ports(pod.get("portMappings"), add_port)

    return ports


def _append_runtime_http_ports(
    runtime_ports: object,
    add_port: Callable[[object], None],
) -> None:
    if not isinstance(runtime_ports, list | tuple):
        return
    for runtime_port in runtime_ports:
        if not isinstance(runtime_port, dict):
            add_port(runtime_port)
            continue
        protocol = (
            runtime_port.get("type")
            or runtime_port.get("protocol")
            or runtime_port.get("privatePortType")
        )
        if protocol is not None and str(protocol).lower() != "http":
            continue
        add_port(
            runtime_port.get("privatePort")
            or runtime_port.get("containerPort")
            or runtime_port.get("port")
            or runtime_port.get("private")
        )


def _append_port_mapping_ports(
    port_mappings: object,
    add_port: Callable[[object], None],
) -> None:
    if isinstance(port_mappings, dict):
        for raw_port in port_mappings:
            add_port(raw_port)
        return
    if not isinstance(port_mappings, list | tuple):
        return
    for mapping in port_mappings:
        if isinstance(mapping, dict):
            add_port(
                mapping.get("privatePort") or mapping.get("containerPort") or mapping.get("port")
            )
        else:
            add_port(mapping)


def _pod_http_proxy_ready(pod: dict[str, Any], *, readiness_path: str = "/health") -> bool:
    pod_id = pod.get("id")
    if not pod_id:
        return False
    for port in _pod_http_probe_ports(pod):
        base_url = f"https://{pod_id}-{port}.proxy.runpod.net"
        for path in _readiness_paths(readiness_path):
            try:
                response = httpx.get(
                    f"{base_url}{path}",
                    headers={"Accept": "*/*", "User-Agent": "curl/8.5.0"},
                    follow_redirects=True,
                    timeout=5,
                )
            except Exception:  # noqa: BLE001  # reason: proxy can return many transient errors
                continue
            if 200 <= response.status_code < 300:
                pod.setdefault("proxyUrl", base_url)
                return True
    return False


def _pod_http_proxy_probe(
    pod: dict[str, Any], *, readiness_path: str = "/health"
) -> PodProbeResult:
    """Probe pod HTTP proxy with structured result and bounded timeout.

    Distinguishes a Cloudflare 524 timeout from other errors.
    """
    pod_id = pod.get("id")
    if not pod_id:
        return PodProbeResult(healthy=False, method=PROXY_PROBE_METHOD, error="no_pod_id")

    for port in _pod_http_probe_ports(pod):
        base_url = f"https://{pod_id}-{port}.proxy.runpod.net"
        for path in _readiness_paths(readiness_path):
            start = time.monotonic()
            try:
                response = httpx.get(
                    f"{base_url}{path}",
                    headers={"Accept": "*/*", "User-Agent": "curl/8.5.0"},
                    follow_redirects=True,
                    timeout=DEFAULT_POD_PROBE_TIMEOUT_S,
                )
                latency_ms = (time.monotonic() - start) * 1000
                if 200 <= response.status_code < 300:
                    pod.setdefault("proxyUrl", base_url)
                    return PodProbeResult(
                        healthy=True,
                        method=PROXY_PROBE_METHOD,
                        status_code=response.status_code,
                        latency_ms=latency_ms,
                    )
                if response.status_code == 524:
                    return PodProbeResult(
                        healthy=False,
                        method=PROXY_PROBE_METHOD,
                        status_code=524,
                        error="524",
                        latency_ms=latency_ms,
                    )
            except httpx.TimeoutException:
                latency_ms = (time.monotonic() - start) * 1000
                return PodProbeResult(
                    healthy=False,
                    method=PROXY_PROBE_METHOD,
                    error="timeout",
                    latency_ms=latency_ms,
                )
            except httpx.HTTPError:
                latency_ms = (time.monotonic() - start) * 1000
                return PodProbeResult(
                    healthy=False,
                    method=PROXY_PROBE_METHOD,
                    error="connection_error",
                    latency_ms=latency_ms,
                )

    return PodProbeResult(healthy=False, method=PROXY_PROBE_METHOD, error="no_port")


def _pod_ssh_localhost_probe(pod: dict[str, Any]) -> PodProbeResult:
    """Placeholder for SSH localhost pod probes.

    Pitwall's readiness order is SSH-first so operators can wire a bounded
    localhost probe without changing the readiness state machine. The default
    client does not open SSH sessions itself, so this probe reports unavailable
    and lets the RunPod proxy probe handle hermetic and default runtime checks.
    """

    if not pod.get("id"):
        return PodProbeResult(healthy=False, method=SSH_LOCALHOST_PROBE_METHOD, error="no_pod_id")
    return PodProbeResult(
        healthy=False,
        method=SSH_LOCALHOST_PROBE_METHOD,
        error="not_configured",
    )


def _pod_readiness_probe(pod: dict[str, Any], *, readiness_path: str = "/health") -> PodProbeResult:
    """Run pod readiness probes in the configured order."""

    last_result: PodProbeResult | None = None
    for method in POD_READINESS_PROBE_ORDER:
        if method == SSH_LOCALHOST_PROBE_METHOD:
            result = _pod_ssh_localhost_probe(pod)
        elif method == PROXY_PROBE_METHOD:
            result = _pod_http_proxy_probe(pod, readiness_path=readiness_path)
        else:
            result = PodProbeResult(healthy=False, method=method, error="unknown_method")
        if result.healthy:
            return result
        last_result = result
    return last_result or PodProbeResult(
        healthy=False,
        method="none",
        error="no_probe_methods",
    )


def _observe_readiness_signals(
    pod: dict[str, Any],
    signals: _ReadinessSignals,
    *,
    readiness_path: str = "/health",
) -> None:
    if signals.runtime_seen_at is None and _pod_has_runtime_signal(pod):
        signals.runtime_seen_at = _utc_now_iso()
    if signals.port_mappings_seen_at is None and _pod_has_port_mappings_signal(pod):
        signals.port_mappings_seen_at = _utc_now_iso()
    if signals.probe_passed_at is None and signals.port_mappings_seen_at is not None:
        probe = _pod_readiness_probe(pod, readiness_path=readiness_path)
        if not probe.healthy:
            _attach_readiness_signals(pod, signals)
            return
        signals.probe_passed_at = _utc_now_iso()
        signals.probe_method = probe.method
    _attach_readiness_signals(pod, signals)


def _attach_readiness_signals(
    pod: dict[str, Any],
    signals: _ReadinessSignals,
) -> None:
    readiness = pod.get("readiness")
    if not isinstance(readiness, dict):
        readiness = {}
        pod["readiness"] = readiness
    readiness.update(signals.as_dict())


def _pod_failed_startup(pod: dict[str, Any]) -> bool:
    state = _pod_runtime_state(pod).lower()
    return state in {
        "cancelled",
        "canceled",
        "dead",
        "exited",
        "failed",
        "stopped",
        "terminated",
    }


def _pod_log_tail(pod_id: str, *, lines: int) -> str | None:
    """Return a short pod log tail without masking readiness failures."""
    try:
        key = _require_api_key(None)
        data = bytearray()
        deadline = time.monotonic() + POD_LOG_DIAGNOSTIC_TIMEOUT_S
        with (
            httpx.Client(timeout=POD_LOG_DIAGNOSTIC_TIMEOUT_S) as client,
            client.stream(
                "GET",
                f"{_rest_base_url()}/pods/{pod_id}/logs",
                headers=_rest_headers(key),
                params={"lines": lines},
            ) as response,
        ):
            if response.status_code >= 400:
                return None
            for chunk in response.iter_raw():
                if time.monotonic() >= deadline:
                    break
                remaining = POD_LOG_DIAGNOSTIC_BYTES - len(data)
                if remaining <= 0:
                    break
                chunk = chunk[:remaining]
                data.extend(chunk)
                if len(data) >= POD_LOG_DIAGNOSTIC_BYTES or data.count(b"\n") >= lines:
                    break
                if time.monotonic() >= deadline:
                    break
    except Exception as exc:  # noqa: BLE001  # reason: logs are best-effort diagnostics
        log.debug("could not fetch logs for pod %s after a failed start: %s", pod_id, exc)
        return None

    log_text = redact_text(data.decode("utf-8", errors="replace"), secrets=(key,))
    tail = "\n".join(log_text.splitlines()[-lines:]).strip()
    return tail or None


def _wait_for_pod_runtime_sync(
    pod_id: str,
    *,
    initial: dict[str, Any] | None = None,
    timeout_s: float = 600.0,
    poll_s: float = 15.0,
    volume_attach_timeout_s: float | None = None,
    readiness_path: str = "/health",
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> dict[str, Any]:
    """Wait until RunPod reports all readiness signals for ``pod_id``.

    Pod creation can return a pod id before the container starts. That state
    still burns credits, so callers should not treat the pod as successfully
    launched until runtime, port mappings, and a successful probe are visible.
    """

    latest = initial or {"id": pod_id}
    signals = _ReadinessSignals.from_pod(latest)
    _observe_readiness_signals(latest, signals, readiness_path=readiness_path)
    if timeout_s <= 0:
        return latest

    started_at = time.monotonic()
    first_seen_at = started_at
    deadline = started_at + timeout_s
    best_uptime: int | None = None
    last_uptime: int | None = None
    restarts = 0
    attach_timeout_s: float | None = None
    attach_deadline: float | None = None
    while True:
        _observe_readiness_signals(latest, signals, readiness_path=readiness_path)
        if signals.complete:
            return latest
        if _pod_failed_startup(latest):
            raise PodStartupFailed(
                f"pod {pod_id} reached startup state {_pod_runtime_state(latest)!r}"
            )

        runtime = latest.get("runtime")
        uptime = runtime.get("uptime") if isinstance(runtime, dict) else None
        if isinstance(uptime, int) and (best_uptime is None or uptime > best_uptime):
            best_uptime = uptime
        if isinstance(uptime, int):
            # RunPod keeps a crash-looping pod RUNNING; only the container uptime
            # falling back shows each restart (live defect 7, 2026-08-30).
            if last_uptime is not None and uptime < last_uptime:
                restarts += 1
            last_uptime = uptime
        if restarts >= CONTAINER_RESTART_LIMIT:
            tail = _pod_log_tail(pod_id, lines=5)
            raise ContainerStartFailure(
                f"pod {pod_id} container restarted {restarts} times before becoming ready"
                + (f"; last log lines: {tail}" if tail else "")
            )

        if attach_deadline is None and _pod_has_network_volume(latest):
            attach_timeout_s = _volume_attach_timeout_s(volume_attach_timeout_s)
            attach_deadline = started_at + attach_timeout_s

        now = time.monotonic()
        failure = _container_start_failure(latest, seen_uptime=best_uptime)
        if failure is not None and (now - first_seen_at) > CONTAINER_START_GRACE_S:
            tail = _pod_log_tail(pod_id, lines=5)
            raise ContainerStartFailure(
                f"pod {pod_id} never started its container after "
                f"{CONTAINER_START_GRACE_S:.0f}s: {failure}"
                + (f"; last log lines: {tail}" if tail else "")
            )
        if (
            attach_deadline is not None
            and attach_timeout_s is not None
            and now >= attach_deadline
            and _pod_has_zero_uptime(latest)
        ):
            raise PodVolumeAttachTimeout(pod_id, attach_timeout_s)

        remaining = deadline - now
        if remaining <= 0:
            raise PodStartupTimeout(
                f"pod {pod_id} did not reach runtime within {timeout_s:.0f}s "
                f"(last state={_pod_runtime_state(latest)!r})"
            )

        time.sleep(min(max(poll_s, 0.1), remaining))
        refreshed = _get_pod_sync_for_auth(pod_id, api_key=api_key, rest_api_url=rest_api_url)
        if refreshed is None:
            raise PodStartupFailed(f"pod {pod_id} disappeared before runtime was ready")
        if latest.get("ports") and not refreshed.get("ports"):
            refreshed["ports"] = latest["ports"]
        _attach_readiness_signals(refreshed, signals)
        latest = refreshed


def wait_for_pod_runtime_sync(
    pod_id: str,
    *,
    initial: dict[str, Any] | None = None,
    timeout_s: float = 600.0,
    poll_s: float = 15.0,
    volume_attach_timeout_s: float | None = None,
    readiness_path: str = "/health",
) -> dict[str, Any]:
    """Wait until RunPod reports all readiness signals for ``pod_id``."""

    return _wait_for_pod_runtime_sync(
        pod_id,
        initial=initial,
        timeout_s=timeout_s,
        poll_s=poll_s,
        volume_attach_timeout_s=volume_attach_timeout_s,
        readiness_path=readiness_path,
    )


async def _wait_for_pod_runtime(
    pod_id: str,
    *,
    initial: dict[str, Any] | None = None,
    timeout_s: float = 600.0,
    poll_s: float = 15.0,
    volume_attach_timeout_s: float | None = None,
    readiness_path: str = "/health",
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> dict[str, Any]:
    """Async wrapper for wait_for_pod_runtime_sync."""

    return await asyncio.to_thread(
        _wait_for_pod_runtime_sync,
        pod_id,
        initial=initial,
        timeout_s=timeout_s,
        poll_s=poll_s,
        volume_attach_timeout_s=volume_attach_timeout_s,
        readiness_path=readiness_path,
        api_key=api_key,
        rest_api_url=rest_api_url,
    )


async def wait_for_pod_runtime(
    pod_id: str,
    *,
    initial: dict[str, Any] | None = None,
    timeout_s: float = 600.0,
    poll_s: float = 15.0,
    volume_attach_timeout_s: float | None = None,
    readiness_path: str = "/health",
) -> dict[str, Any]:
    """Async wrapper for wait_for_pod_runtime_sync."""

    return await _wait_for_pod_runtime(
        pod_id,
        initial=initial,
        timeout_s=timeout_s,
        poll_s=poll_s,
        volume_attach_timeout_s=volume_attach_timeout_s,
        readiness_path=readiness_path,
    )


def _get_pods_sync(
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> list[dict[str, Any]]:
    """Return all pods owned by the account's RunPod user."""
    result = _rest_request(
        "GET",
        "pods",
        **_rest_auth_kwargs(api_key, rest_api_url),
    )
    if not isinstance(result, dict) or not isinstance(result.get("pods"), list):
        raise RunPodError("GET pods returned an invalid REST v2 pods envelope")
    return [_normalize_v2_pod(p) for p in result["pods"] if isinstance(p, dict)]


def get_pods_sync() -> list[dict[str, Any]]:
    """Return all pods owned by the account's RunPod user."""

    return _get_pods_sync()


async def _get_pods(
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> list[dict[str, Any]]:
    """Async wrapper for get_pods_sync."""
    return await asyncio.to_thread(
        _get_pods_sync,
        api_key=api_key,
        rest_api_url=rest_api_url,
    )


async def get_pods() -> list[dict[str, Any]]:
    """Async wrapper for get_pods_sync."""

    return await _get_pods()


def _get_pod_sync(
    pod_id: str,
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
    strict_errors: bool = False,
) -> dict[str, Any] | None:
    """Return a single pod's state, or None if not found."""
    try:
        result = _rest_request(
            "GET",
            f"pods/{pod_id}",
            **_rest_auth_kwargs(api_key, rest_api_url),
        )
        if not isinstance(result, dict):
            # Only a 404 means absent; a non-object 200 is a provider fault, never a gone pod.
            raise RunPodError(f"GET pods/{pod_id} returned an unusable response body")
        pod = _normalize_v2_pod(result)
        if pod and (not _pod_has_runtime_signal(pod) or not _pod_has_port_mappings_signal(pod)):
            sdk_pod = _sdk_get_pod_sync(pod_id, api_key=api_key)
            if sdk_pod:
                for key in (
                    "runtime",
                    "portMappings",
                    "dockerId",
                    "uptimeSeconds",
                    "uptimeInSeconds",
                ):
                    if _pod_value_missing(pod.get(key)) and not _pod_value_missing(
                        sdk_pod.get(key)
                    ):
                        pod[key] = sdk_pod[key]
        return pod
    except RunPodRestError as exc:
        if exc.status_code == 404:
            return None
        if strict_errors:
            raise
        log.warning("get_pod(%s) failed: %s", pod_id, exc)
        raise
        return None
    except Exception as exc:  # noqa: BLE001  # reason: poll path degrades transient errors to None
        if strict_errors:
            raise RunPodError(f"get_pod({pod_id}) failed: {exc}") from exc
        log.warning("get_pod(%s) failed: %s", pod_id, exc)
        return None


def get_pod_sync(pod_id: str) -> dict[str, Any] | None:
    """Return a single pod's state, or None if not found."""

    return _get_pod_sync(pod_id)


async def _get_pod(
    pod_id: str,
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
    strict_errors: bool = False,
) -> dict[str, Any] | None:
    """Async wrapper for get_pod_sync."""
    return await asyncio.to_thread(
        _get_pod_sync,
        pod_id,
        api_key=api_key,
        rest_api_url=rest_api_url,
        strict_errors=strict_errors,
    )


async def get_pod(pod_id: str) -> dict[str, Any] | None:
    """Async wrapper for get_pod_sync."""

    return await _get_pod(pod_id)


def get_pod_strict_sync(
    pod_id: str, *, api_key: str | None = None, rest_api_url: str | None = None
) -> dict[str, Any] | None:
    """Return a pod, or None only when RunPod reports it absent.

    Raises ``RunPodError`` when RunPod cannot answer, so an outage is never read as a gone
    pod (the non-strict readers return None for both).
    """
    return _get_pod_sync(pod_id, strict_errors=True, **_rest_auth_kwargs(api_key, rest_api_url))


async def get_pod_strict(
    pod_id: str, *, api_key: str | None = None, rest_api_url: str | None = None
) -> dict[str, Any] | None:
    """Async ``get_pod_strict_sync``."""
    return await _get_pod(pod_id, strict_errors=True, **_rest_auth_kwargs(api_key, rest_api_url))


def _pod_value_missing(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str | bytes | dict | list | tuple | set):
        return not bool(value)
    return False


def _terminate_pod_sync(
    pod_id: str,
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> None:
    """Irrevocably destroy a pod. Idempotent: silent on already-terminated."""
    try:
        _rest_request(
            "POST",
            f"pods/{pod_id}/action",
            json_body=V2PodActionRequest(action="terminate").model_dump(),
            **_rest_auth_kwargs(api_key, rest_api_url),
        )
    except RunPodRestError as exc:
        if exc.status_code == 404:
            log.info("pod %s already terminated or never existed", pod_id)
            return
        raise RunPodError(f"terminate_pod({pod_id}) failed: {exc}") from exc
    except Exception as exc:  # noqa: BLE001  # reason: normalize client errors to RunPodError
        raise RunPodError(f"terminate_pod({pod_id}) failed: {exc}") from exc


def terminate_pod_sync(pod_id: str) -> None:
    """Irrevocably destroy a pod. Idempotent: silent on already-terminated."""

    _terminate_pod_sync(pod_id)


async def _terminate_pod(
    pod_id: str,
    *,
    api_key: str | None = None,
    rest_api_url: str | None = None,
) -> None:
    """Async wrapper for terminate_pod_sync."""
    await asyncio.to_thread(
        _terminate_pod_sync,
        pod_id,
        api_key=api_key,
        rest_api_url=rest_api_url,
    )


async def terminate_pod(pod_id: str) -> None:
    """Async wrapper for terminate_pod_sync."""

    await _terminate_pod(pod_id)


async def terminate_all_with_tag(
    name_prefix: str = "pitwall-",
) -> int:
    """Kill-switch helper: terminate every pod whose name starts with ``name_prefix``.

    Returns the count terminated.
    """
    pods = await get_pods()
    killed = 0
    for p in pods:
        name = p.get("name") or ""
        pod_id = p.get("id")
        if not pod_id or not name.startswith(name_prefix):
            continue
        log.warning("kill switch: terminating pod %s (%s)", pod_id, name)
        await terminate_pod(pod_id)
        killed += 1
    return killed


async def get_pods_by_tag_prefix(
    name_prefix: str = "pitwall-",
) -> list[dict[str, Any]]:
    """Return pods whose name starts with ``name_prefix``.

    Returns a list of pod info dicts with ``id`` and ``name`` keys.
    """
    pods = await get_pods()
    matching: list[dict[str, Any]] = []
    for p in pods:
        name = p.get("name") or ""
        pod_id = p.get("id")
        if not pod_id or not name.startswith(name_prefix):
            continue
        matching.append({"id": pod_id, "name": name})
    return matching


async def terminate_all_with_tag_and_get_pods(
    name_prefix: str = "pitwall-",
) -> tuple[int, list[dict[str, Any]]]:
    """Kill-switch helper: terminate every pod whose name starts with ``name_prefix``.

    Returns a tuple of (count terminated, list of terminated pod info).
    Each pod info dict contains ``id`` and ``name`` keys.
    """
    pods = await get_pods()
    killed = 0
    terminated_pods: list[dict[str, Any]] = []
    for p in pods:
        name = p.get("name") or ""
        pod_id = p.get("id")
        if not pod_id or not name.startswith(name_prefix):
            continue
        log.warning("kill switch: terminating pod %s (%s)", pod_id, name)
        await terminate_pod(pod_id)
        killed += 1
        terminated_pods.append({"id": pod_id, "name": name})
    return killed, terminated_pods


class UpdatePodRequest(BaseModel):
    """Request body for PATCH /pods/{pod_id}.

    All fields are optional — RunPod applies only supplied fields.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    env: dict[str, str] | None = Field(
        default=None, description="Environment variables to set on the pod"
    )
    ports: list[str] | None = Field(
        default=None,
        description="Exposed ports in RunPod format (e.g. ['8000/http', '8080/tcp'])",
    )
    registry: str | None = None


class UpdatePodResponse(BaseModel):
    """Response body from PATCH /pods/{pod_id}."""

    model_config = {"extra": "allow"}

    id: str
    status: str | None = None
    runtime: dict[str, Any] | None = None


def _pod_action_sync(
    pod_id: str,
    action: Literal["start", "stop", "restart"],
) -> dict[str, Any]:
    """Run one strict REST v2 unified pod action."""

    try:
        result = _rest_request(
            "POST",
            f"pods/{pod_id}/action",
            json_body=V2PodActionRequest(action=action).model_dump(),
        )
    except RunPodRestError as exc:
        raise RunPodError(f"{action}_pod({pod_id}) failed: {exc}") from exc
    if not isinstance(result, dict):
        raise RunPodError(f"{action}_pod({pod_id}) returned an invalid response")
    return _normalize_v2_pod(result)


def _start_pod_sync(pod_id: str) -> dict[str, Any]:
    """Start a stopped pod through REST v2's unified action endpoint."""

    return _pod_action_sync(pod_id, "start")


async def start_pod(pod_id: str) -> dict[str, Any]:
    """Async wrapper for start_pod_sync."""
    return await asyncio.to_thread(_start_pod_sync, pod_id)


def _stop_pod_sync(pod_id: str) -> dict[str, Any]:
    """Stop a running pod through REST v2's unified action endpoint."""

    return _pod_action_sync(pod_id, "stop")


async def stop_pod(pod_id: str) -> dict[str, Any]:
    """Async wrapper for stop_pod_sync."""
    return await asyncio.to_thread(_stop_pod_sync, pod_id)


def _reset_pod_sync(pod_id: str) -> dict[str, Any]:
    """Reset a pod through the bounded legacy-v1 operation."""
    try:
        result = _legacy_rest_request("POST", f"pods/{pod_id}/reset")
    except RunPodRestError as exc:
        raise RunPodError(f"reset_pod({pod_id}) failed: {exc}") from exc
    if not isinstance(result, dict):
        raise RunPodError(f"reset_pod({pod_id}) returned an invalid response")
    return _normalize_pod(result)


async def reset_pod(pod_id: str) -> dict[str, Any]:
    """Async wrapper for reset_pod_sync."""
    return await asyncio.to_thread(_reset_pod_sync, pod_id)


def _restart_pod_sync(pod_id: str) -> dict[str, Any]:
    """Restart a pod through REST v2's unified action endpoint."""

    return _pod_action_sync(pod_id, "restart")


async def restart_pod(pod_id: str) -> dict[str, Any]:
    """Async wrapper for restart_pod_sync."""
    return await asyncio.to_thread(_restart_pod_sync, pod_id)


def _update_pod_sync(
    pod_id: str,
    *,
    env: dict[str, str] | None = None,
    ports: list[str] | None = None,
    container_registry_auth_id: str | None = None,
) -> dict[str, Any]:
    """Update mutable pod fields. Sends only supplied fields to RunPod."""
    payload = UpdatePodRequest(
        env=env,
        ports=ports,
        registry=container_registry_auth_id,
    ).model_dump(exclude_none=True)
    if not payload:
        raise RunPodError(f"update_pod({pod_id}): at least one field must be supplied")
    try:
        result = _rest_request("PATCH", f"pods/{pod_id}", json_body=payload)
        if not isinstance(result, dict):
            raise RunPodError(f"update_pod({pod_id}) returned an invalid response")
        return _normalize_v2_pod(result)
    except RunPodRestError as exc:
        raise RunPodError(f"update_pod({pod_id}) failed: {exc}") from exc


async def update_pod(
    pod_id: str,
    *,
    env: dict[str, str] | None = None,
    ports: list[str] | None = None,
    container_registry_auth_id: str | None = None,
) -> dict[str, Any]:
    """Async wrapper for update_pod_sync."""
    return await asyncio.to_thread(
        _update_pod_sync,
        pod_id,
        env=env,
        ports=ports,
        container_registry_auth_id=container_registry_auth_id,
    )


__all__ = [
    "get_pod_strict",
    "get_pod_strict_sync",
    "RunPodError",
    "NoCapacityError",
    "ProviderAttachHangRecoveryRequested",
    "PodStartupFailed",
    "PodStartupTimeout",
    "PodVolumeAttachTimeout",
    "RunPodRestError",
    "UpdatePodRequest",
    "UpdatePodResponse",
    "create_pod_with_fallback",
    "create_pod_with_fallback_sync",
    "get_pods",
    "get_pods_sync",
    "get_pod",
    "get_pod_sync",
    "terminate_all_with_tag",
    "wait_for_pod_runtime",
    "wait_for_pod_runtime_sync",
    "terminate_pod",
    "terminate_pod_sync",
    "start_pod",
    "stop_pod",
    "reset_pod",
    "restart_pod",
    "update_pod",
]
