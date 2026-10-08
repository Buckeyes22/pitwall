"""Shared types, constants, and config coercion for the audit checks."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from pitwall.runpod_client.registry import (
    DOCKER_HUB_PREFIX,
    GHCR_PREFIX,
    GITLAB_REGISTRY_PREFIX,
)
from pitwall.security.pre_spend import PreSpendFinding

SYNC_RESULT_RETENTION_S = 60
ASYNC_RESULT_RETENTION_S = 1800
REQUIRED_DISK_GB_BY_WORKLOAD = {
    "vllm": 80,
    "embed": 40,
    "slim": 20,
}
REQUIRED_REGISTRY_PREFIXES = (
    GHCR_PREFIX,
    GITLAB_REGISTRY_PREFIX,
    DOCKER_HUB_PREFIX,
)
KILL_SWITCH_STEPS = ("list_pods", "terminate_all", "verify")
DEPRECATED_HF_CLI_COMMAND = " ".join(("huggingface-cli", "download"))
VLLM_PROVIDER_TYPES = {"pod_lease", "serverless_lb", "serverless_queue"}
POD_LEASE_PROVIDER_TYPE = "pod_lease"
POD_LEASE_REQUIRED_READINESS_SIGNALS = ("runtime", "port_mappings", "probe_2xx")
MAX_VOLUME_ATTACH_TIMEOUT_S = 300
R2_TEMP_CREDENTIAL_ROUTE_FRAGMENT = "temp-access-credentials"
R2_FORBIDDEN_POD_ENV_KEYS = frozenset(
    {
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "R2_ACCESS_KEY",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_KEY",
        "R2_SECRET_ACCESS_KEY",
    }
)
R2_TEMP_CREDENTIAL_STRATEGIES = frozenset(
    {
        "temp",
        "temporary",
        "temporary_credentials",
        "temp_credentials",
        "temp-access-credentials",
        "temporary-credentials",
        "temp-credentials",
        "r2_temp_credentials",
        "r2-temp-credentials",
    }
)
LEASE_STOP_ROUTE_PATH = "/v1/leases/{lease_id}/stop"
ADMIN_KILL_SWITCH_ROUTE_PATH = "/v1/admin/kill-switch"
_MISSING = object()
EXPECTED_AUDIT_CHECK_COUNT = 19


class AuditSeverity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class CheckFailed(Exception):
    """Raised by an individual check when its invariant is violated."""

    def __init__(
        self,
        check_id: int,
        message: str,
        severity: AuditSeverity = AuditSeverity.HIGH,
        evidence: str | None = None,
        remediation: str | None = None,
    ) -> None:
        self.check_id = check_id
        self.message = message
        self.severity = severity
        self.evidence = evidence or message
        self.remediation = remediation or ""
        super().__init__(f"Check {check_id}: {message}")


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: int
    name: str
    passed: bool
    severity: AuditSeverity
    evidence: str
    remediation: str
    message: str = ""


class AuditConfig(Protocol):
    """Minimal interface needed by check functions.

    Implementations can pull from env vars, a database, or a hardcoded
    test fixture. This keeps checks decoupled from infrastructure.
    """

    def get(self, key: str, default: Any = None) -> Any: ...

    def gpu_ids(self) -> list[str]: ...

    def workloads(self) -> list[dict[str, Any]]: ...

    def launch_params(self) -> dict[str, Any]: ...

    def readiness_config(self) -> dict[str, Any]: ...

    def cost_config(self) -> dict[str, Any]: ...

    def timeout_config(self) -> dict[str, Any]: ...

    def webhook_config(self) -> dict[str, Any]: ...

    def retention_config(self) -> dict[str, Any]: ...

    def volume_config(self) -> dict[str, Any]: ...

    def probe_config(self) -> dict[str, Any]: ...

    def image_config(self) -> dict[str, Any]: ...

    def disk_config(self) -> dict[str, Any]: ...

    def template_config(self) -> dict[str, Any]: ...

    def registry_config(self) -> dict[str, Any]: ...

    def pre_spend_payloads(self) -> list[dict[str, Any]]: ...

    def provider_fixtures(self) -> list[Any]: ...

    def terminate_config(self) -> dict[str, Any]: ...

    def kill_switch_config(self) -> dict[str, Any]: ...


def _bool_config(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_config(check_id: int, name: str, value: Any) -> int:
    if value is None:
        raise CheckFailed(check_id, f"{name} not set")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise CheckFailed(check_id, f"{name} must be an integer") from exc
    return parsed


def _float_config(check_id: int, name: str, value: Any) -> float:
    if value is None or isinstance(value, bool):
        raise CheckFailed(check_id, f"{name} must be a number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise CheckFailed(check_id, f"{name} must be a number") from exc
    if not math.isfinite(parsed):
        raise CheckFailed(check_id, f"{name} must be finite")
    return parsed


def _str_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list | tuple | set):
        return [str(item) for item in value]
    return []


def _pre_spend_payloads(cfg: AuditConfig) -> list[Any]:
    pre_spend_payloads = getattr(cfg, "pre_spend_payloads", None)
    if callable(pre_spend_payloads):
        return list(pre_spend_payloads())
    return _object_list(cfg.get("pre_spend_payloads", []))


def _findings_evidence(findings: list[PreSpendFinding]) -> str:
    return json.dumps(
        [finding.to_dict() for finding in findings],
        sort_keys=True,
        separators=(",", ":"),
    )


def _workload_gpu_ids(cfg: AuditConfig) -> list[str]:
    gpus: list[str] = []
    for workload in cfg.workloads():
        gpu_types = workload.get("gpu_types") or workload.get("gpuTypeIds")
        gpus.extend(_str_list(gpu_types))
    return gpus


def _field(value: Any, name: str, default: Any = _MISSING) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _python_value(value: Any) -> Any:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="python", exclude_none=True)
    return value


def _object_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        if {"id", "name", "provider_type", "config"} & set(value):
            return [value]
        return list(value.values())
    if isinstance(value, list | tuple | set):
        return list(value)
    return [value]
