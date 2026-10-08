"""Accessors over provider fixture records (mappings or models) used by the audit."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pitwall.audit._common import (
    _MISSING,
    POD_LEASE_PROVIDER_TYPE,
    VLLM_PROVIDER_TYPES,
    AuditConfig,
    _bool_config,
    _field,
    _float_config,
    _object_list,
    _python_value,
    _str_list,
)
from pitwall.runpod_client import pods


def _provider_fixtures(cfg: AuditConfig) -> list[Any]:
    provider_fixtures = getattr(cfg, "provider_fixtures", None)
    if callable(provider_fixtures):
        return _object_list(provider_fixtures())
    return _object_list(cfg.get("provider_fixtures", []))


def _provider_config(provider: Any) -> Mapping[str, Any]:
    config = _python_value(_field(provider, "config", {}))
    if isinstance(config, Mapping):
        return config
    return {}


def _provider_type(provider: Any) -> str:
    raw = _field(provider, "provider_type", "")
    value = getattr(raw, "value", raw)
    return str(value)


def _provider_label(provider: Any) -> str:
    for field_name in ("id", "provider_id", "name", "endpoint_id", "runpod_endpoint_id"):
        value = _field(provider, field_name, None)
        if value:
            return str(value)
    return "<unknown provider>"


def _nested_value(value: Any, path: tuple[str, ...]) -> Any:
    current = _python_value(value)
    for key in path:
        current = _python_value(current)
        current = _field(current, key, _MISSING)
        if current is _MISSING:
            return _MISSING
    return current


def _provider_text_value(provider: Any, config: Mapping[str, Any], path: tuple[str, ...]) -> str:
    value = _nested_value(provider if path[0] in {"id", "name", "capability_id"} else config, path)
    if value is _MISSING or value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list | tuple | set):
        return " ".join(str(item) for item in value)
    return str(value)


def _is_vllm_provider_fixture(provider: Any) -> bool:
    provider_type = _provider_type(provider)
    if provider_type not in VLLM_PROVIDER_TYPES:
        return False

    config = _provider_config(provider)
    markers = [
        _provider_text_value(provider, config, ("id",)),
        _provider_text_value(provider, config, ("name",)),
        _provider_text_value(provider, config, ("capability_id",)),
        _provider_text_value(provider, config, ("image_ref",)),
        _provider_text_value(provider, config, ("image_name",)),
        _provider_text_value(provider, config, ("template_name",)),
        _provider_text_value(provider, config, ("worker_image", "image_ref")),
        _provider_text_value(provider, config, ("env_vars", "VLLM_MODEL")),
        _provider_text_value(provider, config, ("env_vars", "MODEL_NAME")),
        _provider_text_value(provider, config, ("env_vars", "OPENAI_SERVED_MODEL_NAME_OVERRIDE")),
        _provider_text_value(provider, config, ("env", "VLLM_MODEL")),
        _provider_text_value(provider, config, ("env", "MODEL_NAME")),
        _provider_text_value(provider, config, ("env", "OPENAI_SERVED_MODEL_NAME_OVERRIDE")),
        _provider_text_value(provider, config, ("model",)),
        _provider_text_value(provider, config, ("model_name",)),
    ]
    lower_markers = [marker.lower() for marker in markers if marker]
    return any(
        "vllm" in marker or "llm" in marker or "qwen" in marker or "openai-compatible" in marker
        for marker in lower_markers
    )


def _vllm_provider_fixtures(cfg: AuditConfig) -> list[Any]:
    return [provider for provider in _provider_fixtures(cfg) if _is_vllm_provider_fixture(provider)]


def _pod_lease_provider_fixtures(cfg: AuditConfig) -> list[Any]:
    return [
        provider
        for provider in _provider_fixtures(cfg)
        if _provider_type(provider) == POD_LEASE_PROVIDER_TYPE
    ]


def _provider_config_value(provider: Any, paths: tuple[tuple[str, ...], ...]) -> Any:
    config = _provider_config(provider)
    for path in paths:
        value = _nested_value(config, path)
        if value is not _MISSING:
            return value
    return _MISSING


def _normalized_readiness_signal(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    normalized = normalized.replace(" ", "_")
    if normalized in {"runtime", "runtime_present", "runtime_non_null", "runtime_seen"}:
        return "runtime"
    if normalized in {
        "ports",
        "port",
        "port_mappings",
        "portmappings",
        "port_mappings_present",
        "ports_present",
    }:
        return "port_mappings"
    if normalized in {
        "probe",
        "probe_2xx",
        "probe_passed",
        "proxy_or_ssh_probe",
        "ssh_or_proxy_probe",
        "readiness_probe",
    }:
        return "probe_2xx"
    return normalized


def _provider_readiness_signals(provider: Any) -> set[str] | None:
    raw = _provider_config_value(
        provider,
        (
            ("readiness", "required_signals"),
            ("readiness", "signals"),
            ("readiness_signals",),
            ("required_readiness_signals",),
            ("audit", "readiness_signals"),
            ("audit", "required_readiness_signals"),
        ),
    )
    if raw is _MISSING:
        return None
    return {_normalized_readiness_signal(signal) for signal in _str_list(raw)}


def _provider_order(provider: Any, paths: tuple[tuple[str, ...], ...]) -> list[str] | None:
    raw = _provider_config_value(provider, paths)
    if raw is _MISSING:
        return None
    return [step.strip().lower() for step in _str_list(raw) if step.strip()]


def _provider_volume_id(provider: Any) -> str:
    raw = _provider_config_value(
        provider,
        (
            ("network_volume_id",),
            ("networkVolumeId",),
            ("volume_id",),
            ("volume", "id"),
            ("network_volume", "id"),
        ),
    )
    if raw is _MISSING or raw is None:
        return ""
    return str(raw).strip()


def _provider_data_center_ids(provider: Any) -> list[str]:
    raw = _provider_config_value(
        provider,
        (
            ("dataCenterIds",),
            ("data_center_ids",),
            ("data_centers",),
            ("data_center_id",),
            ("datacenter_id",),
        ),
    )
    dc_ids = [dc_id for dc_id in _str_list(raw) if dc_id.strip()] if raw is not _MISSING else []
    if dc_ids:
        return dc_ids
    region = _field(provider, "region", None)
    return [str(region).strip()] if isinstance(region, str) and region.strip() else []


def _provider_attach_timeout_s(check_id: int, provider: Any) -> float:
    raw = _provider_config_value(
        provider,
        (
            ("constraints", "max_attach_hang_s"),
            ("constraints", "volume_attach_timeout_s"),
            ("constraints", "attach_timeout_s"),
            ("max_attach_hang_s",),
            ("volume_attach_timeout_s",),
            ("attach_timeout_s",),
        ),
    )
    if raw is _MISSING:
        return pods.DEFAULT_VOLUME_ATTACH_TIMEOUT_S
    return _float_config(
        check_id,
        f"provider[{_provider_label(provider)}].max_attach_hang_s",
        raw,
    )


def _provider_env_mappings(provider: Any) -> list[Mapping[str, Any]]:
    mappings: list[Mapping[str, Any]] = []
    for path in (
        ("env_vars",),
        ("env",),
        ("environment",),
        ("worker_env",),
        ("pod_env",),
    ):
        raw = _provider_config_value(provider, (path,))
        if isinstance(raw, Mapping):
            mappings.append(raw)
    return mappings


def _provider_requires_r2(provider: Any) -> bool:
    raw = _provider_config_value(
        provider,
        (
            ("requires_r2",),
            ("r2_required",),
            ("r2", "required"),
            ("r2", "enabled"),
            ("storage", "requires_r2"),
        ),
    )
    if raw is _MISSING:
        return False
    return _bool_config(raw)


def _provider_r2_strategy(provider: Any) -> str:
    raw = _provider_config_value(
        provider,
        (
            ("r2", "credential_strategy"),
            ("r2", "strategy"),
            ("r2", "mode"),
            ("r2_credential_strategy",),
            ("storage", "r2_credential_strategy"),
        ),
    )
    if raw is _MISSING or raw is None:
        return ""
    return str(raw).strip().lower().replace("_", "-")


def _provider_container_disk_gb(provider: Any) -> Any:
    config = _provider_config(provider)
    for path in (
        ("container_disk_gb",),
        ("containerDiskInGb",),
        ("container_disk_in_gb",),
        ("template", "container_disk_gb"),
        ("worker_image", "container_disk_gb"),
    ):
        value = _nested_value(config, path)
        if value is not _MISSING:
            return value
    return _MISSING


def _provider_workers_min(provider: Any) -> Any:
    config = _provider_config(provider)
    for path in (
        ("workers_min",),
        ("workersMin",),
        ("workers", "workers_min"),
        ("workers", "workersMin"),
        ("scaling", "workers_min"),
        ("scaling", "workersMin"),
    ):
        value = _nested_value(config, path)
        if value is not _MISSING:
            return value
    for attr in ("workers_min", "workersMin"):
        value = _field(provider, attr, _MISSING)
        if value is not _MISSING:
            return value
    return _MISSING


def _provider_hf_command_text(provider: Any) -> str:
    config = _provider_config(provider)
    texts: list[str] = []
    for path in (
        ("download_command",),
        ("hf_download_command",),
        ("preload_command",),
        ("entrypoint",),
        ("docker_entrypoint",),
        ("command",),
        ("worker_image", "download_command"),
        ("worker_image", "entrypoint"),
        ("worker_image", "docker_entrypoint"),
    ):
        text = _provider_text_value(provider, config, path)
        if text:
            texts.append(text)
    return "\n".join(texts)
