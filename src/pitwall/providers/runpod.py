"""RunPod provider plugin adapter."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Annotated, Any, TypedDict, cast
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, SecretStr, field_validator

from pitwall.api.leases import launch as lease_launch
from pitwall.api.leases import teardown as lease_teardown
from pitwall.core.enums import ProviderType, WorkloadState
from pitwall.core.models import Capability
from pitwall.core.models import Provider as ProviderRecord
from pitwall.cost.estimator import TaggedPricingModel, parse_pricing_model
from pitwall.cost.reconcile_cost import ProviderActualCostResult
from pitwall.providers.interface import (
    ActualCostRequest,
    AsyncInferenceCancelRequest,
    AsyncInferenceCancelResult,
    AsyncInferenceRequest,
    AsyncInferenceStatusRequest,
    AsyncInferenceStatusResult,
    AsyncInferenceSubmission,
    CredentialInput,
    InferenceRequest,
    InferenceResult,
    ProviderCapability,
    ProviderDeclaration,
    ProvisionRequest,
    ProvisionResult,
    ReconcileRequest,
    ReconcileResult,
    ResourceStatus,
    StatusRequest,
    StatusResult,
    TeardownRequest,
    TeardownResult,
    optional_config_string,
    resolve_adapter_credentials,
)
from pitwall.runpod_client import pods as runpod_pods
from pitwall.runpod_client.billing import RunPodBillingClient
from pitwall.runpod_client.graphql import RUNPOD_GRAPHQL_URL
from pitwall.runpod_client.queue import QueueClient
from pitwall.runpod_client.serverless_lb import ServerlessLBClient
from pitwall.runpod_market import (
    RunpodActualCostReference,
    RunpodBillingCategory,
    RunpodMarketService,
)

SafeProviderUrl = Annotated[str, AfterValidator(lambda value: _safe_provider_url(value))]


class RunPodCredentials(BaseModel):
    """Credentials required for RunPod provider operations."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    api_key: SecretStr = Field(min_length=1)
    graphql_url: SafeProviderUrl = RUNPOD_GRAPHQL_URL
    rest_api_url: SafeProviderUrl | None = None
    rest_v1_api_url: SafeProviderUrl | None = None

    @field_validator("api_key")
    @classmethod
    def _validate_api_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("api_key must be non-empty")
        return value


class UnsupportedInferenceOperationError(ValueError):
    """The provider does not implement the requested inference operation."""


_SERVERLESS_QUEUE = ProviderType.SERVERLESS_QUEUE.value
_SERVERLESS_LB = ProviderType.SERVERLESS_LB.value
_PUBLIC_ENDPOINT = ProviderType.PUBLIC_ENDPOINT.value
_POD_LEASE = ProviderType.POD_LEASE.value


def runpod_openai_base_url(provider_type: str, endpoint_id: str) -> str:
    """Return the RunPod OpenAI-compatible base URL for a provider surface."""

    if provider_type == _POD_LEASE:
        raise ValueError("pod_lease providers do not expose openai_base_url")
    if provider_type == _SERVERLESS_LB:
        return f"https://{endpoint_id}.api.runpod.ai/openai/v1"
    return f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1"


def runpod_lb_base_url(endpoint_id: str) -> str:
    """Return the RunPod LB base URL for a serverless_lb endpoint."""

    return f"https://{endpoint_id}.api.runpod.ai"


def _validate_runpod_endpoint(provider_type: str, endpoint_id: str | None) -> None:
    if provider_type == _SERVERLESS_LB and not endpoint_id:
        raise ValueError(
            "serverless_lb providers must register an existing runpod_endpoint_id; "
            "Pitwall does not create LB endpoints"
        )


def _validate_runpod_url(
    provider_type: str | None,
    endpoint_id: str | None,
    config: Mapping[str, Any],
) -> None:
    openai_base_url = optional_config_string(config, "openai_base_url")
    if openai_base_url is not None:
        if provider_type is None:
            return
        if endpoint_id is None:
            raise ValueError("config.openai_base_url requires runpod_endpoint_id")
        expected = runpod_openai_base_url(provider_type, endpoint_id)
        if openai_base_url != expected:
            raise ValueError(
                f"config.openai_base_url must be {expected!r} for provider_type {provider_type!r}"
            )

    lb_base_url = optional_config_string(config, "lb_base_url")
    if lb_base_url is None:
        return
    if provider_type is not None and provider_type != _SERVERLESS_LB:
        raise ValueError("config.lb_base_url is only valid for serverless_lb providers")
    if endpoint_id is None:
        raise ValueError("config.lb_base_url requires runpod_endpoint_id")
    expected_lb = runpod_lb_base_url(endpoint_id)
    if lb_base_url.rstrip("/") != expected_lb:
        raise ValueError(
            f"config.lb_base_url must be {expected_lb!r} for runpod_endpoint_id {endpoint_id!r}"
        )


def _runpod_seed_config(
    spec: Mapping[str, Any],
    config: dict[str, Any],
    provider_type: str,
    endpoint_id: str | None,
) -> dict[str, Any]:
    if endpoint_id is not None:
        if provider_type == _SERVERLESS_LB:
            config.setdefault("lb_base_url", runpod_lb_base_url(endpoint_id))
        elif provider_type in (_SERVERLESS_QUEUE, _PUBLIC_ENDPOINT):
            config.setdefault("openai_base_url", runpod_openai_base_url(provider_type, endpoint_id))
    return config


def _runpod_proxy_headers(provider: Any, outbound: dict[str, str]) -> dict[str, str] | None:
    """A pod's control-plane credential must never reach a model pod."""

    if str(getattr(provider, "provider_type", "")) != _POD_LEASE:
        return None
    config = getattr(provider, "config", None)
    configured_ref = config.get("api_key_env") if isinstance(config, Mapping) else None
    if not isinstance(configured_ref, str) or not configured_ref.strip():
        # Including when an operator chose a custom control-plane env name.
        return outbound
    credential = os.environ.get(configured_ref, "").strip()
    if credential:
        outbound["authorization"] = f"Bearer {credential}"
    return outbound


def _runpod_derive_openai_base_url(provider: Any) -> str | None:
    # local import: routing.openai reads declarations through the registry
    from pitwall.routing.openai import (
        _config_value,
        _field,
        _non_empty_string,
        pod_lease_base_url,
    )

    provider_type = _non_empty_string(_field(provider, "provider_type"))
    if provider_type == _POD_LEASE:
        return pod_lease_base_url(provider)
    configured = _non_empty_string(_config_value(provider, "openai_base_url"))
    if configured is not None:
        return configured.rstrip("/")
    endpoint_id = _non_empty_string(_field(provider, "runpod_endpoint_id"))
    if endpoint_id is None:
        endpoint_id = _non_empty_string(_config_value(provider, "runpod_endpoint_id"))
    if endpoint_id is None:
        endpoint_id = _non_empty_string(_config_value(provider, "endpoint_id"))
    if endpoint_id is None:
        return None
    if provider_type == _SERVERLESS_LB:
        return f"https://{endpoint_id}.api.runpod.ai/openai/v1"
    if provider_type in (_SERVERLESS_QUEUE, _PUBLIC_ENDPOINT, None):
        return f"https://api.runpod.ai/v2/{endpoint_id}/openai/v1"
    return None


async def _runpod_probe_endpoint(prov: Mapping[str, Any], api_key: str) -> bool:
    from pitwall.runpod_client.lb import LBClient  # local: only the reconciler's probe needs it

    result = await LBClient(api_key=api_key).probe(prov["runpod_endpoint_id"])
    return bool(result.healthy)


async def _runpod_poll_job_status(
    provider_type: str, endpoint_id: str, job_id: str, api_key: str
) -> str | None:
    if provider_type == _SERVERLESS_QUEUE:
        from pitwall.runpod_client.queue import QueueClient as PollQueueClient  # local: reconciler

        queue_job = await PollQueueClient(api_key=api_key).status(endpoint_id, job_id)
        return cast(str | None, queue_job.status)
    if provider_type == _POD_LEASE:
        # Strict: an unreachable RunPod raises and skips this sweep; only a 404 is absent.
        pod = await runpod_pods.get_pod_strict(job_id)
        if pod is None:
            return "TIMED_OUT"
        runtime = pod.get("runtime") or {}
        return cast(str | None, runtime.get("podStatus") or runtime.get("status"))
    return None


RUNPOD_DECLARATION = ProviderDeclaration(
    provider_types=frozenset({_SERVERLESS_QUEUE, _SERVERLESS_LB, _PUBLIC_ENDPOINT, _POD_LEASE}),
    default_for_untyped=True,
    openai_proxy_types=frozenset({_SERVERLESS_QUEUE, _SERVERLESS_LB, _PUBLIC_ENDPOINT, _POD_LEASE}),
    derive_openai_base_url=_runpod_derive_openai_base_url,
    openai_url_types=frozenset({_SERVERLESS_QUEUE, _SERVERLESS_LB, _PUBLIC_ENDPOINT}),
    lb_url_types=frozenset({_SERVERLESS_LB}),
    self_hosted_types=frozenset({_PUBLIC_ENDPOINT}),
    openai_base_url=runpod_openai_base_url,
    lb_base_url=runpod_lb_base_url,
    validate_endpoint=_validate_runpod_endpoint,
    validate_url=_validate_runpod_url,
    seed_config=_runpod_seed_config,
    proxy_outbound_headers=_runpod_proxy_headers,
    health_probe_types=(_SERVERLESS_LB, _PUBLIC_ENDPOINT),
    probe_endpoint=_runpod_probe_endpoint,
    poll_job_status=_runpod_poll_job_status,
)


class RunPodProvider:
    """Reference provider plugin backed by the existing RunPod services."""

    id = "runpod"
    name = "RunPod"
    #: What ``ProviderCapability.SYNC_INFERENCE`` means for this provider.
    sync_inference_description = (
        "Synchronous inference supports embedding requests only (payload 'texts'); "
        "chat and text-generation requests are rejected."
    )
    credential_schema = RunPodCredentials
    declaration = RUNPOD_DECLARATION
    capabilities = frozenset(
        {
            ProviderCapability.COMPUTE,
            ProviderCapability.SYNC_INFERENCE,
            ProviderCapability.ASYNC_INFERENCE,
            ProviderCapability.ASYNC_STATUS,
            ProviderCapability.ASYNC_CANCEL,
            ProviderCapability.ACTUAL_COST,
        }
    )

    def pricing_model(
        self,
        capability: Capability,
        provider_record: ProviderRecord,
    ) -> TaggedPricingModel:
        return parse_pricing_model(provider_record.config, cost_mode=capability.cost_mode)

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        """Run the existing synchronous serverless-LB embedding call."""

        if "texts" not in request.payload:
            raise UnsupportedInferenceOperationError(
                "RunPod synchronous inference supports embedding requests only; "
                "the payload must contain 'texts'"
            )
        credentials = _runpod_credentials(request.credentials)
        endpoint_id = request.provider_record.runpod_endpoint_id
        if endpoint_id is None:
            raise ValueError("RunPod synchronous inference requires runpod_endpoint_id")
        client = ServerlessLBClient(
            lb_base_url=f"https://{endpoint_id}.api.runpod.ai",
            api_key=_api_key(credentials),
            # The broker calls the RunPod load balancer itself: Pitwall mode would loop the
            # request back into the broker carrying the RunPod key.
            via_pitwall=False,
            retry_attempts=1,
        )
        try:
            output = await client.embed(
                texts=request.payload.get("texts", []),
                return_dense=request.payload.get("return_dense", True),
                return_sparse=request.payload.get("return_sparse", True),
                return_colbert=request.payload.get("return_colbert", False),
            )
        finally:
            await client.aclose()
        raw = dict(output) if isinstance(output, Mapping) else {}
        return InferenceResult(
            provider_id=request.provider_record.id,
            output=output,
            raw=raw,
        )

    async def submit(self, request: AsyncInferenceRequest) -> AsyncInferenceSubmission:
        """Submit through the existing RunPod queue client call shape."""

        credentials = _runpod_credentials(request.credentials)
        endpoint_id = _runpod_endpoint_id(request.provider_record, operation="submission")
        queue_job = await QueueClient(api_key=_api_key(credentials)).run(
            endpoint_id,
            input=dict(request.payload),
            webhook=request.webhook_url,
        )
        return AsyncInferenceSubmission(
            provider_id=request.provider_record.id,
            external_job_id=queue_job.id,
            state=_async_workload_state(queue_job.status),
            raw=dict(queue_job.raw),
        )

    async def job_status(
        self,
        request: AsyncInferenceStatusRequest,
    ) -> AsyncInferenceStatusResult:
        """Read status through the existing RunPod queue client call shape."""

        credentials = _runpod_credentials(request.credentials)
        endpoint_id = _runpod_endpoint_id(request.provider_record, operation="status")
        queue_job = await QueueClient(api_key=_api_key(credentials)).status(
            endpoint_id,
            request.external_job_id,
        )
        return AsyncInferenceStatusResult(
            provider_id=request.provider_record.id,
            external_job_id=queue_job.id,
            state=_async_workload_state(queue_job.status),
            output=queue_job.output,
            error=queue_job.error,
            raw=dict(queue_job.raw),
        )

    async def cancel_job(
        self,
        request: AsyncInferenceCancelRequest,
    ) -> AsyncInferenceCancelResult:
        """Cancel through the existing RunPod queue client call shape."""

        credentials = _runpod_credentials(request.credentials)
        endpoint_id = _runpod_endpoint_id(request.provider_record, operation="cancellation")
        result = await QueueClient(api_key=_api_key(credentials)).cancel(
            endpoint_id,
            request.external_job_id,
        )
        return AsyncInferenceCancelResult(
            provider_id=request.provider_record.id,
            external_job_id=request.external_job_id,
            cancelled=result.cancelled,
            raw=dict(result.raw),
        )

    async def provision(self, request: ProvisionRequest) -> ProvisionResult:
        credentials = _runpod_credentials(request.credentials)
        raw = await lease_launch.run_launch(
            pool=request.context.pool,
            capability=request.capability,
            provider=request.provider_record,
            request_id=request.request_id,
            extra_env=dict(request.extra_env) if request.extra_env is not None else None,
            payload=dict(request.payload),
            budget_gate=request.budget_gate,
            idempotency_key=request.idempotency_key,
            dry_run=request.dry_run,
            **_launch_credential_kwargs(credentials),
        )
        return ProvisionResult(
            provider_id=_string_or_default(raw.get("provider_id"), request.provider_record.id),
            external_id=_optional_string(raw.get("pod_id")),
            lease_id=_optional_string(raw.get("lease_id")),
            raw=dict(raw),
        )

    async def status(self, request: StatusRequest) -> StatusResult:
        credentials = _runpod_credentials(request.credentials)
        pod = await runpod_pods.get_pod_strict(
            request.external_id,
            **_rest_credential_kwargs(credentials),
        )
        if pod is None:
            return StatusResult(
                provider_id=request.provider_record.id,
                external_id=request.external_id,
                status=ResourceStatus.TERMINATED,
                raw={},
            )
        return StatusResult(
            provider_id=request.provider_record.id,
            external_id=request.external_id,
            status=_pod_status(pod),
            raw=dict(pod),
        )

    async def reconcile(self, request: ReconcileRequest) -> ReconcileResult:
        credentials = _runpod_credentials(request.credentials)
        if request.external_ids:
            resources: list[Mapping[str, Any]] = []
            for external_id in request.external_ids:
                status = await self.status(
                    StatusRequest(
                        context=request.context,
                        provider_record=request.provider_record,
                        credentials=request.credentials,
                        external_id=external_id,
                    )
                )
                resources.append(status.raw)
            return ReconcileResult(
                provider_id=request.provider_record.id,
                checked=len(resources),
                updated=0,
                raw={"resources": resources},
            )

        pods = await runpod_pods._get_pods(**_rest_credential_kwargs(credentials))
        return ReconcileResult(
            provider_id=request.provider_record.id,
            checked=len(pods),
            updated=0,
            raw={"resources": pods},
        )

    async def teardown(self, request: TeardownRequest) -> TeardownResult:
        credentials = _runpod_credentials(request.credentials)
        result = await lease_teardown.run_teardown(
            request.lease_id,
            pool=request.context.pool,
            redis_client=request.context.redis_client,
            reason=request.reason,
            now=request.context.now,
            terminal_state=request.terminal_state,
            **_rest_credential_kwargs(credentials),
        )
        lease = result.lease
        return TeardownResult(
            provider_id=lease.provider_id,
            lease_id=lease.id,
            external_id=lease.external_resource_id or lease.runpod_pod_id,
            raw={
                "event": result.event,
                "published_subscribers": result.published_subscribers,
                "state": _state_value(lease.state),
            },
        )

    async def actual_cost(self, request: ActualCostRequest) -> ProviderActualCostResult:
        """Read only exact Pod billing buckets through the RP-01 service."""

        credentials = _runpod_credentials(request.credentials)
        billing = RunPodBillingClient(
            api_key=_api_key(credentials),
            rest_v1_api_url=credentials.rest_v1_api_url,
        )
        service = RunpodMarketService(None, None, billing)
        try:
            return await service.provider_actual_cost(
                provider_id=request.provider_record.id,
                start_day=request.start_day,
                end_day=request.end_day,
                references=tuple(
                    RunpodActualCostReference(
                        workload_id=item.workload_id,
                        category=cast(RunpodBillingCategory, item.category),
                        external_resource_id=item.external_resource_id,
                    )
                    for item in request.references
                ),
            )
        finally:
            await service.aclose()


def _safe_provider_url(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("url must be non-empty")
    parsed = urlsplit(stripped)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("url must not include user info")
    if parsed.query or parsed.fragment:
        raise ValueError("url must not include query strings or fragments")
    return stripped.rstrip("/")


def _optional_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _runpod_credentials(value: CredentialInput) -> RunPodCredentials:
    return resolve_adapter_credentials(value, RunPodCredentials, adapter_id="runpod")


def _api_key(credentials: RunPodCredentials) -> str:
    return credentials.api_key.get_secret_value()


def _runpod_endpoint_id(provider_record: ProviderRecord, *, operation: str) -> str:
    endpoint_id = provider_record.runpod_endpoint_id
    if endpoint_id is None:
        raise ValueError(f"RunPod asynchronous inference {operation} requires runpod_endpoint_id")
    return endpoint_id


class _LaunchCredentialKwargs(TypedDict, total=False):
    api_key: str
    graphql_url: str
    rest_api_url: str
    rest_v1_api_url: str


def _launch_credential_kwargs(credentials: RunPodCredentials) -> _LaunchCredentialKwargs:
    kwargs = _LaunchCredentialKwargs(
        api_key=_api_key(credentials),
        graphql_url=credentials.graphql_url,
    )
    if credentials.rest_api_url is not None:
        kwargs["rest_api_url"] = credentials.rest_api_url
    if credentials.rest_v1_api_url is not None:
        kwargs["rest_v1_api_url"] = credentials.rest_v1_api_url
    return kwargs


class _RestCredentialKwargs(TypedDict, total=False):
    api_key: str
    rest_api_url: str


def _rest_credential_kwargs(credentials: RunPodCredentials) -> _RestCredentialKwargs:
    kwargs: _RestCredentialKwargs = {"api_key": _api_key(credentials)}
    if credentials.rest_api_url is not None:
        kwargs["rest_api_url"] = credentials.rest_api_url
    return kwargs


def _string_or_default(value: object, default: str) -> str:
    return _optional_string(value) or default


def _pod_status(pod: Mapping[str, Any]) -> ResourceStatus:
    status_text = _status_text(pod)
    if status_text in {"running", "ready"} or _has_runtime_signal(pod):
        return ResourceStatus.RUNNING
    if status_text in {"creating", "starting", "pending", "initializing"}:
        return ResourceStatus.PROVISIONING
    if status_text in {"failed", "error", "unhealthy"}:
        return ResourceStatus.FAILED
    if status_text in {"exited", "stopped", "terminated", "deleted"}:
        return ResourceStatus.TERMINATED
    return ResourceStatus.UNKNOWN


def _status_text(pod: Mapping[str, Any]) -> str:
    for key in ("desiredStatus", "desired_status", "status", "state"):
        value = pod.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _has_runtime_signal(pod: Mapping[str, Any]) -> bool:
    runtime = pod.get("runtime")
    return isinstance(runtime, Mapping) and bool(runtime)


def _state_value(state: object) -> str:
    value = getattr(state, "value", None)
    return value if isinstance(value, str) else str(state)


def _async_workload_state(status: str) -> WorkloadState:
    normalized = status.strip().upper().replace("-", "_").replace(" ", "_")
    states = {
        "IN_QUEUE": WorkloadState.QUEUED,
        "QUEUED": WorkloadState.QUEUED,
        "IN_PROGRESS": WorkloadState.RUNNING,
        "RUNNING": WorkloadState.RUNNING,
        "COMPLETED": WorkloadState.COMPLETED,
        "FAILED": WorkloadState.FAILED,
        "CANCELLED": WorkloadState.CANCELLED,
        "TIMED_OUT": WorkloadState.TIMED_OUT,
    }
    try:
        return states[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported RunPod queue status {status!r}") from exc


__all__ = [
    "RUNPOD_DECLARATION",
    "RunPodCredentials",
    "RunPodProvider",
    "SafeProviderUrl",
    "runpod_lb_base_url",
    "runpod_openai_base_url",
]
