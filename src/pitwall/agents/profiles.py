"""Agent profiles: `[agents.profiles]` persistence in pitwall.toml, validation, and resolution."""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from pitwall.providers.model_studio import catalog as model_studio

from . import profiles_toml
from .paths import xdg_dir
from .run_store import atomic_write_bytes


class ProfilesError(ValueError):
    """The agent profiles are invalid or the requested edit is impossible."""


USAGE = "route-shim: usage: route-shim.sh <name[@harness]> <prompt-source> [--routing-*] [harness flags]"


ROUTE_NAME = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,63}")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
SECRET_KEY = re.compile(r"apikey|api_key|token(?!s)|secret|password", re.IGNORECASE)
URL_CREDENTIAL_QUERY = re.compile(r"(key|token|secret|password|passwd|pwd)$", re.IGNORECASE)
ACCOUNT_LABEL = re.compile(r"[A-Za-z0-9-]{1,16}")
SEATS = ("default-author", "critical", "review", "burst", "throughput", "local", "gateway")
WORKSPACES = ("shared", "isolated", "auto")
TASK_MODES = ("read", "write")
_ENTRY_KEYS = {
    "model",
    "harness",
    "endpoint",
    "args",
    "env",
    "limits",
    "seat",
    "workspace",
    "taskMode",
    "expiresAt",
    "origin",
    "autoServe",
    "effort",
    "account",
}
_HARNESS_KEYS = {"effort", "args"}
EXPIRES_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
ORIGIN_KINDS = ("pitwall",)
ORIGIN_STATES = ("active", "stopped", "unknown")
AUTO_SERVE_KEYS = {"maxUsdPerHour", "ttlMinutes", "idleTimeoutMinutes", "readyTimeoutMinutes"}
EXPIRY_WARNING_SECONDS = 15 * 60
RESOLVE_SKEW_SECONDS = 60


def parse_expires_at(value: str) -> datetime:
    if EXPIRES_AT.fullmatch(value) is None:
        raise ProfilesError(
            f"expiresAt must be ISO 8601 UTC like 2026-08-27T10:00:00Z, got {value!r}"
        )
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def parse_auto_serve(spec: str) -> dict[str, int | float]:
    names = {
        "max-usd-per-hour": ("maxUsdPerHour", float),
        "ttl": ("ttlMinutes", int),
        "idle": ("idleTimeoutMinutes", int),
        "ready-timeout": ("readyTimeoutMinutes", int),
    }
    result: dict[str, int | float] = {}
    for item in spec.split(","):
        key, separator, raw_value = item.partition("=")
        if not separator or key not in names:
            raise ProfilesError(f"--auto-serve has unknown key {key!r}")
        field, converter = names[key]
        try:
            result[field] = converter(raw_value)
        except ValueError as exc:
            raise ProfilesError(f"--auto-serve {key} must be a number, got {raw_value!r}") from exc
    return result


def parse_limits(spec: str) -> dict[str, int]:
    names = {"context", "output"}
    result: dict[str, int] = {}
    for item in spec.split(","):
        key, separator, raw_value = item.partition("=")
        if not separator or key not in names:
            raise ProfilesError(f"--limits has unknown key {key!r}")
        try:
            result[key] = int(raw_value)
        except ValueError as exc:
            raise ProfilesError(f"--limits {key} must be an integer, got {raw_value!r}") from exc
    return result


def expiry_state(entry: Mapping[str, Any], *, now: datetime) -> tuple[str, int | None]:
    value = entry.get("expiresAt")
    if not value:
        return "none", None
    remaining = int((parse_expires_at(str(value)) - now).total_seconds())
    if remaining < 0:
        return "expired", remaining
    if remaining <= EXPIRY_WARNING_SECONDS:
        return "expiring", remaining
    return "ok", remaining


def _expiry_remedy(name: str, entry: Mapping[str, Any]) -> str:
    origin = entry.get("origin") or {}
    if origin.get("kind") == "pitwall":
        return f"renew the lease in Pitwall, then `pitwall agents profiles refresh {name}`"
    return "update or remove the route's expiresAt"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProfilesError(message)


def profiles_path(env: Mapping[str, str]) -> Path:
    """The TOML file holding ``[agents.profiles]``.

    ``PITWALL_AGENTS_PROFILES`` names an alternative file for tests and isolated runs. Otherwise
    it is the pitwall.toml the settings loader uses (``PITWALL_CONFIG_FILE`` or ``./pitwall.toml``;
    config.py validates the same tables for the broker side, and this module stays stdlib-only),
    and ``$XDG_CONFIG_HOME/pitwall/pitwall.toml`` when neither is set.
    """
    configured = env.get("PITWALL_AGENTS_PROFILES")
    if configured:
        return Path(configured).expanduser()
    explicit = env.get("PITWALL_CONFIG_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    local = Path.cwd() / "pitwall.toml"
    if local.is_file():
        return local
    base = xdg_dir(env, "XDG_CONFIG_HOME", ".config")
    return base / "pitwall" / "pitwall.toml"


def empty_profiles() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
        "endpoints": {},
        "harnesses": {},
        "models": {},
    }


def _scan_secrets(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if (
                isinstance(key, str)
                and key not in {"apiKeyEnv", "tokenPlanAutomation"}
                and SECRET_KEY.search(key)
            ):
                raise ProfilesError(
                    f"{path}.{key} looks like an inline secret; agent profiles only store apiKeyEnv names"
                )
            _scan_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_secrets(child, f"{path}[{index}]")


def _agnostic_endpoint_harness(harnesses: Mapping[str, Any], harness: str) -> bool:
    harness_def = harnesses.get(harness)
    if not isinstance(harness_def, Mapping):
        return False
    return (
        harness_def.get("harnessKind") == "model-agnostic"
        and harness_def.get("endpointDelivery") != "none"
    )


class EndpointReference(str):
    """A serializable shared-endpoint name that acts like its resolved object in memory."""

    _resolved: Mapping[str, Any]

    def __new__(cls, name: str, resolved: Mapping[str, Any]) -> EndpointReference:
        instance = str.__new__(cls, name)
        instance._resolved = dict(resolved)
        return instance

    def __getitem__(self, key: Any) -> Any:
        if isinstance(key, str):
            return self._resolved[key]
        return str.__getitem__(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return self._resolved.get(key, default)

    @property
    def resolved(self) -> Mapping[str, Any]:
        return self._resolved


def resolved_endpoint(entry: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """Return an entry's inline or shared endpoint as an endpoint object."""
    endpoint = entry.get("endpoint")
    if isinstance(endpoint, EndpointReference):
        return endpoint.resolved
    if isinstance(endpoint, Mapping):
        return endpoint
    return None


def _reject_url_credentials(base_url: str, field: str) -> None:
    """Refuse userinfo and credential-named query parameters; the message never echoes the URL."""
    try:
        parts = urlsplit(base_url)
        query_names = [name for name, _value in parse_qsl(parts.query, keep_blank_values=True)]
        has_userinfo = parts.username is not None or parts.password is not None
    except ValueError as exc:
        raise ProfilesError(f"{field}.baseUrl is not a valid URL") from exc
    _require(
        not has_userinfo,
        f"{field}.baseUrl must not embed credentials (user:password@); name an environment variable in apiKeyEnv",
    )
    _require(
        not any(URL_CREDENTIAL_QUERY.search(name) for name in query_names),
        f"{field}.baseUrl must not carry a credential query parameter; name an environment variable in apiKeyEnv",
    )


def _validate_endpoint(endpoint: Any, field: str) -> dict[str, Any]:
    if isinstance(endpoint, dict) and endpoint.get("kind") == model_studio.KIND:
        try:
            return model_studio.validate_endpoint(endpoint, field)
        except model_studio.ModelStudioConfigError as exc:
            raise ProfilesError(str(exc)) from exc
    _require(
        isinstance(endpoint, dict)
        and set(endpoint) <= {"baseUrl", "apiKeyEnv"}
        and "baseUrl" in endpoint,
        f"{field} must contain baseUrl and optionally apiKeyEnv, or kind: model-studio",
    )
    base_url = endpoint["baseUrl"]
    _require(
        isinstance(base_url, str) and re.fullmatch(r"https?://\S+", base_url) is not None,
        f"{field}.baseUrl must be an http(s) URL",
    )
    _reject_url_credentials(base_url, field)
    normalized: dict[str, Any] = {"baseUrl": base_url}
    if "apiKeyEnv" in endpoint:
        key_env = endpoint["apiKeyEnv"]
        _require(
            isinstance(key_env, str) and ENV_NAME.fullmatch(key_env) is not None,
            f"{field}.apiKeyEnv must name an environment variable",
        )
        normalized["apiKeyEnv"] = key_env
    return normalized


def _validate_model_studio_route(
    entry: Mapping[str, Any], endpoint: Mapping[str, Any], field: str, *, harness: str
) -> None:
    model = str(entry["model"])
    try:
        model_studio.check_model(str(endpoint["plan"]), model)
    except model_studio.ModelStudioConfigError as exc:
        raise ProfilesError(f"{field}: {exc}") from exc
    if endpoint.get("protocol") == "anthropic":
        _require(
            harness == "opencode",
            f"{field}: protocol anthropic is supported only by the opencode harness",
        )
        _require(
            "effort" not in entry,
            f"{field}.effort is not supported on the anthropic protocol; use protocol openai",
        )
    if "effort" in entry:
        values = model_studio.effort_values(model)
        _require(bool(values), f"{field}.effort: model {model!r} has no effort control")
        _require(
            entry["effort"] in values,
            f"{field}.effort must be one of {', '.join(values)} for {model}",
        )


def _known_registry_model(registry: Mapping[str, Any], name: str) -> tuple[str, str] | None:
    lowered = name.lower()
    for harness_id, harness in registry["harnesses"].items():
        for model_id, model in harness["models"].items():
            if lowered == model_id.lower() or lowered in (
                alias.lower() for alias in model["aliases"]
            ):
                return harness_id, model_id
    return None


def find_vendor_harness(registry: Mapping[str, Any], model: str) -> str | None:
    """The harness whose models/aliases or routeFamilies own ``model``; None for open-weight ids."""
    known = _known_registry_model(registry, model)
    if known is not None:
        return known[0]
    lowered = model.lower()
    for harness_id, harness in registry["harnesses"].items():
        for family in harness["routeFamilies"]:
            if any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in family["patterns"]):
                return str(harness_id)
    return None


def effort_spec(
    registry: Mapping[str, Any], harness: str
) -> tuple[str, str | None, tuple[str, ...]]:
    """(kind, key, allowed values) for a harness's per-invocation effort control."""
    effort = registry["harnesses"][harness]["effort"]
    return (
        str(effort["kind"]),
        effort.get("key"),
        tuple(str(value) for value in effort.get("values") or ()),
    )


def validate_effort(
    registry: Mapping[str, Any], harness: str, model: str | None, value: Any, field: str
) -> str:
    _require(isinstance(value, str) and bool(value), f"{field} must be a non-empty string")
    kind, _key, values = effort_spec(registry, harness)
    _require(kind != "none", f"{field}: harness {harness!r} exposes no effort control")
    if values:
        _require(value in values, f"{field} must be one of {', '.join(values)} for {harness}")
    known = _known_registry_model(registry, model) if model else None
    if known is not None:
        model_values = registry["harnesses"][known[0]]["models"][known[1]].get("effortValues") or []
        if model_values:
            _require(
                value in model_values,
                f"{field} must be one of {', '.join(model_values)} for {known[1]}",
            )
    return str(value)


def render_effort(registry: Mapping[str, Any], harness: str, value: str) -> list[str]:
    kind, key, _values = effort_spec(registry, harness)
    if kind == "harness-flag" and key:
        return [key, value]
    if kind == "config" and key:
        return ["-c", f"{key}={value}"]
    return []


def caller_sets_effort(registry: Mapping[str, Any], harness: str, args: Sequence[str]) -> bool:
    kind, key, _values = effort_spec(registry, harness)
    if not key:
        return False
    if kind == "harness-flag":
        return any(argument == key or argument.startswith(f"{key}=") for argument in args)
    if kind == "config":
        return any(argument.startswith(f"{key}=") for argument in args)
    return False


def entry_harness(
    entry: Mapping[str, Any], defaults: Mapping[str, Any], registry: Mapping[str, Any]
) -> str:
    """The harness an entry resolves to without an @override (mirrors resolve_route)."""
    if "harness" in entry:
        return str(entry["harness"])
    if entry.get("endpoint"):
        return str(defaults["endpointHarness"])
    return find_vendor_harness(registry, str(entry["model"])) or str(defaults["harness"])


def validate_profiles(data: Any, *, registry: Mapping[str, Any]) -> dict[str, Any]:
    _require(isinstance(data, dict), "agents.profiles must be a table")
    _require(data.get("schemaVersion", 1) == 1, "agents.profiles schemaVersion must be 1")
    known_sections = {"schemaVersion", "defaults", "endpoints", "harnesses", "models", "pitwall"}
    _require(
        set(data) <= known_sections,
        f"agents.profiles has unknown fields: {', '.join(sorted(set(data) - known_sections))}",
    )
    _scan_secrets(data, "agents.profiles")
    harnesses = registry["harnesses"]
    defaults = dict(empty_profiles()["defaults"])
    raw_defaults = data.get("defaults", {})
    _require(
        isinstance(raw_defaults, dict) and set(raw_defaults) <= {"harness", "endpointHarness"},
        "agents.profiles.defaults has unknown fields",
    )
    defaults.update(raw_defaults)
    _require(
        defaults["harness"] in harnesses,
        f"defaults.harness names unknown harness {defaults['harness']!r}",
    )
    _require(
        _agnostic_endpoint_harness(harnesses, defaults["endpointHarness"]),
        f"defaults.endpointHarness {defaults['endpointHarness']!r} must be a model-agnostic harness that accepts endpoints",
    )
    raw_endpoints = data.get("endpoints", {})
    _require(isinstance(raw_endpoints, dict), "agents.profiles.endpoints must be a table")
    normalized_endpoints: dict[str, dict[str, Any]] = {}
    for endpoint_name in sorted(raw_endpoints):
        _require(
            isinstance(endpoint_name, str)
            and ROUTE_NAME.fullmatch(endpoint_name) is not None
            and "@" not in endpoint_name,
            f"invalid endpoint name {endpoint_name!r}: use [A-Za-z][A-Za-z0-9._-]{{0,63}} without '@'",
        )
        normalized_endpoints[endpoint_name] = _validate_endpoint(
            raw_endpoints[endpoint_name], f"endpoints.{endpoint_name}"
        )
    raw_harnesses = data.get("harnesses", {})
    _require(isinstance(raw_harnesses, dict), "agents.profiles.harnesses must be a table")
    normalized_harnesses: dict[str, dict[str, Any]] = {}
    for harness_id in sorted(raw_harnesses):
        block = raw_harnesses[harness_id]
        field = f"harnesses.{harness_id}"
        _require(harness_id in harnesses, f"{field} names unknown harness {harness_id!r}")
        _require(isinstance(block, dict), f"{field} must be an object")
        _require(
            set(block) <= _HARNESS_KEYS,
            f"{field} has unknown fields: {', '.join(sorted(set(block) - _HARNESS_KEYS))}",
        )
        args = block.get("args", [])
        _require(
            isinstance(args, list) and all(isinstance(item, str) and item for item in args),
            f"{field}.args must be a list of non-empty strings",
        )
        normalized_block: dict[str, Any] = {"args": list(args)}
        if "effort" in block:
            normalized_block["effort"] = validate_effort(
                registry, harness_id, None, block["effort"], f"{field}.effort"
            )
        normalized_harnesses[harness_id] = normalized_block
    normalized_pitwall: dict[str, Any] | None = None
    if "pitwall" in data:
        raw_pitwall = data["pitwall"]
        _require(
            isinstance(raw_pitwall, dict) and set(raw_pitwall) <= {"autoRegister"},
            "pitwall must contain only autoRegister",
        )
        auto_register = raw_pitwall.get("autoRegister", {})
        _require(isinstance(auto_register, dict), "pitwall.autoRegister must be an object")
        normalized_auto_register: dict[str, str] = {}
        for capability, route_name in auto_register.items():
            _require(
                isinstance(capability, str) and bool(capability),
                "pitwall.autoRegister capability names must be non-empty strings",
            )
            _require(
                isinstance(route_name, str)
                and ROUTE_NAME.fullmatch(route_name) is not None
                and "@" not in route_name,
                f"pitwall.autoRegister route name {route_name!r} is invalid",
            )
            normalized_auto_register[capability] = route_name
        normalized_pitwall = {"autoRegister": dict(sorted(normalized_auto_register.items()))}
    models = data.get("models", {})
    _require(isinstance(models, dict), "agents.profiles.models must be a table")
    normalized_models: dict[str, dict[str, Any]] = {}
    for name in sorted(models):
        entry = models[name]
        _require(
            isinstance(name, str) and ROUTE_NAME.fullmatch(name) is not None and "@" not in name,
            f"invalid route name {name!r}: use [A-Za-z][A-Za-z0-9._-]{{0,63}} without '@'",
        )
        field = f"models.{name}"
        _require(isinstance(entry, dict), f"{field} must be an object")
        _require(
            set(entry) <= _ENTRY_KEYS,
            f"{field} has unknown fields: {', '.join(sorted(set(entry) - _ENTRY_KEYS))}",
        )
        model = entry.get("model")
        _require(
            isinstance(model, str) and bool(model), f"{field}.model must be a non-empty string"
        )
        normalized: dict[str, Any] = {"model": model, "args": [], "env": {}}
        if "harness" in entry:
            _require(
                entry["harness"] in harnesses,
                f"{field}.harness names unknown harness {entry['harness']!r}",
            )
            normalized["harness"] = entry["harness"]
        if "endpoint" in entry:
            endpoint = entry["endpoint"]
            if isinstance(endpoint, str):
                _require(
                    endpoint in normalized_endpoints,
                    f"{field}.endpoint names unknown shared endpoint {endpoint!r}",
                )
                normalized["endpoint"] = EndpointReference(endpoint, normalized_endpoints[endpoint])
            else:
                normalized["endpoint"] = _validate_endpoint(endpoint, f"{field}.endpoint")
        args = entry.get("args", [])
        _require(
            isinstance(args, list) and all(isinstance(item, str) and item for item in args),
            f"{field}.args must be a list of non-empty strings",
        )
        normalized["args"] = list(args)
        extra_env = entry.get("env", {})
        _require(isinstance(extra_env, dict), f"{field}.env must be an object")
        for key, value in extra_env.items():
            _require(
                isinstance(key, str)
                and re.fullmatch(r"[A-Z_][A-Z0-9_]*", key) is not None
                and isinstance(value, str),
                f"{field}.env keys must be UPPER_CASE names with string values",
            )
        normalized["env"] = dict(sorted(extra_env.items()))
        if "limits" in entry:
            limits = entry["limits"]
            _require(isinstance(limits, dict), f"{field}.limits must be an object")
            _require(
                set(limits) <= {"context", "output"},
                f"{field}.limits has unknown fields: {', '.join(sorted(set(limits) - {'context', 'output'}))}",
            )
            for key, value in limits.items():
                _require(
                    isinstance(value, int) and not isinstance(value, bool) and value > 0,
                    f"{field}.limits.{key} must be a positive integer",
                )
            if "context" in limits and "output" in limits:
                _require(
                    limits["output"] <= limits["context"],
                    f"{field}.limits.output must be less than or equal to context",
                )
            normalized["limits"] = dict(sorted(limits.items()))
        if "expiresAt" in entry:
            expires = entry["expiresAt"]
            _require(isinstance(expires, str), f"{field}.expiresAt must be a string")
            try:
                parse_expires_at(expires)
            except ProfilesError as exc:
                raise ProfilesError(f"{field}.{exc}") from exc
            normalized["expiresAt"] = expires
        if "origin" in entry:
            origin = entry["origin"]
            origin_required = {"kind", "capability", "url"}
            _require(
                isinstance(origin, dict)
                and origin_required <= set(origin) <= origin_required | {"leaseId", "state"},
                f"{field}.origin must contain kind, capability, url and optionally leaseId, state",
            )
            _require(
                origin["kind"] in ORIGIN_KINDS,
                f"{field}.origin.kind must be one of {', '.join(ORIGIN_KINDS)}",
            )
            _require(
                isinstance(origin["capability"], str) and bool(origin["capability"]),
                f"{field}.origin.capability must be a non-empty string",
            )
            _require(
                origin.get("leaseId") is None
                or (isinstance(origin["leaseId"], str) and bool(origin["leaseId"])),
                f"{field}.origin.leaseId must be a string or null",
            )
            _require(
                isinstance(origin["url"], str)
                and re.fullmatch(r"https?://\S+", origin["url"]) is not None,
                f"{field}.origin.url must be an http(s) URL",
            )
            _require(
                not isinstance(normalized.get("endpoint"), EndpointReference),
                f"{field}: pitwall-origin routes need an inline endpoint (refresh rewrites baseUrl per lease)",
            )
            normalized["origin"] = {
                "kind": origin["kind"],
                "capability": origin["capability"],
                "leaseId": origin.get("leaseId"),
                "url": origin["url"].rstrip("/"),
            }
            if "state" in origin:
                _require(
                    origin["state"] in ORIGIN_STATES,
                    f"{field}.origin.state must be one of {', '.join(ORIGIN_STATES)}",
                )
                normalized["origin"]["state"] = origin["state"]
        if "autoServe" in entry:
            auto_serve = entry["autoServe"]
            _require(isinstance(auto_serve, dict), f"{field}.autoServe must be an object")
            _require(
                set(auto_serve) <= AUTO_SERVE_KEYS,
                f"{field}.autoServe has unknown fields: {', '.join(sorted(set(auto_serve) - AUTO_SERVE_KEYS))}",
            )
            _require(
                normalized.get("origin", {}).get("kind") == "pitwall",
                f"{field}.autoServe requires a Pitwall origin",
            )
            if "maxUsdPerHour" in auto_serve:
                value = auto_serve["maxUsdPerHour"]
                _require(
                    isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0,
                    f"{field}.autoServe.maxUsdPerHour must be a number greater than 0",
                )
            if "ttlMinutes" in auto_serve:
                value = auto_serve["ttlMinutes"]
                _require(
                    isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 43200,
                    f"{field}.autoServe.ttlMinutes must be an integer from 1 to 43200",
                )
            if "idleTimeoutMinutes" in auto_serve:
                value = auto_serve["idleTimeoutMinutes"]
                _require(
                    isinstance(value, int) and not isinstance(value, bool) and value >= 5,
                    f"{field}.autoServe.idleTimeoutMinutes must be an integer of at least 5",
                )
            if "readyTimeoutMinutes" in auto_serve:
                value = auto_serve["readyTimeoutMinutes"]
                _require(
                    isinstance(value, int) and not isinstance(value, bool) and value >= 1,
                    f"{field}.autoServe.readyTimeoutMinutes must be an integer of at least 1",
                )
            normalized["autoServe"] = dict(auto_serve)
        if "effort" in entry:
            harness_for_entry = entry_harness(entry, defaults, registry)
            normalized["effort"] = validate_effort(
                registry, harness_for_entry, model, entry["effort"], f"{field}.effort"
            )
        for key, allowed in (("seat", SEATS), ("workspace", WORKSPACES), ("taskMode", TASK_MODES)):
            if key in entry:
                _require(
                    entry[key] in allowed, f"{field}.{key} must be one of {', '.join(allowed)}"
                )
                normalized[key] = entry[key]
        if "account" in entry:
            _require(
                isinstance(entry["account"], str)
                and ACCOUNT_LABEL.fullmatch(entry["account"]) is not None,
                f"{field}.account must be 1 to 16 letters, digits, or hyphens",
            )
            normalized["account"] = entry["account"]
        endpoint_object = resolved_endpoint(normalized)
        if endpoint_object is not None and endpoint_object.get("kind") == model_studio.KIND:
            _validate_model_studio_route(
                normalized,
                endpoint_object,
                field,
                harness=entry_harness(normalized, defaults, registry),
            )
        normalized_models[name] = normalized
    normalized_document = {
        "schemaVersion": 1,
        "defaults": defaults,
        "endpoints": normalized_endpoints,
        "harnesses": normalized_harnesses,
        "models": normalized_models,
    }
    if normalized_pitwall is not None:
        normalized_document["pitwall"] = normalized_pitwall
    return normalized_document


def load_profiles(env: Mapping[str, str], *, registry: Mapping[str, Any]) -> dict[str, Any]:
    path = profiles_path(env)
    if not path.exists():
        return empty_profiles()
    try:
        text = path.read_text(encoding="utf-8")
        table = profiles_toml.parse(text)
    except (
        OSError,
        ValueError,
    ) as exc:  # reason: ProfilesTomlError and UnicodeError are ValueErrors
        raise ProfilesError(f"cannot read agent profiles from {path}: {exc}") from exc
    try:
        return validate_profiles(table, registry=registry)
    except ProfilesError as exc:
        raise ProfilesError(f"invalid agent profiles in {path}: {exc}") from exc


def save_profiles(
    env: Mapping[str, str], data: Mapping[str, Any], *, registry: Mapping[str, Any]
) -> Path:
    normalized = validate_profiles(data, registry=registry)
    path = profiles_path(env)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        rewritten = profiles_toml.replace_profiles(existing, normalized)
    except (OSError, ValueError) as exc:  # reason: TOML and encoding failures are ValueErrors
        raise ProfilesError(f"cannot update agent profiles in {path}: {exc}") from exc
    atomic_write_bytes(path, rewritten.encode("utf-8"))
    return path


def add_profile(
    data: Mapping[str, Any],
    name: str,
    *,
    model: str,
    harness: str | None = None,
    endpoint: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    args: tuple[str, ...] | list[str] = (),
    env: Mapping[str, str] | None = None,
    limits: Mapping[str, int] | None = None,
    seat: str | None = None,
    workspace: str | None = None,
    task_mode: str | None = None,
    effort: str | None = None,
    expires_at: str | None = None,
    origin: Mapping[str, Any] | None = None,
    auto_serve: Mapping[str, Any] | None = None,
    account: str | None = None,
) -> dict[str, Any]:
    """Return a copy of ``data`` with ``name`` set (replacing any existing entry). Not validated."""
    if endpoint is not None and (base_url is not None or api_key_env is not None):
        raise ProfilesError("endpoint is mutually exclusive with base_url and api_key_env")
    if api_key_env is not None and base_url is None:
        raise ProfilesError("api_key_env requires base_url (an endpoint)")
    entry: dict[str, Any] = {"model": model}
    if harness is not None:
        entry["harness"] = harness
    if endpoint is not None:
        entry["endpoint"] = endpoint
    elif base_url is not None:
        entry["endpoint"] = {"baseUrl": base_url}
        if api_key_env is not None:
            entry["endpoint"]["apiKeyEnv"] = api_key_env
    if args:
        entry["args"] = list(args)
    if env:
        entry["env"] = dict(env)
    if limits is not None:
        entry["limits"] = dict(limits)
    if effort is not None:
        entry["effort"] = effort
    if expires_at is not None:
        entry["expiresAt"] = expires_at
    if origin is not None:
        entry["origin"] = dict(origin)
    if auto_serve is not None:
        entry["autoServe"] = dict(auto_serve)
    for key, value in (
        ("seat", seat),
        ("workspace", workspace),
        ("taskMode", task_mode),
        ("account", account),
    ):
        if value is not None:
            entry[key] = value
    updated: dict[str, Any] = json.loads(json.dumps(data))
    updated.setdefault("models", {})[name] = entry
    return updated


def remove_profile(data: Mapping[str, Any], name: str) -> dict[str, Any]:
    updated: dict[str, Any] = json.loads(json.dumps(data))
    if name not in updated.get("models", {}):
        raise ProfilesError(f"no route named {name!r}")
    del updated["models"][name]
    return updated
