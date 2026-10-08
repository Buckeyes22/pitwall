"""Load and semantically validate the canonical harness registry."""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from .resources import ResourceError, read_resource_json


class RegistryError(ValueError):
    """Raised when harness-registry data violates the runtime contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RegistryError(message)


def _require_string(value: Any, field: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{field} must be a non-empty string")
    text: str = value
    return text


def _require_string_list(value: Any, field: str, *, min_items: int = 0) -> list[str]:
    _require(isinstance(value, list), f"{field} must be a list")
    _require(all(isinstance(item, str) and item for item in value), f"{field} must contain strings")
    _require(len(value) == len(set(value)), f"{field} must not contain duplicates")
    _require(len(value) >= min_items, f"{field} must contain at least {min_items} item(s)")
    items: list[str] = value
    return items


def _require_keys(
    value: dict[str, Any], field: str, required: set[str], optional: set[str] | None = None
) -> None:
    allowed = required | (optional or set())
    missing = required - set(value)
    extra = set(value) - allowed
    _require(not missing, f"{field} is missing required fields: {', '.join(sorted(missing))}")
    _require(not extra, f"{field} has unknown fields: {', '.join(sorted(extra))}")


def _require_identifier(value: Any, field: str, *, pattern: str) -> str:
    identifier = _require_string(value, field)
    _require(
        re.fullmatch(pattern, identifier) is not None, f"{field} has invalid format: {identifier!r}"
    )
    _require(".." not in identifier.split("/"), f"{field} must not contain a parent path segment")
    return identifier


def _relative_path(relative: str, field: str) -> PurePosixPath:
    candidate = PurePosixPath(relative)
    _require(not candidate.is_absolute(), f"{field} must be relative to the repository")
    _require(
        bool(candidate.parts) and all(part not in {"", ".", ".."} for part in candidate.parts),
        f"{field} contains an unsafe path: {relative}",
    )
    return candidate


def _source_path(repo_root: Path, relative: str, field: str) -> Path:
    candidate = _relative_path(relative, field)
    root = repo_root.resolve()
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise RegistryError(f"{field} escapes the repository: {relative}") from exc
    return resolved


def load_registry(path: Path | None = None) -> dict[str, Any]:
    if path is None:
        try:
            data = read_resource_json("config/harness-registry.json")
        except ResourceError as exc:
            raise RegistryError(str(exc)) from exc
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryError(f"cannot load harness registry {path}: {exc}") from exc
    validate_registry(data)
    registry: dict[str, Any] = data
    return registry


def validate_registry(data: Any) -> None:
    _require(isinstance(data, dict), "registry must be an object")
    _require(data.get("schemaVersion") == 1, "registry schemaVersion must be 1")
    hosts = data.get("hosts")
    harnesses = data.get("harnesses")
    _require(bool(isinstance(hosts, dict) and hosts), "hosts must be a non-empty object")
    _require(
        bool(isinstance(harnesses, dict) and harnesses), "harnesses must be a non-empty object"
    )
    _require(
        "mythos" not in json.dumps(data).lower(),
        "registry must not define a Mythos-specific surface",
    )
    _require_keys(data, "registry", {"schemaVersion", "hosts", "harnesses"}, {"modelFamilies"})

    for host_id, host in hosts.items():
        _require_identifier(host_id, "host id", pattern=r"[a-z0-9-]+")
        _require(isinstance(host, dict), f"host {host_id} must be an object")
        _require_keys(host, f"hosts.{host_id}", {"displayName", "packagePath", "nativeHarnesses"})
        _require_string(host.get("displayName"), f"hosts.{host_id}.displayName")
        package_path = _require_string(host.get("packagePath"), f"hosts.{host_id}.packagePath")
        _relative_path(package_path, f"hosts.{host_id}.packagePath")
        native = _require_string_list(
            host.get("nativeHarnesses"), f"hosts.{host_id}.nativeHarnesses"
        )
        for harness_id in native:
            _require(
                harness_id in harnesses,
                f"host {host_id} names unknown native harness {harness_id}",
            )

    aliases: dict[str, str] = {}
    shims: set[str] = set()
    for harness_id, harness in harnesses.items():
        _require_identifier(harness_id, "harness id", pattern=r"[a-z0-9-]+")
        _require(isinstance(harness, dict), f"harness {harness_id} must be an object")
        _require_keys(
            harness,
            f"harnesses.{harness_id}",
            {
                "displayName",
                "shim",
                "binaryCandidates",
                "nativeHosts",
                "promptDelivery",
                "harnessKind",
                "endpointDelivery",
                "allowUnknownModels",
                "defaultModel",
                "modelSelectors",
                "effort",
                "capabilities",
                "models",
                "routeFamilies",
            },
            {"binaryOverrideEnv", "endpointEnv", "configSync"},
        )
        _require_string(harness.get("displayName"), f"harnesses.{harness_id}.displayName")
        shim = _require_string(harness.get("shim"), f"harnesses.{harness_id}.shim")
        _require(
            re.fullmatch(r"[a-z0-9-]+-shim\.sh", shim) is not None,
            f"harness {harness_id} has invalid shim name",
        )
        _require(shim not in shims, f"shim {shim} is assigned to multiple harnesses")
        shims.add(shim)
        _require_string_list(
            harness.get("binaryCandidates"),
            f"harnesses.{harness_id}.binaryCandidates",
            min_items=1,
        )
        override = harness.get("binaryOverrideEnv")
        if override is not None:
            _require(
                isinstance(override, str)
                and re.fullmatch(r"[A-Z][A-Z0-9_]+", override) is not None,
                f"harnesses.{harness_id}.binaryOverrideEnv has invalid format",
            )
        native_hosts = _require_string_list(
            harness.get("nativeHosts"), f"harnesses.{harness_id}.nativeHosts"
        )
        for host_id in native_hosts:
            _require(host_id in hosts, f"harness {harness_id} names unknown native host {host_id}")
            _require(
                harness_id in hosts[host_id]["nativeHarnesses"],
                f"harness {harness_id} and host {host_id} disagree about native ownership",
            )
        _require(
            harness.get("promptDelivery") in {"stdin", "argv", "file"},
            f"harness {harness_id} has invalid promptDelivery",
        )
        _require(
            harness.get("harnessKind") in {"model-bound", "model-agnostic"},
            f"harness {harness_id} has invalid harnessKind",
        )
        delivery = harness.get("endpointDelivery")
        _require(
            delivery in {"none", "env", "config-sync"},
            f"harness {harness_id} has invalid endpointDelivery",
        )
        if delivery == "env":
            endpoint_env = harness.get("endpointEnv")
            _require(
                isinstance(endpoint_env, dict),
                f"harness {harness_id} endpointEnv is required for env delivery",
            )
            _require_keys(
                endpoint_env, f"harnesses.{harness_id}.endpointEnv", {"baseUrl", "apiKey", "model"}
            )
            for key, value in endpoint_env.items():
                _require(
                    isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]+", value) is not None,
                    f"harnesses.{harness_id}.endpointEnv.{key} must name an environment variable",
                )
        else:
            _require(
                "endpointEnv" not in harness,
                f"harness {harness_id} endpointEnv is only valid for env delivery",
            )
        if delivery == "config-sync":
            config_sync = harness.get("configSync")
            _require(
                isinstance(config_sync, dict),
                f"harness {harness_id} configSync is required for config-sync delivery",
            )
            _require_keys(config_sync, f"harnesses.{harness_id}.configSync", {"path"})
            _require_string(config_sync.get("path"), f"harnesses.{harness_id}.configSync.path")
        else:
            _require(
                "configSync" not in harness,
                f"harness {harness_id} configSync is only valid for config-sync delivery",
            )
        if harness["harnessKind"] == "model-bound":
            _require(
                delivery == "none",
                f"harness {harness_id} is model-bound and cannot accept endpoints",
            )
        _require(
            isinstance(harness.get("allowUnknownModels"), bool),
            f"harness {harness_id} allowUnknownModels must be boolean",
        )
        _require_string_list(
            harness.get("modelSelectors"), f"harnesses.{harness_id}.modelSelectors"
        )

        effort = harness.get("effort")
        _require(isinstance(effort, dict), f"harness {harness_id} effort must be an object")
        _require_keys(effort, f"harnesses.{harness_id}.effort", {"kind", "key", "values"})
        effort_kind = effort.get("kind")
        _require(
            effort_kind in {"config", "harness-flag", "none"},
            f"harness {harness_id} has invalid effort kind",
        )
        effort_values = _require_string_list(
            effort.get("values"), f"harnesses.{harness_id}.effort.values"
        )
        if effort_kind == "none":
            _require(
                effort.get("key") is None,
                f"harness {harness_id} effort key must be null when kind is none",
            )
            _require(
                not effort_values,
                f"harness {harness_id} effort values must be empty when kind is none",
            )
        else:
            _require_string(effort.get("key"), f"harnesses.{harness_id}.effort.key")

        capabilities = harness.get("capabilities")
        _require(
            isinstance(capabilities, dict), f"harness {harness_id} capabilities must be an object"
        )
        _require_keys(
            capabilities,
            f"harnesses.{harness_id}.capabilities",
            {"authProbe", "configProbe", "worktreeDispatch", "structuredOutput"},
        )
        for capability, enabled in capabilities.items():
            _require(
                isinstance(enabled, bool),
                f"harnesses.{harness_id}.capabilities.{capability} must be boolean",
            )

        default_model = harness.get("defaultModel")
        _require(
            isinstance(default_model, dict),
            f"harness {harness_id} defaultModel must be an object",
        )
        _require_keys(default_model, f"harnesses.{harness_id}.defaultModel", {"source", "fallback"})
        _require(
            default_model.get("source")
            in {
                "registry",
                "codex-config",
                "kimi-config",
                "qwen-config",
                "pi-config",
                "hermes-config",
                "cline-config",
                "goose-config",
                "dsh-config",
                "positional",
            },
            f"harness {harness_id} has invalid default model source",
        )
        fallback = default_model.get("fallback")
        _require(
            fallback is None or isinstance(fallback, str),
            f"harness {harness_id} fallback must be string or null",
        )
        if default_model.get("source") == "positional":
            _require(
                fallback is None,
                f"harness {harness_id} positional default must have a null fallback",
            )

        models = harness.get("models")
        families = harness.get("routeFamilies")
        _require(isinstance(models, dict), f"harness {harness_id} models must be an object")
        _require(isinstance(families, list), f"harness {harness_id} routeFamilies must be a list")
        if default_model.get("source") == "registry":
            _require(
                fallback in models,
                f"harness {harness_id} registry fallback {fallback!r} is not a known model",
            )

        for model_id, model in models.items():
            _require_identifier(
                model_id,
                f"harnesses.{harness_id}.model id",
                pattern=r"[A-Za-z0-9][A-Za-z0-9._/-]*",
            )
            _require(isinstance(model, dict), f"model {harness_id}/{model_id} must be an object")
            _require_keys(
                model,
                f"harnesses.{harness_id}.models.{model_id}",
                {
                    "displayName",
                    "aliases",
                    "effortValues",
                    "promptReference",
                    "runtimeReference",
                    "capabilityCard",
                    "provenance",
                },
            )
            _require_string(
                model.get("displayName"), f"harnesses.{harness_id}.models.{model_id}.displayName"
            )
            _require_string(
                model.get("provenance"), f"harnesses.{harness_id}.models.{model_id}.provenance"
            )
            _validate_reference_fields(model, hosts, f"harnesses.{harness_id}.models.{model_id}")
            model_aliases = _require_string_list(
                model.get("aliases"), f"harnesses.{harness_id}.models.{model_id}.aliases"
            )
            _require_string_list(
                model.get("effortValues"), f"harnesses.{harness_id}.models.{model_id}.effortValues"
            )
            for alias in [model_id, *model_aliases]:
                owner = aliases.setdefault(alias.lower(), f"{harness_id}/{model_id}")
                _require(
                    owner == f"{harness_id}/{model_id}",
                    f"model alias {alias!r} is duplicated by {owner}",
                )

        family_ids: set[str] = set()
        for family in families:
            _require(
                isinstance(family, dict), f"harness {harness_id} route family must be an object"
            )
            _require_keys(
                family,
                f"harnesses.{harness_id}.routeFamilies",
                {
                    "id",
                    "displayName",
                    "patterns",
                    "example",
                    "promptReference",
                    "runtimeReference",
                    "capabilityCard",
                },
            )
            family_id = _require_identifier(
                family.get("id"), f"harnesses.{harness_id}.routeFamilies.id", pattern=r"[a-z0-9-]+"
            )
            _require(
                family_id not in family_ids,
                f"harness {harness_id} duplicates route family {family_id}",
            )
            family_ids.add(family_id)
            _require_string(
                family.get("displayName"),
                f"harnesses.{harness_id}.routeFamilies.{family_id}.displayName",
            )
            _require_string_list(
                family.get("patterns"),
                f"harnesses.{harness_id}.routeFamilies.{family_id}.patterns",
                min_items=1,
            )
            _require_string(
                family.get("example"), f"harnesses.{harness_id}.routeFamilies.{family_id}.example"
            )
            _validate_reference_fields(
                family,
                hosts,
                f"harnesses.{harness_id}.routeFamilies.{family_id}",
            )

    _validate_model_families(data.get("modelFamilies", {}), hosts, harnesses)

    for host_id, host in hosts.items():
        for harness_id in host["nativeHarnesses"]:
            _require(
                host_id in harnesses[harness_id]["nativeHosts"],
                f"host {host_id} and harness {harness_id} disagree about native ownership",
            )

    from pitwall.agents.harnesses import adapter_ids, get_adapter

    _require(
        set(harnesses) == adapter_ids(), "harness registry and adapter implementations must match"
    )
    for harness_id, harness in harnesses.items():
        adapter = get_adapter(harness_id)
        _require(
            adapter.prompt_delivery == harness["promptDelivery"],
            f"harness {harness_id} registry and adapter disagree about prompt delivery",
        )
        _require(
            adapter.binary_override_env == harness.get("binaryOverrideEnv"),
            f"harness {harness_id} registry and adapter disagree about binary override",
        )
        _require(
            adapter.endpoint_delivery == harness["endpointDelivery"],
            f"harness {harness_id} registry and adapter disagree about endpoint delivery",
        )


_FAMILY_REQUIRED = {
    "displayName",
    "vendor",
    "patterns",
    "models",
    "license",
    "contextWindow",
    "reasoningControl",
    "modelCardUrl",
    "promptReference",
    "runtimeReference",
    "capabilityCard",
    "provenance",
}
_FAMILY_OPTIONAL = {"samplingDefaults", "servingHint"}
_REASONING_KINDS = {
    "system-prompt-line",
    "request-parameter",
    "chat-template",
    "system-prompt-token",
    "none",
}


def _validate_model_families(
    families: Any,
    hosts: dict[str, Any],
    harnesses: dict[str, Any],
) -> None:
    _require(isinstance(families, dict), "modelFamilies must be an object")
    taken_patterns: dict[str, str] = {}
    for harness_id, harness in harnesses.items():
        for family in harness["routeFamilies"]:
            for pattern in family["patterns"]:
                taken_patterns[pattern.lower()] = f"{harness_id}/{family['id']}"
    harness_models = {
        model_id.lower(): harness_id
        for harness_id, harness in harnesses.items()
        for model_id in harness["models"]
    }
    seen_models: dict[str, str] = {}
    for family_id, family in families.items():
        _require_identifier(family_id, "model family id", pattern=r"[a-z0-9-]+")
        _require(isinstance(family, dict), f"modelFamilies.{family_id} must be an object")
        _require_keys(family, f"modelFamilies.{family_id}", _FAMILY_REQUIRED, _FAMILY_OPTIONAL)
        field = f"modelFamilies.{family_id}"
        for key in ("displayName", "vendor", "license", "provenance"):
            _require_string(family.get(key), f"{field}.{key}")
        url = _require_string(family.get("modelCardUrl"), f"{field}.modelCardUrl")
        _require(url.startswith("https://"), f"{field}.modelCardUrl must be an https URL")
        window = family.get("contextWindow")
        _require(
            type(window) is int and window > 0, f"{field}.contextWindow must be a positive integer"
        )
        control = family.get("reasoningControl")
        _require(isinstance(control, dict), f"{field}.reasoningControl must be an object")
        _require_keys(control, f"{field}.reasoningControl", {"kind", "detail"})
        _require(
            control.get("kind") in _REASONING_KINDS, f"{field}.reasoningControl.kind is invalid"
        )
        _require_string(control.get("detail"), f"{field}.reasoningControl.detail")
        if "samplingDefaults" in family:
            _require(
                isinstance(family["samplingDefaults"], dict),
                f"{field}.samplingDefaults must be an object",
            )
        if "servingHint" in family:
            _require_string(family["servingHint"], f"{field}.servingHint")
        for pattern in _require_string_list(
            family.get("patterns"), f"{field}.patterns", min_items=1
        ):
            owner = taken_patterns.setdefault(pattern.lower(), family_id)
            _require(owner == family_id, f"{field} pattern {pattern!r} is already owned by {owner}")
        for model_id in _require_string_list(family.get("models"), f"{field}.models", min_items=1):
            _require(
                model_id.lower() not in harness_models,
                f"{field} model {model_id!r} is owned by harness {harness_models.get(model_id.lower())}",
            )
            owner = seen_models.setdefault(model_id.lower(), family_id)
            _require(owner == family_id, f"{field} model {model_id!r} is duplicated by {owner}")
        _validate_reference_fields(family, hosts, field)


def family_for_model(registry: dict[str, Any], model: str) -> str | None:
    """Return the modelFamilies id whose patterns match ``model`` (case-insensitive), or None."""
    lowered = model.lower()
    for family_id, family in registry.get("modelFamilies", {}).items():
        if any(fnmatch.fnmatchcase(lowered, pattern.lower()) for pattern in family["patterns"]):
            return str(family_id)
    return None


def _validate_reference_fields(
    item: dict[str, Any],
    hosts: dict[str, Any],
    field: str,
) -> None:
    for key in ("promptReference", "capabilityCard"):
        relative = _require_string(item.get(key), f"{field}.{key}")
        _relative_path(relative, f"{field}.{key}")
    runtime_reference = _require_string(item.get("runtimeReference"), f"{field}.runtimeReference")
    relative, separator, anchor = runtime_reference.partition("#")
    _require(
        relative == "references/model-prompting.md",
        f"{field}.runtimeReference must use package-local model-prompting.md",
    )
    _require(bool(separator and anchor), f"{field}.runtimeReference must include an anchor")
    for host_id, host in hosts.items():
        _relative_path(
            f"{host['packagePath']}/skills/subagent-model-routing/{relative}",
            f"hosts.{host_id}.runtimeReferenceBundle",
        )


def validate_source_layout(data: Any, *, repo_root: Path) -> None:
    """Validate clone-only paths and reference anchors for source CI."""

    validate_registry(data)
    hosts = data["hosts"]
    harnesses = data["harnesses"]
    for host_id, host in hosts.items():
        package_path = host["packagePath"]
        _require(
            _source_path(repo_root, package_path, f"hosts.{host_id}.packagePath").is_dir(),
            f"host {host_id} package does not exist: {package_path}",
        )
    for harness_id, harness in harnesses.items():
        for model_id, model in harness["models"].items():
            _validate_source_references(
                model,
                repo_root,
                hosts,
                f"harnesses.{harness_id}.models.{model_id}",
            )
        for family in harness["routeFamilies"]:
            _validate_source_references(
                family,
                repo_root,
                hosts,
                f"harnesses.{harness_id}.routeFamilies.{family['id']}",
            )
    for family_id, family in data.get("modelFamilies", {}).items():
        _validate_source_references(
            family,
            repo_root,
            hosts,
            f"modelFamilies.{family_id}",
        )


def _validate_source_references(
    item: dict[str, Any],
    repo_root: Path,
    hosts: dict[str, Any],
    field: str,
) -> None:
    for key in ("promptReference", "capabilityCard"):
        relative = item[key]
        _require(
            _source_path(repo_root, relative, f"{field}.{key}").is_file(),
            f"{field}.{key} does not exist: {relative}",
        )
    relative, _, anchor = item["runtimeReference"].partition("#")
    for host_id, host in hosts.items():
        bundle = _source_path(
            repo_root,
            f"{host['packagePath']}/skills/subagent-model-routing/{relative}",
            f"hosts.{host_id}.runtimeReferenceBundle",
        )
        _require(
            bundle.is_file(),
            f"host {host_id} runtime reference bundle does not exist: {bundle}",
        )
        try:
            contents = bundle.read_text(encoding="utf-8").lower()
        except (OSError, UnicodeError) as exc:
            raise RegistryError(f"cannot read runtime reference bundle {bundle}: {exc}") from exc
        _require(
            f"(#{anchor.lower()})" in contents,
            f"{field}.runtimeReference anchor does not exist for host {host_id}: {anchor}",
        )
