"""Audit checks 03-06, 08, 10: readiness, cost order, timeouts, retention, probes."""

from __future__ import annotations

from pitwall.audit._common import (
    ASYNC_RESULT_RETENTION_S,
    SYNC_RESULT_RETENTION_S,
    AuditConfig,
    CheckFailed,
    _bool_config,
    _int_config,
    _str_list,
)
from pitwall.audit._fixture_cases import (
    _check_order_has_cost_before_readiness,
    _check_pod_lease_cost_order_cases,
    _check_pod_lease_readiness_cases,
)
from pitwall.audit._introspect import facts_of
from pitwall.runpod_client import pods


def check_03_readiness_runtime(cfg: AuditConfig) -> str:
    rc = cfg.readiness_config()
    probe_field = str(rc.get("probe_field", ""))
    if probe_field != "runtime":
        raise CheckFailed(
            3,
            f"readiness probes '{probe_field}' instead of 'runtime' — "
            "desiredStatus is not sufficient",
        )
    desired_running_without_runtime = {
        "id": "pod-audit",
        "desiredStatus": "RUNNING",
        "runtime": None,
        "portMappings": {"8000": 12345},
    }
    if pods._pod_has_runtime(desired_running_without_runtime):
        raise CheckFailed(3, "desiredStatus=RUNNING was treated as ready without runtime")
    runtime_with_ports = {
        "id": "pod-audit",
        "desiredStatus": "PENDING",
        "runtime": {"ports": [{"privatePort": 8000, "type": "http"}]},
    }
    if not pods._pod_has_runtime(runtime_with_ports):
        raise CheckFailed(3, "runtime+ports was not treated as a readiness signal")
    _check_pod_lease_readiness_cases(cfg)
    return "readiness verified via runtime, port mappings, and probe signals"


def check_04_cost_cap_before_readiness(cfg: AuditConfig) -> str:
    cc = cfg.cost_config()
    order = list(cc.get("check_order", []))
    if not order:
        raise CheckFailed(4, "cost check order not configured")
    _check_order_has_cost_before_readiness(
        check_id=4,
        order=[str(step).lower() for step in order],
        label="runtime config",
    )
    gated = any(
        facts_of(create).before("_gate_pod_cost_before_readiness", "wait_for_pod_runtime_sync")
        for create in (pods.create_pod_with_fallback_sync, pods._create_pod_with_fallback_sync)
    )
    if not gated:
        raise CheckFailed(
            4,
            "create_pod_with_fallback_sync does not gate cost before readiness wait",
        )
    _check_pod_lease_cost_order_cases(cfg)
    return "cost-cap check fires before readiness wait for pod-lease providers"


def check_05_execution_timeout(cfg: AuditConfig) -> str:
    tc = cfg.timeout_config()
    timeout = _int_config(5, "executionTimeout", tc.get("executionTimeout"))
    max_timeout = _int_config(5, "executionTimeoutMax", tc.get("executionTimeoutMax"))
    if timeout <= 0:
        raise CheckFailed(5, "executionTimeout must be > 0")
    if max_timeout <= 0:
        raise CheckFailed(5, "executionTimeoutMax must be > 0")
    if timeout > max_timeout:
        raise CheckFailed(
            5,
            f"executionTimeout ({timeout}) exceeds max ({max_timeout})",
        )
    return f"executionTimeout={timeout} within bounds"


def check_06_ttl_ge_timeout_plus_queue(cfg: AuditConfig) -> str:
    tc = cfg.timeout_config()
    ttl = _int_config(6, "ttl", tc.get("ttl"))
    exec_timeout = _int_config(6, "executionTimeout", tc.get("executionTimeout"))
    queue_time = _int_config(6, "expected_queue_time", tc.get("expected_queue_time"))
    if ttl < exec_timeout + queue_time:
        raise CheckFailed(
            6,
            f"ttl ({ttl}) < executionTimeout ({exec_timeout}) + expected_queue_time ({queue_time})",
        )
    return f"ttl ({ttl}) >= executionTimeout + queue_time"


def check_08_retention_windows(cfg: AuditConfig) -> str:
    rc = cfg.retention_config()
    sync_ret = _int_config(8, "sync_retention_s", rc.get("sync_retention_s"))
    async_ret = _int_config(8, "async_retention_s", rc.get("async_retention_s"))
    if sync_ret < SYNC_RESULT_RETENTION_S:
        raise CheckFailed(
            8,
            f"sync retention ({sync_ret}s) < minimum ({SYNC_RESULT_RETENTION_S}s)",
        )
    if async_ret < ASYNC_RESULT_RETENTION_S:
        raise CheckFailed(
            8,
            f"async retention ({async_ret}s) < minimum ({ASYNC_RESULT_RETENTION_S}s)",
        )
    if not _bool_config(rc.get("persist_before_expiry")):
        raise CheckFailed(8, "results are not configured to persist before RunPod expiry")
    sync_deadline = _int_config(
        8,
        "sync_persist_deadline_s",
        rc.get("sync_persist_deadline_s"),
    )
    async_deadline = _int_config(
        8,
        "async_persist_deadline_s",
        rc.get("async_persist_deadline_s"),
    )
    if sync_deadline >= SYNC_RESULT_RETENTION_S:
        raise CheckFailed(
            8,
            f"sync persist deadline ({sync_deadline}s) must be before {SYNC_RESULT_RETENTION_S}s",
        )
    if async_deadline >= ASYNC_RESULT_RETENTION_S:
        raise CheckFailed(
            8,
            f"async persist deadline ({async_deadline}s) must be before {ASYNC_RESULT_RETENTION_S}s",
        )
    return "results persist before retention expiry (sync<60s, async<1800s)"


def check_10_ssh_first_probe(cfg: AuditConfig) -> str:
    pc = cfg.probe_config()
    if not _bool_config(pc.get("ssh_first")):
        raise CheckFailed(
            10,
            "SSH-first probe pattern not enabled for pod-mode readiness",
        )
    probe_methods = _str_list(pc.get("probe_methods"))
    if probe_methods and pods.SSH_LOCALHOST_PROBE_METHOD not in probe_methods:
        raise CheckFailed(10, "ssh_localhost is absent from configured probe methods")
    primary_probe = pc.get("primary_probe")
    if primary_probe is not None and primary_probe != pods.SSH_LOCALHOST_PROBE_METHOD:
        raise CheckFailed(10, f"primary probe is {primary_probe!r}, expected ssh_localhost")
    if pods.POD_READINESS_PROBE_ORDER[0] != pods.SSH_LOCALHOST_PROBE_METHOD:
        raise CheckFailed(10, "pod readiness probe order is not SSH-first")
    return "SSH-first probe pattern available"
