"""Semantic validation for dispatch result documents."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any


class ResultError(ValueError):
    pass


REQUIRED_FIELDS = {
    "schemaVersion",
    "dispatchId",
    "workflowId",
    "taskId",
    "provider",
    "model",
    "requestedModel",
    "effort",
    "arguments",
    "providerVersion",
    "status",
    "outcome",
    "createdAt",
    "startedAt",
    "finishedAt",
    "wallMs",
    "exitCode",
    "signal",
    "timeout",
    "sentinel",
    "workspace",
    "output",
    "artifacts",
    "integration",
}


def validate_result(value: Any) -> None:
    if not isinstance(value, dict):
        raise ResultError("result must be an object")
    missing = REQUIRED_FIELDS - set(value)
    extra = set(value) - REQUIRED_FIELDS - {"route"}
    if missing or extra:
        raise ResultError(f"result field mismatch: missing={sorted(missing)} extra={sorted(extra)}")
    if value["schemaVersion"] != 1:
        raise ResultError("unsupported result schema version")
    try:
        uuid.UUID(value["dispatchId"])
    except (ValueError, TypeError, AttributeError) as exc:
        raise ResultError("dispatchId must be a UUID") from exc
    if value["status"] not in {"succeeded", "failed", "timed_out", "cancelled", "preflight_failed"}:
        raise ResultError("invalid result status")
    if value["outcome"] not in {"ok", "error", "timeout", "cancelled"}:
        raise ResultError("invalid result outcome")
    for field in ("provider", "model", "requestedModel", "createdAt", "finishedAt"):
        if not isinstance(value[field], str) or not value[field]:
            raise ResultError(f"{field} must be a non-empty string")
    for field in ("workflowId", "taskId", "effort", "providerVersion", "startedAt"):
        if value[field] is not None and not isinstance(value[field], str):
            raise ResultError(f"{field} must be a string or null")
    if not isinstance(value["arguments"], list) or not all(
        isinstance(item, str) for item in value["arguments"]
    ):
        raise ResultError("arguments must be a list of strings")
    if type(value["wallMs"]) is not int or value["wallMs"] < 0:
        raise ResultError("wallMs must be a non-negative integer")
    if type(value["exitCode"]) is not int or not 0 <= value["exitCode"] <= 255:
        raise ResultError("invalid result exit code")
    if value["signal"] is not None and (type(value["signal"]) is not int or value["signal"] <= 0):
        raise ResultError("signal must be a positive integer or null")
    timeout = value["timeout"]
    if not isinstance(timeout, dict) or set(timeout) != {"seconds", "expired"}:
        raise ResultError("invalid timeout metadata")
    if timeout["seconds"] is not None and (
        not isinstance(timeout["seconds"], (int, float))
        or isinstance(timeout["seconds"], bool)
        or timeout["seconds"] < 0
    ):
        raise ResultError("timeout seconds must be a non-negative number or null")
    if not isinstance(timeout["expired"], bool):
        raise ResultError("timeout expired must be boolean")
    if value["sentinel"] != {"emitted": True, "exit": value["exitCode"]}:
        raise ResultError("sentinel metadata must match the result exit code")
    workspace = value["workspace"]
    if (
        not isinstance(workspace, dict)
        or workspace.get("mode") not in {"shared", "isolated"}
        or not isinstance(workspace.get("path"), str)
    ):
        raise ResultError("invalid workspace metadata")
    output = value["output"]
    if not isinstance(output, dict) or set(output) != {"stdout", "stderr"}:
        raise ResultError("invalid output metadata")
    for channel in ("stdout", "stderr"):
        digest = output[channel]
        if (
            not isinstance(digest, dict)
            or type(digest.get("bytes")) is not int
            or digest["bytes"] < 0
            or not isinstance(digest.get("sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", digest["sha256"]) is None
        ):
            raise ResultError(f"invalid {channel} digest")
    if not isinstance(value["artifacts"], dict) or not all(
        isinstance(key, str) and isinstance(path, str) for key, path in value["artifacts"].items()
    ):
        raise ResultError("artifacts must map strings to strings")
    integration = value["integration"]
    required_integration = {"status", "appliedAt", "target"}
    allowed_integration = required_integration | {"method", "identity", "conflictedFiles"}
    if (
        not isinstance(integration, dict)
        or not required_integration <= set(integration)
        or not set(integration) <= allowed_integration
        or integration.get("status") not in {"not_applied", "applied", "conflicted", "discarded"}
    ):
        raise ResultError("invalid integration status")
    for field in ("appliedAt", "target", "identity"):
        if (
            field in integration
            and integration[field] is not None
            and not isinstance(integration[field], str)
        ):
            raise ResultError(f"integration {field} must be a string or null")
    if integration.get("method") not in {None, "patch", "cherry-pick", "discard"}:
        raise ResultError("invalid integration method")
    if "conflictedFiles" in integration and (
        not isinstance(integration["conflictedFiles"], list)
        or not all(isinstance(path, str) for path in integration["conflictedFiles"])
    ):
        raise ResultError("integration conflictedFiles must be a list of strings")
    if "route" in value:
        route = value["route"]
        required_route_fields = {"spec", "name", "harness", "model", "endpointHost"}
        allowed_route_fields = required_route_fields | {"effort", "effortSource"}
        if (
            not isinstance(route, dict)
            or not required_route_fields <= set(route)
            or not set(route) <= allowed_route_fields
        ):
            raise ResultError(
                "route must contain spec, name, harness, model, endpointHost and only known fields"
            )
        for field in ("spec", "name", "harness", "model"):
            if not isinstance(route[field], str) or not route[field]:
                raise ResultError(f"route {field} must be a non-empty string")
        if route["endpointHost"] is not None and not isinstance(route["endpointHost"], str):
            raise ResultError("route endpointHost must be a string or null")
        if (
            "effort" in route
            and route["effort"] is not None
            and not isinstance(route["effort"], str)
        ):
            raise ResultError("route effort must be a string or null")
        if "effortSource" in route and route["effortSource"] not in {
            None,
            "route",
            "harness",
            "caller",
        }:
            raise ResultError("route effortSource must be route, harness, caller, or null")


SHIM_DONE = re.compile(r"SHIM-DONE exit=([0-9]+)")
SHIM_RESULT_PREFIX = "SHIM-RESULT "


def parse_shim_receipt(stdout: str) -> dict[str, Any]:
    """Return the authoritative trailing ``SHIM-RESULT`` receipt from shim stdout.

    A dispatched child controls its own stdout and can print lines that look like a
    receipt. Only the ``SHIM-RESULT`` line immediately before the final ``SHIM-DONE``
    was written by the shim, so this reads the last two lines and nothing else, then
    cross-checks the receipt's exit code against the sentinel's.
    """

    lines = [line.rstrip("\r\n") for line in stdout.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    if len(lines) < 2:
        raise ResultError("missing trailing receipt/sentinel pair")
    receipt_line, sentinel_line = lines[-2:]
    sentinel = SHIM_DONE.fullmatch(sentinel_line)
    if sentinel is None or not receipt_line.startswith(SHIM_RESULT_PREFIX):
        raise ResultError("last two lines are not SHIM-RESULT then SHIM-DONE")
    try:
        receipt = json.loads(receipt_line[len(SHIM_RESULT_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise ResultError(f"invalid receipt JSON: {exc.msg}") from exc
    if not isinstance(receipt, dict):
        raise ResultError("receipt is not a JSON object")
    if receipt.get("exit") != int(sentinel.group(1)):
        raise ResultError("receipt exit does not match SHIM-DONE")
    return receipt
