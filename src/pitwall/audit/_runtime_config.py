"""Runtime AuditConfig backed by environment variables and defaults.

This module is imported only by the CLI entry-point so that the check
functions themselves remain decoupled from any specific config source.

Boolean inputs are never typed in as ``True``: each one is measured from Pitwall
code or behaviour (see ``pitwall.audit._probes``), so a regression flips the
input and the check fails.
"""

from __future__ import annotations

import os
from typing import Any

from pitwall.audit import _probes
from pitwall.runpod_client import pods
from pitwall.runpod_client.registry import (
    DOCKER_HUB_PREFIX,
    GHCR_PREFIX,
    GITLAB_REGISTRY_PREFIX,
)


def _int_setting(name: str, default: int) -> Any:
    """Return the setting as an int, or as the raw text when it is not an integer.

    A malformed value is handed on unparsed so the check that consumes it fails: its
    ``_int_config`` raises ``CheckFailed`` naming the setting, and the runner still emits the
    full report. Raising here would crash the whole audit run instead.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return raw


def _r2_temp_credentials_supported() -> bool:
    """Whether the code path that vends short-lived R2 pod credentials exists."""
    from pitwall.r2_temp_credentials import R2TemporaryCredentials  # noqa: PLC0415  # reason: lazy

    return callable(getattr(R2TemporaryCredentials, "as_pod_env", None))


class RuntimeAuditConfig:
    """Build an AuditConfig from the process environment / sensible defaults.

    The CLI reads values from the process environment and supplies documented
    synthetic defaults. It does not load database state; the capability audit
    endpoint is the deployment-backed path for checking a live configured
    capability.
    """

    def get(self, key: str, default: Any = None) -> Any:
        return os.environ.get(key, default)

    def gpu_ids(self) -> list[str]:
        raw = os.environ.get(
            "PITWALL_AUDIT_GPU_IDS",
            "NVIDIA H100 80GB HBM3,NVIDIA L4,NVIDIA A100-SXM4-80GB",
        )
        return [g.strip() for g in raw.split(",") if g.strip()]

    def workloads(self) -> list[dict[str, Any]]:
        return [
            {"name": "vllm", "disk_gb": 80},
            {"name": "embed", "disk_gb": 40},
            {"name": "slim", "disk_gb": 20},
        ]

    def launch_params(self) -> dict[str, Any]:
        return {
            "cloud_type": os.environ.get("PITWALL_AUDIT_CLOUD_TYPE", "SECURE"),
            "networkVolumeId": os.environ.get("RUNPOD_NETWORK_VOLUME_ID", ""),
        }

    def readiness_config(self) -> dict[str, Any]:
        return {"probe_field": "runtime" if _probes.readiness_reads_runtime_field() else ""}

    def cost_config(self) -> dict[str, Any]:
        if _probes.cost_gate_runs_before_readiness_wait():
            return {"check_order": ["cost", "readiness"]}
        return {"check_order": ["readiness", "cost"]}

    def timeout_config(self) -> dict[str, Any]:
        return {
            "executionTimeout": _int_setting("PITWALL_AUDIT_EXEC_TIMEOUT_S", 3600),
            "executionTimeoutMax": _int_setting("PITWALL_AUDIT_EXEC_TIMEOUT_MAX_S", 7200),
            "ttl": _int_setting("PITWALL_DEFAULT_LEASE_TTL_S", 7200),
            "expected_queue_time": _int_setting("PITWALL_AUDIT_QUEUE_TIME_S", 300),
        }

    def webhook_config(self) -> dict[str, Any]:
        return {
            "idempotent": _probes.webhook_dedupes_through_repository()
            and _probes.webhook_repository_skips_duplicates(),
            "fast_200": _probes.webhook_is_fast_200(),
        }

    def retention_config(self) -> dict[str, Any]:
        sync_retention_s, async_retention_s = 60, 1800
        sync_deadline_s, async_deadline_s = 30, 300
        return {
            "sync_retention_s": sync_retention_s,
            "async_retention_s": async_retention_s,
            "persist_before_expiry": (
                sync_deadline_s < sync_retention_s and async_deadline_s < async_retention_s
            ),
            "sync_persist_deadline_s": sync_deadline_s,
            "async_persist_deadline_s": async_deadline_s,
        }

    def volume_config(self) -> dict[str, Any]:
        vol_id = os.environ.get("RUNPOD_NETWORK_VOLUME_ID", "")
        dc_id = os.environ.get("RUNPOD_DATA_CENTER_ID", "")
        return {
            "networkVolumeId": vol_id,
            "dataCenterIds": [dc_id] if dc_id else [],
        }

    def probe_config(self) -> dict[str, Any]:
        order = _probes.pod_readiness_probe_order()
        return {
            "ssh_first": order[0] == pods.SSH_LOCALHOST_PROBE_METHOD,
            "probe_methods": list(order),
            "primary_probe": order[0],
        }

    def image_config(self) -> dict[str, Any]:
        return {
            "image_pull_timeout_s": _int_setting("PITWALL_IMAGE_PULL_TIMEOUT_S", 600),
            "startup_timeout_s": _int_setting("PITWALL_AUDIT_STARTUP_TIMEOUT_S", 600),
        }

    def disk_config(self) -> dict[str, Any]:
        return {
            "per_workload": {
                "vllm": 80,
                "embed": 40,
                "slim": 20,
            },
        }

    def template_config(self) -> dict[str, Any]:
        return _probes.template_cache_shape()

    def registry_config(self) -> dict[str, Any]:
        mapping: dict[str, str | None] = {}
        ghcr = (
            os.environ.get("RUNPOD_REGISTRY_AUTH_ID_GHCR")
            or os.environ.get("RUNPOD_REGISTRY_AUTH_ID")
            or "placeholder-ghcr"
        )
        gitlab = os.environ.get("RUNPOD_REGISTRY_AUTH_ID_GITLAB") or "placeholder-gitlab"
        docker_hub = os.environ.get("RUNPOD_REGISTRY_AUTH_ID_DOCKER_HUB") or None
        mapping[GHCR_PREFIX] = ghcr
        mapping[GITLAB_REGISTRY_PREFIX] = gitlab
        mapping[DOCKER_HUB_PREFIX] = docker_hub
        return {"prefix_to_auth_id": mapping}

    def pre_spend_payloads(self) -> list[dict[str, Any]]:
        return [
            {
                "texts": ["hello world"],
                "metadata": {"tenant": "audit-fixture"},
            },
            {
                "messages": [{"role": "user", "content": "summarize this safe payload"}],
                "max_tokens": 128,
            },
        ]

    def provider_fixtures(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "prov_custom_qwen3_vllm_fixture",
                "capability_id": "cap_llm_qwen3_32b",
                "name": "qwen3-32b-vllm-fixture",
                "provider_type": "serverless_lb",
                "runpod_endpoint_id": "qwen3-32b-fixture",
                "config": {
                    "gpu_type": "NVIDIA L4",
                    "lb_base_url": "https://qwen3-32b-fixture.api.runpod.ai",
                    "image_ref": "ghcr.io/acme/pitwall/operator-vllm:fixture",
                    "container_disk_gb": 80,
                    "workers": {
                        "workers_min": 0,
                        "workers_max": 1,
                    },
                    "env_vars": {
                        "VLLM_MODEL": "Qwen/Qwen3-32B",
                    },
                    "worker_image": {
                        "download_command": "hf download ${VLLM_MODEL}",
                    },
                },
            },
            {
                "id": "prov_embedding_pod_lease_fixture",
                "capability_id": "cap_embedding_bge_m3",
                "name": "bge-m3-pod-lease-fixture",
                "provider_type": "pod_lease",
                "region": "US-KS-2",
                "config": {
                    "image_ref": "ghcr.io/acme/pitwall/worker-embed:fixture",
                    "template_name": "pitwall-bge-m3-pod-lease",
                    "gpu_type_priority": ["NVIDIA L4"],
                    "container_disk_gb": 40,
                    "ports": {"http": [8000], "tcp": [22]},
                    "volume_id": "vol-model-cache",
                    "data_center_id": "US-KS-2",
                    "constraints": {
                        "max_attach_hang_s": 300,
                        "max_cost_per_hr": 1.0,
                    },
                    "readiness": {
                        "required_signals": ["runtime", "port_mappings", "probe_2xx"],
                    },
                    "cost_check_order": ["cost_cap", "readiness_wait"],
                    "r2": {
                        "required": _r2_temp_credentials_supported(),
                        "credential_strategy": "temp_credentials",
                    },
                },
            },
        ]

    def terminate_config(self) -> dict[str, Any]:
        return {"treat_404_as_success": _probes.terminate_treats_404_as_success()}

    def kill_switch_config(self) -> dict[str, Any]:
        return {
            "atomic": _probes.kill_switch_latches_before_teardown(),
            "steps": ["list_pods", "terminate_all", "verify"],
            "budget_s": 25,
        }
