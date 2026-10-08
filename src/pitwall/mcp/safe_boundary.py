"""Non-reflecting error boundary for MCPServer tool calls."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from typing import Any

from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, CallToolRequestParams, CallToolResult, TextContent

_BOUNDARY_MARKER = "_pitwall_safe_call_boundary"

# One stdio broker serves one client. The burst covers a legitimate sweep (the release
# journey sends 162 calls in one session); the refill rate caps a runaway loop.
RATE_LIMIT_PER_SECOND = 20.0
RATE_LIMIT_BURST = 200

# Budget refusals carry Pitwall's own budget state so an agent can act without asking a
# person which rule fired. Only these server-computed fields cross the boundary.
_BUDGET_ERRORS = frozenset({"budget_rejected", "sub_budget_rejected", "budget_exhausted"})
_BUDGET_REASONS = frozenset({"monthly_budget", "per_request_cap"})
_SNAPSHOT_KEYS = (
    "monthly_budget_usd",
    "per_request_max_usd",
    "mtd_spend_usd",
    "estimate_usd",
    "budget_remaining_usd",
)
_DECIMAL = re.compile(r"-?\d+(?:\.\d+)?")
BUDGET_REMEDY = (
    "raise the limit with pitwall_budget_set (a reason is required), "
    "or lower ttl_minutes or max_cost_per_hour"
)


# Codes whose documented remedy is a retry with the same idempotency_key (§3.4 and the
# RunPod resource-controls guide). A new key would repeat the mutation, so the client gets
# a fixed, server-written remedy and the ``retryable`` flag; a created pod's id crosses too.
_SAME_KEY_RETRY_ERRORS = frozenset({"audit_unavailable"})
SAME_KEY_RETRY_REMEDY = (
    "retry with the same idempotency_key once the broker database is available; "
    "a new key would repeat the mutation"
)
CREATED_POD_RETRY_REMEDY = (
    "the pod was created and is kept; retry with the same idempotency_key once the broker "
    "database is available to record its lease; a new key would create a second pod"
)
_PROVIDER_RESOURCE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")

# A serve replay whose active lease does not serve the model is still mutation_in_progress,
# with a fixed remedy so a caller without a key does not retry forever. Only the server's
# ``reason`` marker selects it, and only a well-formed lease id crosses.
_IN_PROGRESS_ERROR = "mutation_in_progress"
_LEASE_NOT_SERVING = "lease_not_serving"
LEASE_NOT_SERVING_REMEDY = (
    "the lease is active but its pod is not serving the model; retry later, or end the lease "
    "(id) with pitwall_stop_lease and serve again"
)


def _in_progress_detail(data: Mapping[str, Any]) -> dict[str, Any]:
    if data.get("reason") != _LEASE_NOT_SERVING:
        return {}
    detail: dict[str, Any] = {"reason": _LEASE_NOT_SERVING, "remedy": LEASE_NOT_SERVING_REMEDY}
    lease_id = data.get("id")
    if isinstance(lease_id, str) and _PROVIDER_RESOURCE_ID.fullmatch(lease_id):
        detail["id"] = lease_id
    return detail


def _same_key_retry_detail(data: Mapping[str, Any]) -> dict[str, Any]:
    """The remedy and flags a same-key-retry code carries; never request or exception text.

    ``resource_id`` crosses only for a created pod (``changed``, operation ``pod.create``,
    resource type pod), where it is the id RunPod returned, not a value from the request.
    """
    detail: dict[str, Any] = {"remedy": SAME_KEY_RETRY_REMEDY}
    if data.get("retryable") is True:
        detail["retryable"] = True
    resource_id = data.get("resource_id")
    if (
        data.get("changed") is True
        and data.get("operation") == "pod.create"
        and data.get("resource_type") == "pod"
        and isinstance(resource_id, str)
        and _PROVIDER_RESOURCE_ID.fullmatch(resource_id)
    ):
        detail.update(changed=True, resource_id=resource_id, remedy=CREATED_POD_RETRY_REMEDY)
    return detail


# Idempotency-key refusals: a fixed, server-written remedy and a fixed ``retryable`` flag,
# so an agent knows whether to reuse its key or take a new one. Raised by the RunPod
# control-plane journal and by the lease_pod/serve_model and inference replay paths.
_IDEMPOTENCY_KEY_REMEDIES: dict[str, tuple[str, bool]] = {
    "mutation_in_progress": (
        "retry with the same idempotency_key once the first call finishes",
        True,
    ),
    "idempotency_conflict": ("this key is spent; use a new idempotency_key", False),
    "idempotency_mismatch": (
        "this key was used for a different request; use a new idempotency_key",
        False,
    ),
}


def _idempotency_key_detail(error: str) -> dict[str, Any]:
    remedy, retryable = _IDEMPOTENCY_KEY_REMEDIES[error]
    return {"remedy": remedy, "retryable": retryable}


def _budget_detail(data: Mapping[str, Any]) -> dict[str, Any]:
    detail: dict[str, Any] = {}
    if data.get("reason") in _BUDGET_REASONS:
        detail["reason"] = data["reason"]
    snapshot = data.get("snapshot")
    if isinstance(snapshot, Mapping):
        values = {
            key: str(snapshot[key])
            for key in _SNAPSHOT_KEYS
            if key in snapshot and _DECIMAL.fullmatch(str(snapshot[key]))
        }
        if values:
            detail["snapshot"] = values
    if detail:
        detail["remedy"] = BUDGET_REMEDY
    return detail


def _stable_error_payload(exc: Exception) -> dict[str, Any]:
    """Return only a stable error code, never exception or request text.

    Budget refusals add Pitwall's own budget state, same-key-retry codes add a fixed remedy
    and their ``retryable`` flag (plus a created pod's id), idempotency-key refusals add a
    fixed remedy and a fixed ``retryable`` flag, and an active lease that is not serving
    replaces that remedy with a fixed ``pitwall_stop_lease`` one and adds its lease id, all
    server-written.
    """
    cause = exc.__cause__ if isinstance(exc, ToolError) else exc
    if isinstance(cause, MCPError) and isinstance(cause.error.data, dict):
        error = cause.error.data.get("error")
        if isinstance(error, str) and error:
            payload: dict[str, Any] = {"error": error}
            if error in _BUDGET_ERRORS:
                payload.update(_budget_detail(cause.error.data))
            elif error in _SAME_KEY_RETRY_ERRORS:
                payload.update(_same_key_retry_detail(cause.error.data))
            elif error in _IDEMPOTENCY_KEY_REMEDIES:
                payload.update(_idempotency_key_detail(error))
                if error == _IN_PROGRESS_ERROR:
                    payload.update(_in_progress_detail(cause.error.data))
            return payload
    # Pitwall API errors declare their code on the class, never from request text, so it is the
    # same stable code the REST API returns. An instance attribute is not trusted.
    class_code = getattr(type(cause), "error_code", None) if cause is not None else None
    if isinstance(class_code, str) and class_code:
        payload = {"error": class_code}
        to_body = getattr(type(cause), "to_response_body", None)
        if class_code in _BUDGET_ERRORS and callable(to_body):
            payload.update(_budget_detail(to_body(cause)))
        elif class_code in _IDEMPOTENCY_KEY_REMEDIES:
            payload.update(_idempotency_key_detail(class_code))
            if class_code == _IN_PROGRESS_ERROR and callable(to_body):
                payload.update(_in_progress_detail(to_body(cause)))
        return payload
    return {"error": "tool_execution_failed"}


def _payload_result(payload: dict[str, Any]) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, sort_keys=True))],
        structured_content=payload,
        is_error=True,
    )


def _error_result(exc: Exception) -> CallToolResult:
    return _payload_result(_stable_error_payload(exc))


class _RateLimiter:
    """One token bucket per server process: MCP 2026-07-28 tools security requires rate limits."""

    def __init__(self) -> None:
        self._tokens = float(RATE_LIMIT_BURST)
        self._stamp = time.monotonic()

    def acquire(self) -> float:
        """Return 0.0 when a call may proceed, else the seconds until a token is available."""
        now = time.monotonic()
        self._tokens = min(
            float(RATE_LIMIT_BURST), self._tokens + (now - self._stamp) * RATE_LIMIT_PER_SECOND
        )
        self._stamp = now
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return 0.0
        # Never round a refusal down to 0.0, which the caller would read as permission.
        return max(round((1.0 - self._tokens) / RATE_LIMIT_PER_SECOND, 3), 0.001)


def _validation_fields(exc: Exception, declared: frozenset[str]) -> list[str] | None:
    """Declared parameter names a pydantic argument-validation failure points at; never values.

    The SDK raises a plain ``ToolError`` for arguments that fail the input schema. A pydantic
    error raised inside the tool body arrives as ``UnexpectedToolError`` (a ``ToolError``
    subclass) and is an execution failure, not the caller's mistake, so only the exact class
    counts here.
    """
    if type(exc) is not ToolError:
        return None
    cause = exc.__cause__
    errors = getattr(cause, "errors", None)
    if cause is None or not cause.__class__.__module__.startswith(("pydantic", "pydantic_core")):
        return None
    if not callable(errors):
        return None
    names = {str(error["loc"][0]) for error in errors() if error.get("loc")}
    return sorted(names & declared)


def install_safe_call_boundary(server: Any) -> None:
    """Replace the SDK 2 ``tools/call`` handler with one that never reflects request text."""
    if getattr(server, _BOUNDARY_MARKER, False):
        return

    # Every tool declares its complete argument set. An undeclared argument (a misspelled
    # ``dry_run``, say) is refused instead of silently dropped into a real, possibly paid call.
    declared: dict[str, frozenset[str]] = {}
    for tool in server._tool_manager.list_tools():
        tool.parameters["additionalProperties"] = False
        declared[tool.name] = frozenset(tool.parameters.get("properties", {}))

    limiter = _RateLimiter()

    async def safe_call_tool(ctx: Any, params: CallToolRequestParams) -> Any:
        # A refused call is still a call: the bucket is drawn before any refusal is decided.
        wait = limiter.acquire()
        if wait:
            return _payload_result({"error": "rate_limited", "retry_after_s": wait})
        allowed = declared.get(params.name)
        if allowed is None:
            raise MCPError(
                code=INVALID_PARAMS, message="Unknown tool", data={"error": "unknown_tool"}
            )
        arguments = params.arguments or {}
        if not set(arguments) <= allowed:
            return _payload_result({"error": "invalid_tool_arguments", "allowed": sorted(allowed)})
        context = Context(
            request_context=ctx,
            mcp_server=server,
            input_params=params,
            subscriptions=server._subscriptions,
        )
        try:
            return await server.call_tool(params.name, arguments, context)
        except Exception as exc:  # reason: MCP transport must fail closed without reflection.
            fields = _validation_fields(exc, allowed)
            if fields is not None:
                return _payload_result({"error": "invalid_tool_arguments", "fields": fields})
            return _error_result(exc)

    server._lowlevel_server.add_request_handler("tools/call", CallToolRequestParams, safe_call_tool)
    setattr(server, _BOUNDARY_MARKER, True)


__all__ = ["install_safe_call_boundary"]
