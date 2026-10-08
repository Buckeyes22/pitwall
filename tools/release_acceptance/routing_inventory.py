"""Routing-surface inventory for the agent-routing provider registry.

Extracts deterministic surface rows from the canonical provider registry at
``src/pitwall/agents/resources/config/harness-registry.json``
and the shims ``pitwall agents install`` generates (``pitwall.agents.installation.shim_script``).

A *surface* is one routable artifact:

* ``routing:provider:<id>`` - a provider record from the registry. The entire
  canonical registry record and its sha256 digest are preserved in metadata.
* ``routing:host:<id>`` - a host record (plugin package). ``nativeHarnesses``,
  ``packagePath``, and the record digest are preserved in metadata.
* ``shim:<stem>`` - one generated ``*-shim.sh`` wrapper, with its sha256 and the
  provider IDs that reference it (by registry ``shim`` field). ``route-shim.sh``
  is the shared runtime dispatcher: it is independent of any single provider
  and is marked as the explicit special dispatcher.

``discover`` returns the surface rows; ``discover_with_issues`` additionally
returns explicit issues (registered shim names that cannot be generated, and
the scope items handled by separate later units) without dropping any row.

Only JSON parsing and byte hashing are performed. Shims are never executed,
only the light ``installation`` module is imported for the wrapper template,
and no credentials are read.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pitwall.agents.installation import shim_script

REGISTRY_REL_PATH = Path("src/pitwall/agents/resources/config/harness-registry.json")
SHIM_SOURCE_REL_PATH = Path("src/pitwall/agents/installation.py")
SHIM_SUFFIX = "-shim.sh"
ROUTE_SHIM_FILENAME = "route-shim.sh"
SUPPORTED_SCHEMA_VERSION = 1

KIND_PROVIDER = "provider"
KIND_HOST = "plugin"
KIND_SHIM = "shim"

OP_PROVIDER = "provider-registration"
OP_HOST = "host-plugin"
OP_SHIM_COMPAT = "compatibility-shim"
OP_SHIM_ROUTE = "route-dispatcher"

ISSUE_MISSING_SHIM = "missing-referenced-shim"
ISSUE_SEVERITY = "warning"
UNRESOLVED_SEVERITY = "unresolved"

_UNRESOLVED_SCOPES: tuple[tuple[str, str], ...] = (
    (
        "unresolved-adapter-class-contracts",
        "adapter class contracts are not extracted by routing_inventory; "
        "tracked as a separate later unit",
    ),
    (
        "unresolved-plugin-manifests-and-marketplaces",
        "plugin manifests and marketplaces are not extracted by routing_inventory; "
        "tracked as a separate later unit",
    ),
    (
        "unresolved-channel-routing-mcp-tools",
        "channel/routing MCP tools are not extracted by routing_inventory; "
        "tracked as a separate later unit",
    ),
)

_OPTIONAL_STRING_FIELDS = (
    "binaryOverrideEnv",
    "harnessKind",
    "endpointDelivery",
    "promptDelivery",
)
_OPTIONAL_STRING_LIST_FIELDS = ("binaryCandidates", "modelSelectors")
_OPTIONAL_OBJECT_FIELDS = ("effort", "defaultModel", "endpointEnv", "configSync", "models")
_OPTIONAL_BOOL_FIELDS = ("allowUnknownModels",)


class RegistryError(ValueError):
    """Raised when the provider registry is missing or malformed.

    The message is a diagnostic that names the registry path and the JSON
    pointer of the offending value.
    """


def discover(root: str | Path) -> list[dict[str, Any]]:
    """Return the deterministic, ``surface_id``-sorted surface rows for ``root``."""
    return cast(list[dict[str, Any]], discover_with_issues(root)["surfaces"])


def discover_with_issues(root: str | Path) -> dict[str, Any]:
    """Return ``{"surfaces", "issues", "schema_version"}`` for ``root``.

    Raises :class:`RegistryError` when the registry file is missing, is not
    valid JSON, or violates the known schemaVersion 1 shapes.
    """
    root = Path(root)
    registry_path = root / REGISTRY_REL_PATH
    data, text = _load_registry(registry_path)
    _validate_registry(data)

    providers: dict[str, Any] = data["harnesses"]
    hosts: dict[str, Any] = data["hosts"]

    generated_names = {ROUTE_SHIM_FILENAME} | {
        record["shim"] for record in providers.values() if record["shim"].endswith(SHIM_SUFFIX)
    }
    shims_by_name = {name: shim_script(name).encode("utf-8") for name in sorted(generated_names)}
    shim_source_line = _shim_source_line(root)

    referenced: dict[str, list[str]] = {}
    for provider_id in sorted(providers):
        referenced.setdefault(providers[provider_id]["shim"], []).append(provider_id)

    surfaces: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    for provider_id in sorted(providers):
        record = providers[provider_id]
        pointer: str | None = f"/harnesses/{_pointer_token(provider_id)}"
        surfaces.append(
            {
                "surface_id": f"routing:provider:{provider_id}",
                "kind": KIND_PROVIDER,
                "operation": OP_PROVIDER,
                "source": _registry_source(text, "harnesses", provider_id),
                "metadata": {
                    "json_pointer": pointer,
                    "sha256": _record_digest(record),
                    "shim": record["shim"],
                    "native_hosts": list(record["nativeHosts"]),
                    "capabilities": record["capabilities"],
                    "record": record,
                },
            }
        )
        if record["shim"] not in shims_by_name:
            issues.append(
                {
                    "code": ISSUE_MISSING_SHIM,
                    "message": (
                        f"provider {provider_id!r} references shim {record['shim']!r} "
                        f"but install cannot generate it (names must end in {SHIM_SUFFIX})"
                    ),
                    "severity": ISSUE_SEVERITY,
                    "surface_ids": [f"routing:provider:{provider_id}"],
                }
            )

    for host_id in sorted(hosts):
        record = hosts[host_id]
        pointer = f"/hosts/{_pointer_token(host_id)}"
        surfaces.append(
            {
                "surface_id": f"routing:host:{host_id}",
                "kind": KIND_HOST,
                "operation": OP_HOST,
                "source": _registry_source(text, "hosts", host_id),
                "metadata": {
                    "json_pointer": pointer,
                    "sha256": _record_digest(record),
                    "package_path": record["packagePath"],
                    "native_providers": list(record["nativeHarnesses"]),
                    "record": record,
                },
            }
        )

    for name in sorted(shims_by_name):
        content = shims_by_name[name]
        associated = referenced.get(name, [])
        special = name == ROUTE_SHIM_FILENAME
        operation = OP_SHIM_ROUTE if special else OP_SHIM_COMPAT
        pointer = (
            f"/harnesses/{_pointer_token(associated[0])}/shim" if len(associated) == 1 else None
        )
        stem = _shim_stem(name)
        surfaces.append(
            {
                "surface_id": f"shim:{stem}",
                "kind": KIND_SHIM,
                "operation": operation,
                "source": f"{SHIM_SOURCE_REL_PATH.as_posix()}:{shim_source_line}",
                "metadata": {
                    "json_pointer": pointer,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "providers": list(associated),
                    "special_dispatcher": special,
                },
            }
        )

    for code, message in _UNRESOLVED_SCOPES:
        issues.append(
            {
                "code": code,
                "message": message,
                "severity": UNRESOLVED_SEVERITY,
                "surface_ids": [],
            }
        )

    surfaces.sort(key=lambda surface: surface["surface_id"])
    issues.sort(key=lambda issue: (issue["code"], ",".join(issue["surface_ids"]), issue["message"]))
    return {
        "surfaces": surfaces,
        "issues": issues,
        "schema_version": data["schemaVersion"],
    }


def _load_registry(path: Path) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise RegistryError(f"{REGISTRY_REL_PATH.as_posix()}: registry file not found at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegistryError(
            f"{REGISTRY_REL_PATH.as_posix()}: invalid JSON at line {exc.lineno} column {exc.colno}"
        ) from exc
    if not isinstance(data, dict):
        raise _malformed("", f"registry root must be an object, got {type(data).__name__}")
    return data, text


def _malformed(pointer: str, detail: str) -> RegistryError:
    return RegistryError(f"{REGISTRY_REL_PATH.as_posix()}: {pointer or '/'}: {detail}")


def _validate_registry(data: dict[str, Any]) -> None:
    schema = data.get("schemaVersion")
    if isinstance(schema, bool) or not isinstance(schema, int):
        raise _malformed("/schemaVersion", "must be an integer")
    if schema != SUPPORTED_SCHEMA_VERSION:
        raise _malformed(
            "/schemaVersion",
            f"unsupported schemaVersion {schema!r}, expected {SUPPORTED_SCHEMA_VERSION}",
        )
    _validate_hosts(data.get("hosts"))
    _validate_providers(data.get("harnesses"))


def _validate_hosts(hosts: Any) -> None:
    if not isinstance(hosts, dict):
        raise _malformed(
            "/hosts", f"must be an object keyed by host id, got {type(hosts).__name__}"
        )
    for host_id, record in hosts.items():
        pointer = f"/hosts/{_pointer_token(host_id)}"
        if not isinstance(record, dict):
            raise _malformed(pointer, f"host record must be an object, got {type(record).__name__}")
        _require_string(record, "displayName", pointer)
        _require_string(record, "packagePath", pointer)
        _require_string_list(record, "nativeHarnesses", pointer)


def _validate_providers(providers: Any) -> None:
    if not isinstance(providers, dict):
        raise _malformed(
            "/harnesses", f"must be an object keyed by harness id, got {type(providers).__name__}"
        )
    for provider_id, record in providers.items():
        pointer = f"/harnesses/{_pointer_token(provider_id)}"
        if not isinstance(record, dict):
            raise _malformed(
                pointer, f"provider record must be an object, got {type(record).__name__}"
            )
        _require_string(record, "displayName", pointer)
        _require_string(record, "shim", pointer)
        _require_string_list(record, "nativeHosts", pointer)

        capabilities = record.get("capabilities")
        if not isinstance(capabilities, dict):
            raise _malformed(
                f"{pointer}/capabilities",
                f"must be an object, got {type(capabilities).__name__}",
            )
        for name, value in capabilities.items():
            if not isinstance(value, bool):
                raise _malformed(
                    f"{pointer}/capabilities/{_pointer_token(name)}",
                    f"must be a boolean, got {type(value).__name__}",
                )

        for field in _OPTIONAL_STRING_FIELDS:
            if field in record:
                _require_string(record, field, pointer)
        for field in _OPTIONAL_STRING_LIST_FIELDS:
            if field in record:
                _require_string_list(record, field, pointer)
        for field in _OPTIONAL_OBJECT_FIELDS:
            if field in record and not isinstance(record[field], dict):
                raise _malformed(
                    f"{pointer}/{field}", f"must be an object, got {type(record[field]).__name__}"
                )
        for field in _OPTIONAL_BOOL_FIELDS:
            if field in record and not isinstance(record[field], bool):
                raise _malformed(
                    f"{pointer}/{field}", f"must be a boolean, got {type(record[field]).__name__}"
                )
        if "routeFamilies" in record:
            _validate_route_families(record["routeFamilies"], pointer)
        if "models" in record:
            _validate_models(record["models"], pointer)


def _validate_route_families(families: Any, pointer: str) -> None:
    if not isinstance(families, list):
        raise _malformed(
            f"{pointer}/routeFamilies", f"must be an array, got {type(families).__name__}"
        )
    for index, family in enumerate(families):
        entry = f"{pointer}/routeFamilies/{index}"
        if not isinstance(family, dict):
            raise _malformed(entry, f"route family must be an object, got {type(family).__name__}")
        _require_string(family, "id", entry)
        _require_string(family, "displayName", entry)


def _validate_models(models: Any, pointer: str) -> None:
    if not isinstance(models, dict):
        raise _malformed(f"{pointer}/models", f"must be an object, got {type(models).__name__}")
    for model_id, model in models.items():
        entry = f"{pointer}/models/{_pointer_token(model_id)}"
        if not isinstance(model, dict):
            raise _malformed(entry, f"model record must be an object, got {type(model).__name__}")
        _require_string(model, "displayName", entry)


def _require_string(record: dict[str, Any], field: str, pointer: str) -> None:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise _malformed(f"{pointer}/{field}", "must be a non-empty string")


def _require_string_list(record: dict[str, Any], field: str, pointer: str) -> None:
    value = record.get(field)
    if not isinstance(value, list):
        raise _malformed(
            f"{pointer}/{field}", f"must be an array of strings, got {type(value).__name__}"
        )
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise _malformed(
                f"{pointer}/{field}/{index}", f"must be a string, got {type(item).__name__}"
            )


def _pointer_token(token: Any) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def _record_digest(record: dict[str, Any]) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _shim_source_line(root: Path) -> int:
    """The line where the wrapper template is defined, for a stable ``path:line`` source."""

    try:
        lines = (root / SHIM_SOURCE_REL_PATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        return 1
    for number, line in enumerate(lines, 1):
        if line.startswith("def shim_script("):
            return number
    return 1


def _registry_source(text: str, section: str, key: str) -> str:
    return f"{REGISTRY_REL_PATH.as_posix()}:{_line_for_key(text, section, key)}"


def _line_for_key(text: str, section: str, key: str) -> int:
    section_start = text.find(f'"{section}"')
    if section_start < 0:
        return 1
    index = text.find(f'"{key}":', section_start)
    if index < 0:
        return 1
    return text.count("\n", 0, index) + 1


def _shim_stem(filename: str) -> str:
    stem = filename[: -len(".sh")] if filename.endswith(".sh") else filename
    if stem.endswith("-shim"):
        stem = stem[: -len("-shim")]
    return stem or filename
