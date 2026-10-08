"""Workbench profiles compiled into an isolated Pi agent directory (port of ``profile.ts``).

A profile is a plain mapping with the camelCase keys Pi and the packaged extensions read.
Compilation writes ``models.json`` and an ownership marker into the agent directory. Files are
created exclusively with mode 0600 and rechecked on every reopen.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pitwall.workbench.runtime_settings import (
    RuntimeSettingsError,
    derive_compaction_settings,
    enforce_runtime_settings,
    is_safe_integer,
)

API_KINDS = ("openai-completions", "openai-responses", "anthropic-messages")
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh", "max")
PROFILE_KEYS = frozenset(
    {
        "provider",
        "modelId",
        "endpoint",
        "api",
        "apiKeyEnv",
        "keyless",
        "servedContextTokens",
        "maxCompletionTokens",
        "contextReserveTokens",
        "requestTimeoutMs",
        "requestInactivityTimeoutMs",
        "generationTimeoutMs",
        "toolTimeoutMs",
        "taskTimeoutMs",
        "inputModalities",
        "thinkingLevelMap",
        "samplingParams",
        "compat",
        "cost",
        "reasoning",
        "reasoningLevel",
        "resourceGroup",
        "allowProviderFallback",
        "accountRef",
        "accountGroup",
        "accountMaxConcurrent",
        "accountInFlightTokenBudget",
        "accountUnknownUsage",
    }
)
RESERVED_CREDENTIAL_ENV_NAMES = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "COLORTERM",
        "LANG",
        "LC_ALL",
        "TZ",
        "TMPDIR",
        "NO_COLOR",
        "NODE_OPTIONS",
        "NODE_PATH",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "BASH_ENV",
        "ENV",
        "CDPATH",
        "PITWALL_WORKBENCH_NATIVE_PROFILE",
        "PITWALL_WORKBENCH_PROVIDER_PROFILE",
        "PITWALL_WORKBENCH_RESOURCE_DIR",
        "PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR",
        "PITWALL_WORKBENCH_APPROVED_CHECKS",
        "PITWALL_WORKBENCH_ACCOUNTING_PATH",
        "PITWALL_WORKBENCH_RESTRICTED_CREDENTIAL_ENV",
        "PI_CODING_AGENT_DIR",
        "PI_TELEMETRY",
        "PI_OFFLINE",
    }
)
MARKER_NAME = ".pi-workbench-owned.json"
_TIMEOUT_FIELDS = (
    "requestTimeoutMs",
    "requestInactivityTimeoutMs",
    "generationTimeoutMs",
    "toolTimeoutMs",
    "taskTimeoutMs",
)
_ACCOUNT_BUDGET_FIELDS = (
    "accountGroup",
    "accountMaxConcurrent",
    "accountInFlightTokenBudget",
    "accountUnknownUsage",
)


class ProfileError(ValueError):
    """A profile or its agent directory violates Workbench policy."""


@dataclass(frozen=True)
class CompiledProfile:
    name: str
    profile: dict[str, Any]
    agent_dir: Path
    models_path: Path
    provider: dict[str, Any]


@dataclass(frozen=True)
class ProviderProfilePaths:
    profile_path: Path
    env: dict[str, str]


def _stable(value: object) -> str:
    """Canonical JSON with sorted keys, used to hash a profile."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _dump(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def validate_profile_keys(profile: object) -> None:
    if not isinstance(profile, dict):
        raise ProfileError("profile must be an object")
    for key in profile:
        if key not in PROFILE_KEYS:
            raise ProfileError(f"unknown profile field: {key}")


def validate_profile_policy(profile: object) -> None:
    validate_profile_keys(profile)
    if isinstance(profile, dict):
        _assert_profile_policy(profile)


def _positive_int(value: object) -> int | None:
    """Return ``value`` when it is a positive safe integer, otherwise ``None``."""
    if isinstance(value, int | float) and is_safe_integer(value) and value > 0:
        return int(value)
    return None


def _check_endpoint(endpoint: object) -> None:
    try:
        parts = urlsplit(endpoint) if isinstance(endpoint, str) else None
        if parts is not None:
            _ = parts.port
    except ValueError:
        parts = None
    if (
        not isinstance(endpoint, str)
        or parts is None
        or parts.scheme not in ("http", "https")
        or not parts.hostname
    ):
        raise ProfileError("profile endpoint must be an http(s) URL")
    if (
        parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or "?" in endpoint
        or "#" in endpoint
    ):
        raise ProfileError(
            "endpoint must not contain credentials, query parameters, or fragments; "
            "credentials must use apiKeyEnv"
        )


def _assert_profile_policy(profile: Mapping[str, Any]) -> None:
    provider = profile.get("provider")
    if not isinstance(provider, str) or not provider:
        raise ProfileError("profile requires exact provider string")
    model_id = profile.get("modelId")
    if (
        not isinstance(model_id, str)
        or not model_id
        or model_id == "SET_FROM_SERVER_DISCOVERY"
        or re.search(r"[*?]", model_id)
    ):
        raise ProfileError("profile requires an exact modelId")
    if profile.get("api") not in API_KINDS:
        raise ProfileError("unsupported api")
    level = profile.get("reasoningLevel")
    if level and level not in THINKING_LEVELS:
        raise ProfileError("unsupported reasoningLevel")
    if profile.get("reasoning") is False and level and level != "off":
        raise ProfileError("reasoning capability is disabled for a non-off reasoning level")
    _check_endpoint(profile.get("endpoint"))
    resource_group = profile.get("resourceGroup")
    if not isinstance(resource_group, str) or not resource_group:
        raise ProfileError("resourceGroup is required")
    served = _positive_int(profile.get("servedContextTokens"))
    if served is None:
        raise ProfileError("servedContextTokens must be positive")
    completion = _positive_int(profile.get("maxCompletionTokens"))
    if completion is None or completion >= served:
        raise ProfileError("maxCompletionTokens must be below served context")
    reserve = profile.get("contextReserveTokens")
    if reserve is not None:
        reserve_tokens = _positive_int(reserve)
        if reserve_tokens is None or reserve_tokens >= served:
            raise ProfileError("contextReserveTokens must be positive and below served context")
    try:
        derive_compaction_settings(profile)
    except RuntimeSettingsError as error:
        raise ProfileError(str(error)) from error
    for name in _TIMEOUT_FIELDS:
        value = profile.get(name)
        if value is not None:
            timeout = _positive_int(value)
            if timeout is None or timeout > 3_600_000:
                raise ProfileError(f"{name} must be 1..3600000")
    if profile.get("allowProviderFallback") is not False:
        raise ProfileError("provider fallback must be false")
    api_key_env = profile.get("apiKeyEnv")
    account_ref = profile.get("accountRef")
    if account_ref is not None and (
        not isinstance(account_ref, str)
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", account_ref)
        or not api_key_env
    ):
        raise ProfileError("accountRef requires an exact account name and apiKeyEnv")
    budget = [profile.get(name) for name in _ACCOUNT_BUDGET_FIELDS]
    group, max_concurrent, in_flight, unknown = budget
    if any(value is not None for value in budget) and (
        not isinstance(group, str)
        or isinstance(max_concurrent, bool)
        or not isinstance(max_concurrent, int | float)
        or isinstance(in_flight, bool)
        or not isinstance(in_flight, int | float)
        or not unknown
    ):
        raise ProfileError(
            "hosted account budget policy must specify accountGroup, accountMaxConcurrent, "
            "inFlightTokenBudget, and unknownUsage together"
        )
    if group is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", group):
        raise ProfileError("accountGroup must be a valid shared identity")
    if max_concurrent is not None and (not is_safe_integer(max_concurrent) or max_concurrent < 1):
        raise ProfileError("accountMaxConcurrent must be positive")
    if in_flight is not None and (not is_safe_integer(in_flight) or in_flight < 1):
        raise ProfileError("accountInFlightTokenBudget must be positive")
    if unknown is not None and unknown not in ("hold", "release"):
        raise ProfileError("accountUnknownUsage must be hold or release")
    if api_key_env and (
        not isinstance(api_key_env, str)
        or not re.fullmatch(r"[A-Z][A-Z0-9_]*", api_key_env)
        or api_key_env in RESERVED_CREDENTIAL_ENV_NAMES
    ):
        raise ProfileError("apiKeyEnv must be a non-reserved environment variable name")
    keyless = profile.get("keyless")
    if api_key_env and keyless:
        raise ProfileError("keyless dummy policy cannot be combined with apiKeyEnv")
    if not api_key_env and keyless != "dummy":
        raise ProfileError("keyless policy must be explicit as dummy when apiKeyEnv is absent")
    modalities = profile.get("inputModalities")
    if modalities is not None and (
        not isinstance(modalities, list)
        or len(modalities) < 1
        or any(item not in ("text", "image") for item in modalities)
        or len(set(modalities)) != len(modalities)
        or "text" not in modalities
    ):
        raise ProfileError("inputModalities must be unique text or text,image")
    levels = profile.get("thinkingLevelMap")
    if levels is not None and (
        not isinstance(levels, dict)
        or any(
            level not in THINKING_LEVELS or not isinstance(value, str)
            for level, value in levels.items()
        )
    ):
        raise ProfileError("thinkingLevelMap contains an unsupported level")
    compat = profile.get("compat")
    if compat is not None and not isinstance(compat, dict):
        raise ProfileError("compat metadata must be an object")


def _provider_config(profile: Mapping[str, Any]) -> dict[str, Any]:
    provider: dict[str, Any] = {"baseUrl": profile["endpoint"], "api": profile["api"]}
    if profile.get("apiKeyEnv"):
        provider["apiKey"] = f"${profile['apiKeyEnv']}"
    elif profile.get("keyless") == "dummy":
        provider["apiKey"] = "dummy"  # pragma: allowlist secret
    reasoning = profile.get("reasoning")
    model: dict[str, Any] = {
        "id": profile["modelId"],
        "name": profile["modelId"],
        "reasoning": reasoning
        if reasoning is not None
        else profile.get("reasoningLevel") is not None,
        "input": list(profile.get("inputModalities") or ["text"]),
    }
    if profile.get("cost"):
        model["cost"] = profile["cost"]
    model["contextWindow"] = profile["servedContextTokens"]
    model["maxTokens"] = profile["maxCompletionTokens"]
    for key in ("thinkingLevelMap", "samplingParams", "compat"):
        if profile.get(key):
            model[key] = profile[key]
    provider["models"] = [model]
    return provider


def _regular_file(path: Path, message: str) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ProfileError(message)


def _write_exclusive(path: Path, content: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)


def _recheck_owned(marker_path: Path, models_path: Path, profile_hash: str, models: str) -> None:
    """Recheck an existing owned directory; raises FileNotFoundError when it is not owned yet."""
    _regular_file(marker_path, "owned agentDir marker must be a regular file")
    _regular_file(models_path, "owned agentDir models file must be a regular file")
    for path in (marker_path, models_path):
        if path.lstat().st_mode & 0o077:
            raise ProfileError("owned agentDir file permissions are too broad")
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ProfileError("owned agentDir has a profile or models conflict") from error
    current = models_path.read_text(encoding="utf-8")
    if (
        not isinstance(marker, dict)
        or marker.get("profileHash") != profile_hash
        or marker.get("modelsHash") != _sha256(current)
        or current != models
    ):
        raise ProfileError("owned agentDir has a profile or models conflict")


def compile_profile(
    name: str, profile: Mapping[str, Any], agent_dir: Path | str
) -> CompiledProfile:
    """Validate ``profile`` and materialise it in ``agent_dir`` (created 0700 when absent)."""
    validate_profile_policy(profile)
    if not name or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        raise ProfileError("profile name must be lowercase kebab case")
    isolated = Path(os.path.abspath(agent_dir))
    try:
        mode = isolated.lstat().st_mode
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ProfileError("agentDir must be a real directory")
    except FileNotFoundError:
        pass
    isolated.mkdir(mode=0o700, parents=True, exist_ok=True)
    if isolated.lstat().st_mode & 0o077:
        raise ProfileError("agentDir permissions are too broad")
    provider = _provider_config(profile)
    models_path = isolated / "models.json"
    models = _dump({"providers": {profile["provider"]: provider}})
    profile_hash = _sha256(_stable({"name": name, "profile": profile}))
    marker_path = isolated / MARKER_NAME
    try:
        _recheck_owned(marker_path, models_path, profile_hash, models)
    except FileNotFoundError:
        if any(isolated.iterdir()):
            raise ProfileError("agentDir contains unowned files") from None
        _write_exclusive(models_path, models)
        marker = {"schemaVersion": 1, "profileHash": profile_hash, "modelsHash": _sha256(models)}
        _write_exclusive(marker_path, json.dumps(marker) + "\n")
    return CompiledProfile(
        name=name,
        profile=dict(profile),
        agent_dir=isolated,
        models_path=models_path,
        provider=provider,
    )


def profile_from_config(config: object, name: str) -> dict[str, Any]:
    if not isinstance(config, dict) or "profiles" not in config or config.get("schemaVersion") != 1:
        raise ProfileError("invalid workbench config schemaVersion")
    profiles = config["profiles"]
    profile = profiles.get(name) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise ProfileError(f"profile not found: {name}")
    return profile


def configure_provider_profile(
    compiled: CompiledProfile, cwd: Path | str | None = None
) -> ProviderProfilePaths:
    """Write the ``provider-profile.json`` the packaged provider extension loads."""
    enforce_runtime_settings(compiled.agent_dir, cwd, compiled.profile)
    profile_path = compiled.models_path.parent / "provider-profile.json"
    content = json.dumps(
        {"profile": compiled.profile, "providerConfig": compiled.provider}, ensure_ascii=False
    )
    content += "\n"
    try:
        info = profile_path.lstat()
    except FileNotFoundError:
        _write_exclusive(profile_path, content)
    else:
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ProfileError("provider profile path is not a private regular file")
        if profile_path.read_text(encoding="utf-8") != content:
            raise ProfileError("provider profile already contains different content")
    return ProviderProfilePaths(
        profile_path=profile_path, env={"PITWALL_WORKBENCH_PROVIDER_PROFILE": str(profile_path)}
    )
