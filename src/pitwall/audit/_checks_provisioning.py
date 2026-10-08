"""Audit checks 01, 02, 09, 11-14: GPU naming, volumes, images, disks, templates, registries."""

from __future__ import annotations

from pitwall.audit._common import (
    _MISSING,
    DEPRECATED_HF_CLI_COMMAND,
    REQUIRED_DISK_GB_BY_WORKLOAD,
    REQUIRED_REGISTRY_PREFIXES,
    AuditConfig,
    CheckFailed,
    _bool_config,
    _int_config,
    _str_list,
    _workload_gpu_ids,
)
from pitwall.audit._fixture_cases import (
    _check_pod_lease_attach_hang_cases,
    _check_r2_temp_credential_cases,
)
from pitwall.audit._fixtures import (
    _provider_container_disk_gb,
    _provider_hf_command_text,
    _provider_label,
    _provider_type,
    _provider_workers_min,
    _vllm_provider_fixtures,
)
from pitwall.audit._introspect import facts_of
from pitwall.runpod_client import pods, templates
from pitwall.runpod_client.gpu import CANONICAL_GPU_NAMES
from pitwall.runpod_client.registry import (
    DOCKER_HUB_PREFIX,
    GHCR_PREFIX,
    GITLAB_REGISTRY_PREFIX,
    registry_auth_id_from_env,
)


def check_01_gpu_ids_canonical(cfg: AuditConfig) -> str:
    gpus = [*cfg.gpu_ids(), *_workload_gpu_ids(cfg)]
    if not gpus:
        raise CheckFailed(1, "no GPU IDs configured for audit")
    non_canonical = [g for g in gpus if g not in CANONICAL_GPU_NAMES]
    if non_canonical:
        raise CheckFailed(
            1,
            f"non-canonical GPU IDs: {non_canonical!r}",
        )
    return "all GPU IDs are canonical"


def check_02_cloud_type_volume(cfg: AuditConfig) -> str:
    params = cfg.launch_params()
    cloud_type = str(params.get("cloud_type", "")).upper()
    has_volume = bool(params.get("networkVolumeId"))
    if cloud_type == "ALL" and has_volume:
        raise CheckFailed(
            2,
            "cloud_type=ALL combined with networkVolumeId — "
            "COMMUNITY attempt will always fail (volumes are Secure Cloud only)",
        )
    volume_cloud_types = pods._cloud_types_for_rest("ALL", network_volume_id="vol-audit")
    if volume_cloud_types != ["SECURE"]:
        raise CheckFailed(
            2,
            f"launcher expands ALL+networkVolumeId to {volume_cloud_types!r}; expected ['SECURE']",
        )
    return "cloud_type and volume usage are consistent"


def check_09_dc_pin(cfg: AuditConfig) -> str:
    vc = cfg.volume_config()
    dc_ids = [dc_id for dc_id in _str_list(vc.get("dataCenterIds", [])) if dc_id.strip()]
    has_volume = bool(vc.get("networkVolumeId"))
    if has_volume and len(dc_ids) != 1:
        raise CheckFailed(
            9,
            f"volume attached with {len(dc_ids)} dataCenterIds — must pin to exactly one DC",
        )
    _check_pod_lease_attach_hang_cases(cfg)
    return "network-volume DC pin and attach-hang timeout enforced"


def check_11_image_pull_timeout(cfg: AuditConfig) -> str:
    ic = cfg.image_config()
    pull_timeout = _int_config(11, "image_pull_timeout_s", ic.get("image_pull_timeout_s"))
    startup_timeout = _int_config(11, "startup_timeout_s", ic.get("startup_timeout_s"))
    if pull_timeout <= 0:
        raise CheckFailed(11, "image_pull_timeout_s must be > 0")
    if pull_timeout < startup_timeout:
        raise CheckFailed(
            11,
            f"image_pull_timeout ({pull_timeout}s) < startup_timeout ({startup_timeout}s)",
        )
    _check_r2_temp_credential_cases(cfg)
    return f"image-pull timeout ({pull_timeout}s) >= startup_timeout; StagingStore seam verified"


def check_12_disk_sized(cfg: AuditConfig) -> str:
    dc = cfg.disk_config()
    workloads = dc.get("per_workload", {})
    if not workloads:
        raise CheckFailed(12, "container disk not sized per workload")
    for wl_name, required_gb in REQUIRED_DISK_GB_BY_WORKLOAD.items():
        if wl_name not in workloads:
            raise CheckFailed(12, f"missing container disk size for {wl_name!r}")
        size_gb = _int_config(12, f"per_workload[{wl_name}]", workloads.get(wl_name))
        if size_gb < required_gb:
            raise CheckFailed(
                12,
                f"workload {wl_name!r} disk {size_gb}GB < required {required_gb}GB",
            )
    vllm_fixtures = _vllm_provider_fixtures(cfg)
    for provider in vllm_fixtures:
        provider_label = _provider_label(provider)
        disk_gb = _provider_container_disk_gb(provider)
        if disk_gb is _MISSING:
            raise CheckFailed(
                12,
                f"vLLM provider fixture {provider_label!r} missing container_disk_gb",
            )
        parsed_disk_gb = _int_config(
            12,
            f"provider[{provider_label}].container_disk_gb",
            disk_gb,
        )
        required_vllm_disk_gb = REQUIRED_DISK_GB_BY_WORKLOAD["vllm"]
        if parsed_disk_gb < required_vllm_disk_gb:
            raise CheckFailed(
                12,
                f"vLLM provider fixture {provider_label!r} disk {parsed_disk_gb}GB "
                f"< required {required_vllm_disk_gb}GB",
            )
        command_text = _provider_hf_command_text(provider)
        if DEPRECATED_HF_CLI_COMMAND in command_text:
            raise CheckFailed(
                12,
                f"vLLM provider fixture {provider_label!r} uses deprecated "
                f"{DEPRECATED_HF_CLI_COMMAND!r}",
            )
    return "container disk sized for vllm/embed/slim workloads; provider commands verified"


def check_13_template_cache(cfg: AuditConfig) -> str:
    tc = cfg.template_config()
    if not _bool_config(tc.get("cache_enabled")):
        raise CheckFailed(
            13,
            "template cache not enabled — templates recreated on every launch",
        )
    if not _bool_config(tc.get("create_on_cache_miss", True)):
        raise CheckFailed(13, "template creation on cache miss is not configured")
    if not _bool_config(tc.get("reuse_on_cache_hit", True)):
        raise CheckFailed(13, "cached templates are not reused")
    ensure = facts_of(templates.ensure_template)
    if not ensure.before("_lookup_cached", "create_template_rest"):
        raise CheckFailed(13, "ensure_template does not look up cache before create")
    if not ensure.references("_insert_cache"):
        raise CheckFailed(13, "ensure_template does not persist created templates to cache")
    return "template create + cache pattern enabled"


def check_14_registry_auth(cfg: AuditConfig) -> str:
    rc = cfg.registry_config()
    mapping = rc.get("prefix_to_auth_id", {})
    if not mapping:
        raise CheckFailed(
            14,
            "registry-auth-id not configured for any image-ref prefix",
        )
    missing = [prefix for prefix in REQUIRED_REGISTRY_PREFIXES if prefix not in mapping]
    if missing:
        raise CheckFailed(14, f"registry-auth-id mapping missing prefixes: {missing!r}")
    if not mapping.get(GHCR_PREFIX):
        raise CheckFailed(14, "GHCR image prefix has no registry-auth-id")
    if not mapping.get(GITLAB_REGISTRY_PREFIX):
        raise CheckFailed(14, "GitLab Registry image prefix has no registry-auth-id")

    fake_env = {
        "RUNPOD_REGISTRY_AUTH_ID_GHCR": "ghcr-auth",
        "RUNPOD_REGISTRY_AUTH_ID_GITLAB": "gitlab-auth",
        "RUNPOD_REGISTRY_AUTH_ID_DOCKER_HUB": "docker-auth",
        "RUNPOD_REGISTRY_AUTH_ID": "legacy-auth",
    }
    selections = {
        GHCR_PREFIX: registry_auth_id_from_env(
            "ghcr.io/example/pitwall-worker:abc",
            environ=fake_env,
        ),
        GITLAB_REGISTRY_PREFIX: registry_auth_id_from_env(
            "registry.gitlab.com/example/pitwall-worker:abc",
            environ=fake_env,
        ),
        DOCKER_HUB_PREFIX: registry_auth_id_from_env(
            "vllm/vllm-openai:v0.11.2",
            environ=fake_env,
        ),
    }
    expected = {
        GHCR_PREFIX: "ghcr-auth",
        GITLAB_REGISTRY_PREFIX: "gitlab-auth",
        DOCKER_HUB_PREFIX: "docker-auth",
    }
    if selections != expected:
        raise CheckFailed(14, f"registry auth selector returned {selections!r}")
    for provider in _vllm_provider_fixtures(cfg):
        provider_label = _provider_label(provider)
        provider_type = _provider_type(provider)
        if provider_type not in {"serverless_lb", "serverless_queue"}:
            continue
        workers_min = _provider_workers_min(provider)
        if workers_min is _MISSING:
            raise CheckFailed(
                14,
                f"vLLM provider fixture {provider_label!r} missing workers_min for L14",
            )
        parsed_workers_min = _int_config(
            14,
            f"provider[{provider_label}].workers_min",
            workers_min,
        )
        if parsed_workers_min > 0:
            raise CheckFailed(
                14,
                f"vLLM provider fixture {provider_label!r} has workers_min="
                f"{parsed_workers_min}; L14 requires hibernated fixtures",
            )
    return f"registry-auth-id mapped for {len(mapping)} prefix(es); L14 fixtures hibernated"
