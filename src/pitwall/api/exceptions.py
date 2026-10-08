"""Mapped API exceptions for Pitwall REST surface.

Each exception class carries its own HTTP status code and response body shape
so FastAPI exception handlers can produce consistent error responses.

The public OpenAPI document describes the resulting response contracts.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any


class PitwallApiError(RuntimeError):
    """Base for all mapped API exceptions."""

    status_code: int = 500
    error_code: str = "internal_error"

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code}


class ApiErrorResponse(PitwallApiError):
    """A service error whose stable, redacted ``{"error": code, ...}`` body is already shaped.

    Routes over services with their own structured errors (RunPod control plane,
    onboarding, webhook configuration) raise this so the response uses the one documented
    envelope instead of nesting the body under ``detail``.
    """

    def __init__(self, status_code: int, body: Mapping[str, Any]) -> None:
        super().__init__(str(body.get("error", self.error_code)))
        self.status_code = status_code
        self._body = dict(body)

    def to_response_body(self) -> dict[str, Any]:
        return dict(self._body)


class CapabilityNotFound(PitwallApiError):
    """Capability name does not exist in the registry."""

    status_code = 404
    error_code = "capability_not_found"

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "name": self.name}


class InvalidProxyPath(PitwallApiError):
    """OpenAI proxy path failed safety validation (absolute URL, traversal, …)."""

    status_code = 400
    error_code = "invalid_proxy_path"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "detail": self.detail}


class CapabilityDisabled(PitwallApiError):
    """Capability exists but is disabled."""

    status_code = 409
    error_code = "capability_disabled"

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "name": self.name}


class CapabilityConflict(PitwallApiError):
    """Duplicate capability name on create."""

    status_code = 409
    error_code = "capability_conflict"

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "name": self.name}


class ProviderNotFound(PitwallApiError):
    """Provider ID does not exist in the registry."""

    status_code = 404
    error_code = "provider_not_found"

    def __init__(self, provider_id: str) -> None:
        super().__init__(provider_id)
        self.provider_id = provider_id

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "id": self.provider_id}


class ProviderUnavailable(PitwallApiError):
    """No enabled, healthy provider can currently serve a capability."""

    status_code = 503
    error_code = "no_providers_available"

    def __init__(
        self,
        capability: str,
        chain: list[str] | None = None,
        *,
        escape_hatch: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(capability)
        self.capability = capability
        self.chain = chain or []
        # The prong-3 own-pod proposal when every free pool is exhausted; absent otherwise.
        self.escape_hatch = dict(escape_hatch) if escape_hatch else None

    def to_response_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "error": self.error_code,
            "capability": self.capability,
            "chain": self.chain,
        }
        if self.escape_hatch is not None:
            body["escape_hatch"] = self.escape_hatch
        return body


class ProviderConflict(PitwallApiError):
    """Duplicate provider name on create."""

    status_code = 409
    error_code = "provider_conflict"

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "name": self.name}


class ProviderCapabilityMissing(PitwallApiError):
    """Provider creation references a capability id that is not registered."""

    status_code = 422
    error_code = "provider_capability_missing"

    def __init__(self, capability_id: str) -> None:
        super().__init__(capability_id)
        self.capability_id = capability_id

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "capability_id": self.capability_id,
            "message": (
                f"capability '{self.capability_id}' does not exist; create it first "
                "with POST /v1/admin/capabilities"
            ),
        }


class RateLimited(PitwallApiError):
    """Local token bucket could not admit a request within the wait budget."""

    status_code = 503
    error_code = "rate_limited"

    def __init__(self, *, retry_after_s: float) -> None:
        super().__init__(f"rate_limited retry_after_s={retry_after_s}")
        self.retry_after_s = retry_after_s

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "retry_after_s": self.retry_after_s}


class LeaseNotFound(PitwallApiError):
    """Lease ID does not exist."""

    status_code = 404
    error_code = "lease_not_found"

    def __init__(self, lease_id: str) -> None:
        super().__init__(lease_id)
        self.lease_id = lease_id

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "id": self.lease_id}


class LeaseStateConflict(PitwallApiError):
    """Lease lifecycle state cannot satisfy the requested operation."""

    status_code = 409
    error_code = "lease_state_conflict"

    def __init__(self, lease_id: str, state: str, operation: str) -> None:
        super().__init__(f"{lease_id}:{state}:{operation}")
        self.lease_id = lease_id
        self.state = state
        self.operation = operation

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "id": self.lease_id,
            "state": self.state,
            "operation": self.operation,
        }


class ChangeSetTooBroad(PitwallApiError):
    """Lease PATCH attempts to change more than one paid-launch axis."""

    status_code = 400
    error_code = "change_set_too_broad"

    def __init__(self, conflicting_fields: list[str]) -> None:
        super().__init__(",".join(conflicting_fields))
        self.conflicting_fields = conflicting_fields

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "conflicting_fields": self.conflicting_fields,
        }


class UnsupportedLeasePatch(PitwallApiError):
    """Lease PATCH contains fields that are not mutable in the public contract."""

    status_code = 422
    error_code = "unsupported_lease_patch"

    def __init__(self, fields: list[str]) -> None:
        super().__init__(",".join(fields))
        self.fields = fields

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "fields": self.fields}


class EmptyLeasePatch(PitwallApiError):
    """Lease PATCH contains no setting to mutate."""

    status_code = 422
    error_code = "empty_lease_patch"


class LeaseExpiryLimitExceeded(PitwallApiError):
    """Lease renewal would put expiry beyond the allowed future horizon."""

    status_code = 409
    error_code = "lease_expiry_limit_exceeded"

    def __init__(self, lease_id: str, max_horizon_minutes: int) -> None:
        super().__init__(lease_id)
        self.lease_id = lease_id
        self.max_horizon_minutes = max_horizon_minutes

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "id": self.lease_id,
            "max_horizon_minutes": self.max_horizon_minutes,
        }


class IdempotencyConflict(PitwallApiError):
    """An idempotency key was reused for a different mutation."""

    status_code = 422
    error_code = "idempotency_conflict"

    def __init__(self, idempotency_key: str) -> None:
        super().__init__(idempotency_key)
        self.idempotency_key = idempotency_key

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "idempotency_key": self.idempotency_key}


class LeaseLaunchInProgress(PitwallApiError):
    """A launch under the same idempotency key (or a serve replay) is not finished yet.

    The first attempt has no lease recorded yet (or stopped before it recorded its lease and
    is left to the reconciler), or a serve replay found the lease not yet ready and verified.
    A retry with the same key later returns the lease; a new key would launch a second pod.
    """

    status_code = 409
    error_code = "mutation_in_progress"

    def __init__(self, workload_id: str | None, *, lease_id: str | None = None) -> None:
        super().__init__(workload_id or lease_id or "")
        self.workload_id = workload_id
        self.lease_id = lease_id

    def to_response_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"error": self.error_code, "workload_id": self.workload_id}
        if self.lease_id is not None:
            body["id"] = self.lease_id
        return body


class LeaseNotServing(LeaseLaunchInProgress):
    """A serve replay found its lease active, but the pod does not list the served model.

    Still ``mutation_in_progress`` (the serve that launched it may be verifying it), with a
    fixed remedy: the lease is active and not serving, so retry later or end it with
    ``pitwall_stop_lease``.
    """

    reason = "lease_not_serving"
    remedy = (
        "the lease is active but its pod is not serving the model; retry later, or end the "
        "lease (id) with pitwall_stop_lease and serve again"
    )

    def to_response_body(self) -> dict[str, Any]:
        return {**super().to_response_body(), "reason": self.reason, "remedy": self.remedy}


class WebhookSubscriptionNotFound(PitwallApiError):
    """Webhook subscription ID does not exist."""

    status_code = 404
    error_code = "webhook_subscription_not_found"

    def __init__(self, subscription_id: int) -> None:
        super().__init__(str(subscription_id))
        self.subscription_id = subscription_id

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "id": self.subscription_id}


class WebhookTargetNotAllowed(PitwallApiError):
    status_code = 422
    error_code = "webhook_target_not_allowed"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "detail": self.detail}


class IdempotencyMismatch(PitwallApiError):
    """Idempotency key was reused with a different request body."""

    status_code = 422
    error_code = "idempotency_mismatch"

    def __init__(self, original_workload_id: str) -> None:
        super().__init__(original_workload_id)
        self.original_workload_id = original_workload_id

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "original_workload_id": self.original_workload_id,
        }


class PreSpendPayloadRejected(PitwallApiError):
    """Inbound payload contains blocked pre-spend PII/secret findings."""

    status_code = 422
    error_code = "pre_spend_payload_rejected"

    def __init__(self, *, decision: str, findings: list[dict[str, Any]]) -> None:
        super().__init__(decision)
        self.decision = decision
        self.findings = findings

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": self.error_code,
            "decision": self.decision,
            "findings": self.findings,
        }


class WorkloadNotFound(PitwallApiError):
    """Workload ID does not exist."""

    status_code = 404
    error_code = "workload_not_found"

    def __init__(self, workload_id: str) -> None:
        super().__init__(workload_id)
        self.workload_id = workload_id

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "id": self.workload_id}


class JobNotReady(PitwallApiError):
    """Job result requested before the workload reached a terminal state."""

    status_code = 409
    error_code = "job_not_ready"

    def __init__(self, workload_id: str, state: str) -> None:
        super().__init__(f"{workload_id}:{state}")
        self.workload_id = workload_id
        self.state = state

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "id": self.workload_id, "state": self.state}


class JobNotCancellable(PitwallApiError):
    """The workload is not an async job, or its provider cannot cancel one."""

    status_code = 409
    error_code = "job_not_cancellable"

    def __init__(self, workload_id: str) -> None:
        super().__init__(workload_id)
        self.workload_id = workload_id

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "id": self.workload_id}


class JobCancelFailed(PitwallApiError):
    """The provider cancellation call failed; the job keeps its state and may be retried."""

    status_code = 502
    error_code = "job_cancel_failed"

    def __init__(self, workload_id: str) -> None:
        super().__init__(workload_id)
        self.workload_id = workload_id

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "id": self.workload_id}


class _ServeError(PitwallApiError):
    def __init__(self, detail: str, **fields: Any) -> None:
        super().__init__(detail)
        self.detail = detail
        self.fields = fields

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, "detail": self.detail, **self.fields}


class ServeConflict(_ServeError):
    status_code = 409
    error_code = "serve_conflict"

    def __init__(self, capability: str, active_model: str | None) -> None:
        super().__init__(
            f"{capability} already has a live lease for {active_model!r}",
            capability=capability,
            active_model=active_model,
        )


class ServeNoServeHistory(_ServeError):
    """A capability-only serve has no complete persisted model selection."""

    status_code = 422
    error_code = "no_serve_history"

    def __init__(self, capability: str) -> None:
        super().__init__(
            f"{capability!r} has no persisted serve model",
            capability=capability,
        )

    def to_response_body(self) -> dict[str, str]:
        return {"error": self.error_code}


class ServeRateRequired(_ServeError):
    status_code = 422
    error_code = "rate_required"


class ServeInvalidGpuClass(_ServeError):
    status_code = 422
    error_code = "invalid_gpu_class"

    def __init__(self, gpu_class: str, suggestions: tuple[str, ...]) -> None:
        self.gpu_class = gpu_class
        self.suggestions = suggestions
        if suggestions:
            detail = (
                f"invalid GPU class {gpu_class!r}; suggested canonical GPU class(es): "
                f"{', '.join(suggestions)}"
            )
        else:
            detail = (
                f"invalid GPU class {gpu_class!r}; no close canonical GPU class "
                "suggestions are available"
            )
        super().__init__(
            detail,
            gpu_class=gpu_class,
            suggestions=list(suggestions),
        )


class ServeUnknownVariant(_ServeError):
    status_code = 422
    error_code = "unknown_variant"

    def __init__(self, model: str, variant: str | None) -> None:
        super().__init__(
            f"unknown variant {variant!r} for {model!r}",
            model=model,
            variant=variant,
        )


class ServeTemplateInvalid(_ServeError):
    status_code = 422
    error_code = "invalid_template"


class ServeTtlBelowStartup(_ServeError):
    """The lease would expire before the model can start answering."""

    status_code = 422
    error_code = "ttl_below_startup"


class ServeStalePrice(_ServeError):
    """Configured price-freshness policy rejected a paid pod launch."""

    status_code = 422
    error_code = "stale_price"


class ServeCapExceeded(_ServeError):
    status_code = 422
    error_code = "cap_exceeded"
    routing_error_code = error_code
    routing_status_code = status_code

    def __init__(
        self,
        *,
        gpu_class: str,
        price_usd_per_hour: Decimal,
        max_usd_per_hour: Decimal,
    ) -> None:
        super().__init__(
            "selected GPU price exceeds max_usd_per_hour",
            gpu_class=gpu_class,
            price_usd_per_hour=str(price_usd_per_hour),
            max_usd_per_hour=str(max_usd_per_hour),
        )

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, **self.fields}


class ServePriceUnknown(_ServeError):
    status_code = 422
    error_code = "price_unknown"
    routing_error_code = error_code
    routing_status_code = status_code

    def __init__(self, *, gpu_class: str, max_usd_per_hour: Decimal) -> None:
        super().__init__(
            "selected GPU price is not live and fresh under max_usd_per_hour",
            gpu_class=gpu_class,
            max_usd_per_hour=str(max_usd_per_hour),
        )

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, **self.fields}


class ServeBudgetExhausted(_ServeError):
    """Serve-specific mapping of the legacy HTTP 402 budget refusal."""

    status_code = 422
    error_code = "budget_exhausted"

    def __init__(self, *, reason: str, snapshot: dict[str, Any]) -> None:
        super().__init__("serve budget admission rejected", reason=reason, snapshot=snapshot)

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, **self.fields}


class ServeKillSwitchEngaged(_ServeError):
    """Serve-specific mapped kill-switch admission refusal."""

    status_code = 422
    error_code = "kill_switch_engaged"

    def __init__(self) -> None:
        super().__init__("kill switch is engaged")

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code}


class ServeLaunchFailed(_ServeError):
    status_code = 503
    error_code = "launch_failed"


class ServeWarmFailed(_ServeError):
    status_code = 503
    error_code = "warm_failed"

    def __init__(self, *, provider_id: str, model_id: str, state: str) -> None:
        super().__init__(
            f"self-hosted model {model_id!r} did not become ready",
            provider_id=provider_id,
            model_id=model_id,
            state=state,
        )

    def to_response_body(self) -> dict[str, Any]:
        return {"error": self.error_code, **self.fields}


class ServeVerificationFailed(_ServeError):
    status_code = 502
    error_code = "served_model_mismatch"

    def __init__(self, expected: str, observed: list[str]) -> None:
        super().__init__(
            f"{expected!r} was not reported by /v1/models",
            expected=expected,
            observed=observed,
        )


def install_api_error_handler(app: Any) -> None:
    """Serialize every ``PitwallApiError`` in the one documented ``{"error": ...}`` envelope."""
    from fastapi.responses import JSONResponse

    async def _handler(_request: Any, exc: PitwallApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response_body())

    app.add_exception_handler(PitwallApiError, _handler)


__all__ = [
    "ApiErrorResponse",
    "ChangeSetTooBroad",
    "CapabilityConflict",
    "CapabilityDisabled",
    "CapabilityNotFound",
    "IdempotencyMismatch",
    "IdempotencyConflict",
    "LeaseLaunchInProgress",
    "LeaseNotServing",
    "JobCancelFailed",
    "JobNotCancellable",
    "JobNotReady",
    "LeaseNotFound",
    "LeaseExpiryLimitExceeded",
    "LeaseStateConflict",
    "PitwallApiError",
    "PreSpendPayloadRejected",
    "EmptyLeasePatch",
    "ProviderConflict",
    "ProviderCapabilityMissing",
    "ProviderNotFound",
    "ProviderUnavailable",
    "RateLimited",
    "ServeConflict",
    "ServeBudgetExhausted",
    "ServeCapExceeded",
    "ServeInvalidGpuClass",
    "ServeLaunchFailed",
    "ServeWarmFailed",
    "ServeKillSwitchEngaged",
    "ServeNoServeHistory",
    "ServeRateRequired",
    "ServePriceUnknown",
    "ServeTemplateInvalid",
    "ServeTtlBelowStartup",
    "ServeStalePrice",
    "ServeUnknownVariant",
    "ServeVerificationFailed",
    "UnsupportedLeasePatch",
    "WebhookSubscriptionNotFound",
    "WebhookTargetNotAllowed",
    "WorkloadNotFound",
    "install_api_error_handler",
]
