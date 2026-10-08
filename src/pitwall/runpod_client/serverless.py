"""RunPod Serverless OpenAI-compatible client + endpoint admin.

Async httpx wrapper around the chat-completions endpoint exposed by RunPod
Serverless workers configured by the operator. Pitwall does not publish a GPU
worker image in the public alpha.

Also provides async CRUD operations for RunPod serverless endpoint management
(workers min/max, idle timeout, GPU type, flashboot) via the REST API.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from pitwall.rate_limits.retry_after import (
    DEFAULT_MAX_RETRY_AFTER_DELAY_S,
    parse_retry_after,
)
from pitwall.runpod_client.catalog import list_gpu_types_v2
from pitwall.runpod_client.pods import RunPodError, RunPodRestError
from pitwall.runpod_client.retry import (
    ClockFunc,
    RetryPolicy,
    SleepFunc,
    rest_retry_policy,
    retry_any_transport_failure,
    retry_rate_limit_or_server_error,
    send_with_retry,
    utc_now,
)
from pitwall.runpod_credentials import (
    DEFAULT_RUNPOD_REST_URL,
    MISSING_CREDENTIAL_MESSAGE,
    resolve_runpod_api_key,
)
from pitwall.security.redaction import redact_text


class ServerlessResponse(BaseModel):
    """Parsed chat-completion response."""

    content: str
    model: str
    input_tokens: int
    output_tokens: int
    finish_reason: str
    duration_ms: int
    raw: dict[str, Any]


class ServerlessClient:
    """OpenAI-compatible /v1/chat/completions client for RunPod Serverless."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: int = 600,
        retry_delays: tuple[float, ...] = (1.0, 3.0, 9.0),
        max_retry_after_s: float = DEFAULT_MAX_RETRY_AFTER_DELAY_S,
        sleep: SleepFunc = asyncio.sleep,
        clock: ClockFunc = utc_now,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.endswith("/openai/v1"):
            raise ValueError(f"base_url must end with /openai/v1; got {base_url!r}")
        if max_retry_after_s < 0:
            raise ValueError("max_retry_after_s must be >= 0")
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_s,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
        )
        self._model = model
        self._retry_policy = RetryPolicy(
            retry_exception=retry_any_transport_failure,
            retry_status=retry_rate_limit_or_server_error,
            delays=retry_delays,
            max_retry_after_s=max_retry_after_s,
            sleep=sleep,
            clock=clock,
        )

    @property
    def model(self) -> str:
        return self._model

    async def chat_completion(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float = 0.0,
        extra: dict[str, Any] | None = None,
    ) -> ServerlessResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if extra:
            payload.update(extra)

        t0 = time.perf_counter()
        response = await self._retry_post("/chat/completions", json=payload)
        data = response.json()
        duration_ms = max(1, int((time.perf_counter() - t0) * 1000))

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        usage = data.get("usage", {}) or {}

        return ServerlessResponse(
            content=str(message.get("content", "")),
            model=str(data.get("model", self._model)),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            finish_reason=str(choice.get("finish_reason", "")),
            duration_ms=duration_ms,
            raw=data,
        )

    async def _retry_post(self, path: str, *, json: dict[str, Any]) -> httpx.Response:
        response = await send_with_retry(
            lambda: self._client.post(path, json=json), self._retry_policy
        )
        response.raise_for_status()
        return response

    async def aclose(self) -> None:
        await self._client.aclose()


class EndpointScalingConfig(BaseModel):
    """Scaling configuration for a RunPod serverless endpoint.

    Attributes:
        workers_min: Minimum number of idle workers kept warm (default 0).
        workers_max: Maximum number of concurrent workers (default 3).
        idle_timeout: Seconds before an idle worker is stopped (default 60).
        gpu_type_id: GPU type identifier (e.g. "NVIDIA L4"). If not set,
            RunPod auto-selects based on availability.
        flashboot: If True, enable flashboot for faster cold-start.
    """

    model_config = ConfigDict(extra="forbid")

    workers_min: int = Field(default=0, ge=0)
    workers_max: int = Field(default=3, ge=1)
    idle_timeout: int = Field(default=60, ge=0)
    gpu_type_id: str | None = None
    flashboot: bool = False
    scaler_type: Literal["QUEUE_DELAY", "REQUEST_COUNT"] = "QUEUE_DELAY"
    scaler_value: float = Field(default=4.0, gt=0)

    def to_request_json(self) -> dict[str, Any]:
        """Serialize the nested workers/scaling/flashboot REST v2 shape."""

        if self.idle_timeout < 1:
            raise RunPodError("RunPod REST v2 requires idle_timeout >= 1")
        scaling: dict[str, Any]
        if self.scaler_type == "QUEUE_DELAY":
            if self.scaler_value < 0.5:
                raise RunPodError("RunPod QUEUE_DELAY scaler_value must be >= 0.5")
            scaling = {"type": "QUEUE_DELAY", "queueDelay": self.scaler_value}
        else:
            if not self.scaler_value.is_integer():
                raise RunPodError("RunPod REQUEST_COUNT scaler_value must be an integer")
            scaling = {"type": "REQUEST_COUNT", "requestCount": int(self.scaler_value)}
        workers = {"min": self.workers_min, "max": self.workers_max}
        if self.scaler_type == "QUEUE_DELAY":
            workers["idleTimeout"] = self.idle_timeout
        return {
            "workers": workers,
            "scaling": scaling,
            "flashboot": "FLASHBOOT" if self.flashboot else "OFF",
        }


class V2EndpointWorkers(BaseModel):
    """Strict REST v2 endpoint worker limits."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    minimum: int = Field(default=0, ge=0, alias="min")
    maximum: int = Field(default=3, ge=0, alias="max")
    idle_timeout: int | None = Field(default=None, ge=1, le=3600, alias="idleTimeout")


class V2QueueDelayScaling(BaseModel):
    """Strict REST v2 queue-delay scaler."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["QUEUE_DELAY"] = "QUEUE_DELAY"
    queue_delay: float = Field(ge=0.5, alias="queueDelay")


class V2RequestCountScaling(BaseModel):
    """Strict REST v2 in-flight request-count scaler."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["REQUEST_COUNT"] = "REQUEST_COUNT"
    request_count: int = Field(ge=1, alias="requestCount")


V2EndpointScaling = V2QueueDelayScaling | V2RequestCountScaling


class V2EndpointGpuRequest(BaseModel):
    """Strict REST v2 serverless GPU-pool selection."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    pools: list[str] = Field(min_length=1)
    excluded_types: list[str] = Field(default_factory=list, alias="excludedTypes")
    count: int = Field(default=1, ge=1)


class V2CreateEndpointRequest(BaseModel):
    """Strict REST v2 serverless endpoint-create request."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1)
    type: Literal["QUEUE", "LOAD_BALANCER"]
    scaling: V2EndpointScaling = Field(discriminator="type")
    workers: V2EndpointWorkers
    flashboot: Literal["OFF", "FLASHBOOT", "PRIORITY_FLASHBOOT"]
    gpu: V2EndpointGpuRequest
    template_id: str | None = Field(default=None, alias="templateId")
    image: str | None = None


class V2EndpointResponse(BaseModel):
    """Validated stable fields from a REST v2 serverless response."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    scaling: V2EndpointScaling = Field(discriminator="type")
    workers: V2EndpointWorkers
    flashboot: Literal["OFF", "FLASHBOOT", "PRIORITY_FLASHBOOT"] = "OFF"
    template_id: str | None = Field(default=None, alias="templateId")
    created_at: str | None = Field(default=None, alias="createdAt")


class V2UpdateEndpointRequest(BaseModel):
    """Strict REST v2 endpoint update fields used by Pitwall."""

    model_config = ConfigDict(extra="forbid")

    scaling: V2EndpointScaling = Field(discriminator="type")
    workers: V2EndpointWorkers
    flashboot: Literal["OFF", "FLASHBOOT", "PRIORITY_FLASHBOOT"]
    gpu: V2EndpointGpuRequest | None = None


class Endpoint(BaseModel):
    """A RunPod serverless endpoint."""

    id: str
    name: str
    scaling: EndpointScalingConfig
    template_id: str | None = None
    created_at: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


_REST_TIMEOUT_S = 60.0


def _rest_api_key() -> str:
    key, _source = resolve_runpod_api_key(os.environ)
    if not key:
        raise RunPodError(MISSING_CREDENTIAL_MESSAGE)
    return key


def _rest_base_url() -> str:
    return os.environ.get("RUNPOD_REST_API_URL", DEFAULT_RUNPOD_REST_URL).rstrip("/")


async def _rest_request_async(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout_s: float = _REST_TIMEOUT_S,
) -> Any:
    url = f"{_rest_base_url()}/{path.lstrip('/')}"
    key = _rest_api_key()
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    async def send() -> httpx.Response:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            return await client.request(
                method,
                url,
                headers=headers,
                json=json_body,
                params=params,
            )

    response = await send_with_retry(send, rest_retry_policy(method))
    if response.status_code == 204:
        return {}
    if response.status_code >= 400:
        body = redact_text(response.text[:4096], secrets=(key,))
        raise RunPodRestError(method, path, response.status_code, body)
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise RunPodError(f"{method} {path} returned a non-JSON response") from exc


def _normalize_endpoint_id(endpoint_id: str) -> str:
    normalized = endpoint_id.strip().strip("/")
    if not normalized:
        raise ValueError("endpoint_id must be non-empty")
    if "/" in normalized:
        raise ValueError("endpoint_id must not contain path separators")
    return normalized


def _parse_endpoint(data: dict[str, Any]) -> Endpoint:
    """Build an Endpoint from the REST v2 nested response."""

    response = V2EndpointResponse.model_validate(data)
    if isinstance(response.scaling, V2RequestCountScaling):
        scaler_type: Literal["QUEUE_DELAY", "REQUEST_COUNT"] = "REQUEST_COUNT"
        scaler_value: float = float(response.scaling.request_count)
    else:
        scaler_type = "QUEUE_DELAY"
        scaler_value = response.scaling.queue_delay
    scaling = EndpointScalingConfig(
        workers_min=response.workers.minimum,
        workers_max=response.workers.maximum,
        idle_timeout=response.workers.idle_timeout or 60,
        gpu_type_id=None,
        flashboot=response.flashboot != "OFF",
        scaler_type=scaler_type,
        scaler_value=scaler_value,
    )
    return Endpoint(
        id=response.id,
        name=response.name,
        scaling=scaling,
        template_id=response.template_id,
        created_at=response.created_at,
        raw=dict(data),
    )


async def resolve_gpu_selection_for_types(
    gpu_type_ids: list[str],
    *,
    count: int = 1,
) -> V2EndpointGpuRequest:
    """Resolve v1 GPU type IDs into the v2 pool-plus-exclusions shape."""

    requested = list(dict.fromkeys(gpu_type_ids))
    if not requested:
        raise RunPodError("RunPod REST v2 serverless create requires at least one GPU type")
    catalogue = await asyncio.to_thread(list_gpu_types_v2, product="SERVERLESS")
    by_id = {entry.id: entry for entry in catalogue}
    missing = [gpu_type_id for gpu_type_id in requested if gpu_type_id not in by_id]
    if missing:
        raise RunPodError(f"RunPod serverless GPU types not found in v2 catalogue: {missing!r}")
    pools = list(dict.fromkeys(by_id[gpu_type_id].pool for gpu_type_id in requested))
    if any(pool is None for pool in pools):
        raise RunPodError("RunPod v2 catalogue omitted a serverless pool for a requested GPU")
    selected_pools = [str(pool) for pool in pools]
    excluded = sorted(
        entry.id
        for entry in catalogue
        if entry.pool in selected_pools and entry.id not in requested
    )
    return V2EndpointGpuRequest(
        pools=selected_pools,
        excludedTypes=excluded,
        count=count,
    )


async def create_endpoint(
    name: str,
    template_id: str | None,
    *,
    gpu_ids: list[str] | None = None,
    gpu_pools: list[str] | None = None,
    excluded_gpu_types: list[str] | None = None,
    gpu_count: int = 1,
    image_name: str | None = None,
    endpoint_type: Literal["QUEUE", "LOAD_BALANCER"] = "QUEUE",
    scaling: EndpointScalingConfig | None = None,
    timeout_s: float = _REST_TIMEOUT_S,
) -> Endpoint:
    """Create a RunPod serverless endpoint.

    Args:
        name: Human-readable endpoint name.
        template_id: RunPod template ID to deploy.
        gpu_ids: GPU type IDs to resolve into strict v2 pool selection.
        gpu_pools: Already-resolved v2 GPU pool IDs. Mutually exclusive with
            ``gpu_ids`` and ``scaling.gpu_type_id``.
        excluded_gpu_types: GPU type IDs excluded from ``gpu_pools``.
        gpu_count: GPUs assigned to each worker.
        image_name: Container image when ``template_id`` is absent.
        endpoint_type: Queue or load-balancer routing semantics.
        scaling: Scaling configuration (workers min/max, idle timeout, GPU,
            flashboot). Defaults to EndpointScalingConfig() if not provided.
        timeout_s: Request timeout in seconds.

    Returns:
        The created Endpoint.
    """
    scaling_config = scaling if scaling is not None else EndpointScalingConfig()
    requested_gpu_ids = gpu_ids
    if requested_gpu_ids is None and scaling_config.gpu_type_id is not None:
        requested_gpu_ids = [scaling_config.gpu_type_id]
    if gpu_pools is not None and requested_gpu_ids is not None:
        raise RunPodError("supply gpu_ids or gpu_pools, not both")
    if gpu_pools is not None:
        gpu = V2EndpointGpuRequest(
            pools=gpu_pools,
            excludedTypes=excluded_gpu_types or [],
            count=gpu_count,
        )
    elif requested_gpu_ids is not None:
        gpu = await resolve_gpu_selection_for_types(requested_gpu_ids, count=gpu_count)
    else:
        raise RunPodError(
            "RunPod REST v2 serverless create requires gpu_ids or gpu_pools; "
            "implicit compute selection is no longer supported"
        )
    if template_id is None and image_name is None:
        raise RunPodError(
            "RunPod REST v2 serverless create requires image_name when template_id is absent"
        )
    scaling_payload = scaling_config.to_request_json()
    if endpoint_type == "LOAD_BALANCER" and scaling_config.scaler_type != "REQUEST_COUNT":
        raise RunPodError("RunPod LOAD_BALANCER endpoints require REQUEST_COUNT scaling")
    payload = V2CreateEndpointRequest(
        name=name,
        type=endpoint_type,
        scaling=scaling_payload["scaling"],
        workers=scaling_payload["workers"],
        flashboot=scaling_payload["flashboot"],
        gpu=gpu,
        templateId=template_id,
        image=image_name,
    ).model_dump(by_alias=True, exclude_none=True)
    result = await _rest_request_async(
        "POST",
        "serverless",
        json_body=payload,
        timeout_s=timeout_s,
    )
    if not isinstance(result, dict):
        raise RunPodError(f"create_endpoint({name}) returned unexpected shape: {result!r}")
    return _parse_endpoint(result)


async def get_endpoint(
    endpoint_id: str,
    *,
    timeout_s: float = _REST_TIMEOUT_S,
) -> Endpoint:
    """Fetch a single serverless endpoint by ID.

    Args:
        endpoint_id: RunPod endpoint ID.
        timeout_s: Request timeout in seconds.

    Returns:
        The Endpoint.
    """
    normalized = _normalize_endpoint_id(endpoint_id)
    result = await _rest_request_async(
        "GET",
        f"serverless/{normalized}",
        timeout_s=timeout_s,
    )
    if not isinstance(result, dict):
        raise RunPodError(f"get_endpoint({normalized}) returned unexpected shape: {result!r}")
    return _parse_endpoint(result)


async def list_endpoints(
    *,
    name_prefix: str | None = None,
    timeout_s: float = _REST_TIMEOUT_S,
) -> list[Endpoint]:
    """List all serverless endpoints for the account.

    Args:
        name_prefix: Optional filter for endpoint names starting with this prefix.
        timeout_s: Request timeout in seconds.

    Returns:
        List of Endpoints, possibly empty.
    """
    result = await _rest_request_async(
        "GET",
        "serverless",
        timeout_s=timeout_s,
    )
    if not isinstance(result, dict) or not isinstance(result.get("endpoints"), list):
        raise RunPodError("list_endpoints returned an invalid REST v2 endpoints envelope")
    endpoints = [_parse_endpoint(item) for item in result["endpoints"] if isinstance(item, dict)]
    if name_prefix is None:
        return endpoints
    return [endpoint for endpoint in endpoints if endpoint.name.startswith(name_prefix)]


async def update_endpoint_scaling(
    endpoint_id: str,
    scaling: EndpointScalingConfig,
    *,
    gpu_pools: list[str] | None = None,
    excluded_gpu_types: list[str] | None = None,
    gpu_count: int = 1,
    timeout_s: float = _REST_TIMEOUT_S,
) -> Endpoint:
    """Update the scaling configuration for a serverless endpoint.

    Args:
        endpoint_id: RunPod endpoint ID.
        scaling: New scaling configuration.
        gpu_pools: Optional explicit REST v2 GPU pool selection.
        excluded_gpu_types: GPU type IDs excluded from ``gpu_pools``.
        gpu_count: GPUs assigned to each worker when GPU selection changes.
        timeout_s: Request timeout in seconds.

    Returns:
        The updated Endpoint.
    """
    normalized = _normalize_endpoint_id(endpoint_id)
    scaling_payload = scaling.to_request_json()
    if gpu_pools is not None and scaling.gpu_type_id is not None:
        raise RunPodError("supply scaling.gpu_type_id or gpu_pools, not both")
    if gpu_pools is not None:
        gpu = V2EndpointGpuRequest(
            pools=gpu_pools,
            excludedTypes=excluded_gpu_types or [],
            count=gpu_count,
        )
    elif scaling.gpu_type_id is not None:
        gpu = await resolve_gpu_selection_for_types(
            [scaling.gpu_type_id],
            count=gpu_count,
        )
    else:
        gpu = None
    payload = V2UpdateEndpointRequest(
        scaling=scaling_payload["scaling"],
        workers=scaling_payload["workers"],
        flashboot=scaling_payload["flashboot"],
        gpu=gpu,
    ).model_dump(by_alias=True, exclude_none=True)
    result = await _rest_request_async(
        "PATCH",
        f"serverless/{normalized}",
        json_body=payload,
        timeout_s=timeout_s,
    )
    if not isinstance(result, dict):
        raise RunPodError(
            f"update_endpoint_scaling({normalized}) returned unexpected shape: {result!r}"
        )
    return _parse_endpoint(result)


async def delete_endpoint(
    endpoint_id: str,
    *,
    timeout_s: float = _REST_TIMEOUT_S,
) -> dict[str, Any]:
    """Delete a serverless endpoint.

    Args:
        endpoint_id: RunPod endpoint ID.
        timeout_s: Request timeout in seconds.

    Returns:
        Empty dict on success.
    """
    normalized = _normalize_endpoint_id(endpoint_id)
    result = await _rest_request_async(
        "DELETE",
        f"serverless/{normalized}",
        timeout_s=timeout_s,
    )
    if not isinstance(result, dict):
        return {}
    return result


__all__ = [
    "DEFAULT_MAX_RETRY_AFTER_DELAY_S",
    "Endpoint",
    "EndpointScalingConfig",
    "RunPodError",
    "RunPodRestError",
    "ServerlessClient",
    "ServerlessResponse",
    "create_endpoint",
    "delete_endpoint",
    "get_endpoint",
    "list_endpoints",
    "parse_retry_after",
    "resolve_gpu_selection_for_types",
    "update_endpoint_scaling",
]
