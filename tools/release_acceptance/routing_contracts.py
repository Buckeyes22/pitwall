"""Routing declaration inventories for plugins, adapters, and channel MCP tools.

This module owns the routing declaration surfaces that ``routing_inventory``
does not:

* ``plugin:manifest:<relative-path>`` - one shipped plugin declaration,
  discovered by manifest glob under ``plugins`` (never
  by hardcoded plugin name). The full parsed record and its canonical digest
  are preserved.
* ``plugin:marketplace:<host>:<entry-name>`` - one marketplace listing from
  ``.claude-plugin/marketplace.json``, ``.agents/plugins/marketplace.json``,
  and ``.github/plugin/marketplace.json``, with the full parsed entry, its
  canonical digest, the normalized source path, and host ownership resolved
  against the shipped provider registry and discovered manifests.
* ``routing:adapter:<provider_id>`` - one adapter class that declares a
  literal ``provider_id``. Declared attributes and method names are read by
  AST, inherited defaults come from ``providers/base.py`` (whose hash is
  recorded), and static ``_ADAPTERS`` registry bindings are captured. No
  adapter is imported or instantiated.
* ``routing:mcp:<role>:<tool-name>`` - one channel MCP tool definition for
  each role where ``build_server`` registers it. Schemas are captured by a
  fresh minimal-environment subprocess that imports the candidate
  ``pitwall.agents.mcp_tools`` with its origin asserted, builds the server with
  in-memory streams and controlled synthetic environments, and inspects the
  registered definitions. No handler, inbox, run-state, or credential is ever
  touched, and no server, network, or provider operation is possible.

``discover`` returns the surface rows; ``discover_with_issues`` additionally
returns explicit issues for malformed declarations, unresolved constructs
(dynamic registry bindings, unreadable provider ids, missing families), and
declaration/binding mismatches. Malformed known JSON is rejected with a
diagnostic naming the file and JSON pointer.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import textwrap
import uuid
from pathlib import Path
from typing import Any, cast

ROW_SCHEMA_VERSION = 1

PACKAGE_REL_PATH = Path(".")
PLUGIN_ROOTS_REL_PATH = PACKAGE_REL_PATH / "plugins"
PLUGIN_MANIFEST_GLOB = "**/plugin.json"
RUNTIME_REL_PATH = PACKAGE_REL_PATH / "src"
ADAPTERS_REL_PATH = RUNTIME_REL_PATH / "pitwall" / "agents" / "harnesses"
ADAPTER_BASE_NAME = "base.py"
ADAPTER_BASE_CLASS = "HarnessAdapter"
MCP_TOOLS_REL_PATH = RUNTIME_REL_PATH / "pitwall" / "agents" / "mcp_tools.py"
MCP_SERVER_REL_PATH = RUNTIME_REL_PATH / "pitwall" / "agents" / "mcp_server.py"
MCP_PACKAGE_NAME = "pitwall.agents"
REGISTRY_REL_PATH = (
    RUNTIME_REL_PATH / "pitwall" / "agents" / "resources" / "config" / "harness-registry.json"
)
VENV_PYTHON_REL_PATH = PACKAGE_REL_PATH / ".venv" / "bin" / "python"

MARKETPLACE_DECLARATIONS: tuple[tuple[str, Path], ...] = (
    ("claude", Path(".claude-plugin/marketplace.json")),
    ("codex", Path(".agents/plugins/marketplace.json")),
    ("copilot", Path(".github/plugin/marketplace.json")),
)

ROLES: tuple[str, ...] = ("orchestrator", "subagent", "misconfigured")
ROLE_OPERATIONS = {
    "orchestrator": "orchestrator-tool",
    "subagent": "subagent-tool",
    "misconfigured": "misconfigured-tool",
}

KIND_PLUGIN = "plugin"
KIND_PROVIDER = "provider"
KIND_MCP = "mcp"

CATEGORY_PLUGIN_MANIFEST = "plugin-manifest"
CATEGORY_PLUGIN_MARKETPLACE = "plugin-marketplace"
CATEGORY_ADAPTER = "adapter-contract"
CATEGORY_MCP = "channel-mcp-tool"

OP_PLUGIN_DECLARATION = "plugin-declaration"
OP_MARKETPLACE_LISTING = "marketplace-listing"
OP_ADAPTER_CONTRACT = "adapter-class-contract"

SEVERITY_WARNING = "warning"
SEVERITY_UNRESOLVED = "unresolved"

ISSUE_UNRESOLVED_PLUGIN_MANIFESTS = "unresolved-plugin-manifests"
ISSUE_UNRESOLVED_PLUGIN_MARKETPLACES = "unresolved-plugin-marketplaces"
ISSUE_UNRESOLVED_PLUGIN_HOST = "unresolved-plugin-host-ownership"
ISSUE_MARKETPLACE_WITHOUT_MANIFEST = "marketplace-entry-without-plugin-manifest"
ISSUE_UNRESOLVED_ADAPTERS = "unresolved-adapter-contracts"
ISSUE_UNRESOLVED_ADAPTER_PROVIDER_ID = "unresolved-adapter-provider-id"
ISSUE_UNRESOLVED_ADAPTER_ATTRIBUTE = "unresolved-adapter-attribute-value"
ISSUE_UNRESOLVED_ADAPTER_REGISTRY = "unresolved-adapter-registry"
ISSUE_DUPLICATE_ADAPTER_PROVIDER_ID = "duplicate-adapter-provider-id"
ISSUE_ADAPTER_BINDING_MISMATCH = "adapter-registry-binding-mismatch"
ISSUE_UNBOUND_ADAPTER_PROVIDER = "unbound-adapter-provider"
ISSUE_UNLISTED_ADAPTER_CLASS = "unlisted-adapter-class"
ISSUE_UNRESOLVED_MCP = "unresolved-channel-mcp-tools"
ISSUE_MCP_ROLE_MISMATCH = "mcp-role-tool-mismatch"
ISSUE_MCP_ROLE_BOUNDARY = "mcp-role-boundary-mismatch"
ISSUE_MCP_MISCONFIGURED_TOOLS = "mcp-misconfigured-role-has-tools"
ISSUE_MCP_DEFINITION_DRIFT = "mcp-tool-definition-drift"
ISSUE_UNRESOLVED_MCP_TOOL_SOURCE = "unresolved-mcp-tool-source"

PROBE_ENV_KEYS = (
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "HOME",
    "TMPDIR",
    "LC_CTYPE",
)
INVALID_DISPATCH_ID = "not-a-dispatch-id"

_PROBE_SCRIPT = textwrap.dedent(
    """
    import io
    import json
    import os
    import sys

    request = json.loads(sys.stdin.read() or "{}")
    state_root = request["state_root"]

    from pitwall.agents import mcp_server, mcp_tools
    from pitwall.agents.channel import CHANNEL_DISPATCH_ENV, CHANNEL_STATE_ROOT_ENV

    expected_module = os.path.realpath(request["expected_module"])
    expected_server = os.path.realpath(request["expected_server_module"])
    actual_module = os.path.realpath(mcp_tools.__file__)
    actual_server = os.path.realpath(mcp_server.__file__)
    if actual_module != expected_module or actual_server != expected_server:
        raise RuntimeError(
            "module origin mismatch before construction: "
            f"mcp_tools={actual_module} mcp_server={actual_server}"
        )

    environments = {
        "orchestrator": {CHANNEL_STATE_ROOT_ENV: state_root},
        "subagent": {
            CHANNEL_STATE_ROOT_ENV: state_root,
            CHANNEL_DISPATCH_ENV: request["subagent_dispatch_id"],
        },
        "misconfigured": {
            CHANNEL_STATE_ROOT_ENV: state_root,
            CHANNEL_DISPATCH_ENV: request["invalid_dispatch_id"],
        },
    }

    roles = {}
    for role, env in environments.items():
        server = mcp_tools.build_server(env, io.BytesIO(b""), io.BytesIO())
        visible = server._visible()
        tools = {}
        for name, entry in visible.items():
            definition = entry[0]
            tools[str(name)] = json.loads(json.dumps(definition, sort_keys=True))
        declared = list(mcp_server.SUBAGENT_TOOLS if role == "subagent" else mcp_server.ORCHESTRATOR_TOOLS)
        declared = declared if role != "misconfigured" else []
        roles[role] = {
            "role": server.role,
            "tools": tools,
            "declared": [str(name) for name in declared],
        }

    payload = {
        "module": os.path.realpath(mcp_tools.__file__),
        "server_module": os.path.realpath(mcp_server.__file__),
        "python": sys.executable,
        "env_keys": sorted(os.environ),
        "state_entries_after": sorted(os.listdir(state_root)),
        "roles": roles,
    }
    sys.stdout.write(json.dumps(payload, sort_keys=True))
    """
)


class DeclarationError(ValueError):
    """Raised when a known declaration file is missing or structurally malformed."""


class McpProbeError(RuntimeError):
    """Raised when the channel MCP probe fails or imports the wrong module origin."""


def discover(root: str | Path) -> list[dict[str, Any]]:
    """Return the deterministic, ``surface_id``-sorted declaration rows for ``root``."""
    return cast(list[dict[str, Any]], discover_with_issues(root)["surfaces"])


def discover_with_issues(root: str | Path) -> dict[str, Any]:
    """Return ``{"surfaces", "issues", "schema_version"}`` for ``root``.

    Malformed known JSON declarations raise :class:`DeclarationError`; missing
    families and unresolved constructs are reported as explicit issues without
    dropping any otherwise valid row.
    """
    root = Path(root)
    surfaces: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []

    manifest_rows = _plugin_manifest_rows(root, issues)
    surfaces.extend(manifest_rows)
    surfaces.extend(_marketplace_rows(root, manifest_rows, issues))
    surfaces.extend(_adapter_rows(root, issues))
    surfaces.extend(_mcp_rows(root, issues))

    surfaces.sort(key=lambda surface: surface["surface_id"])
    issues.sort(key=_issue_sort_key)
    return {"surfaces": surfaces, "issues": issues, "schema_version": ROW_SCHEMA_VERSION}


def probe_channel_tools(
    root: str | Path, *, python_bin: str | Path | None = None, timeout_s: int = 180
) -> dict[str, Any]:
    """Build the channel servers in a fresh subprocess and return the raw probe payload.

    The subprocess runs with a minimal environment containing only
    :data:`PROBE_ENV_KEYS`, a synthetic state directory, and ``PYTHONPATH``
    pointed at the candidate runtime. It imports ``pitwall.agents.mcp_tools``,
    asserts its origin, constructs ``build_server`` for the orchestrator, a
    synthetic UUID subagent, and an invalid-ID role, and reports the visible
    definitions. Handlers are never invoked.
    """
    root = Path(root)
    runtime = (root / RUNTIME_REL_PATH).resolve()
    module_path = runtime / "pitwall" / "agents" / "mcp_tools.py"
    server_path = runtime / "pitwall" / "agents" / "mcp_server.py"
    if not module_path.is_file() or not server_path.is_file():
        raise McpProbeError(
            f"candidate runtime not found: {module_path} and {server_path} are required"
        )
    python = Path(python_bin) if python_bin is not None else _candidate_python(root)
    python = python.resolve()
    if not python.is_file():
        raise McpProbeError(f"candidate python not found: {python}")

    with tempfile.TemporaryDirectory(prefix="routing-contracts-") as scratch:
        state_root = Path(scratch) / "state"
        state_root.mkdir()
        request = json.dumps(
            {
                "state_root": str(state_root),
                "subagent_dispatch_id": str(
                    uuid.uuid5(uuid.NAMESPACE_URL, MCP_TOOLS_REL_PATH.as_posix())
                ),
                "invalid_dispatch_id": INVALID_DISPATCH_ID,
                "expected_module": str(module_path.resolve()),
                "expected_server_module": str(server_path.resolve()),
            }
        )
        child_env = {
            "PYTHONPATH": str(runtime),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "HOME": scratch,
            "TMPDIR": scratch,
            "LC_CTYPE": "C.UTF-8",
        }
        completed = subprocess.run(
            [str(python), "-c", _PROBE_SCRIPT],
            input=request,
            capture_output=True,
            text=True,
            env=child_env,
            cwd=scratch,
            timeout=timeout_s,
        )
        if completed.returncode != 0:
            raise McpProbeError(
                f"channel MCP probe exited {completed.returncode}: {completed.stderr.strip()[-2000:]}"
            )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise McpProbeError(
                f"channel MCP probe produced invalid JSON: {completed.stdout[:500]!r}"
            ) from exc

    _check_module_origin(module_path, payload.get("module"))
    _check_module_origin(server_path, payload.get("server_module"))
    roles = payload.get("roles")
    if not isinstance(roles, dict) or set(roles) != set(ROLES):
        raise McpProbeError(f"channel MCP probe did not report every role: {sorted(payload)}")
    return cast(dict[str, Any], payload)


def _candidate_python(root: Path) -> Path:
    candidate = root / VENV_PYTHON_REL_PATH
    if candidate.is_file():
        return candidate
    return Path(sys.executable)


def _check_module_origin(expected: Path, observed: Any) -> None:
    if not isinstance(observed, str) or not observed:
        raise McpProbeError(f"probe did not report a module origin; expected {expected}")
    resolved = Path(observed).resolve()
    if resolved != Path(expected).resolve():
        raise McpProbeError(
            f"wrong module origin: probe imported {resolved}, expected {Path(expected).resolve()}"
        )


def _plugin_manifest_rows(root: Path, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    plugin_root = root / PLUGIN_ROOTS_REL_PATH
    manifest_paths = (
        sorted(path for path in plugin_root.glob(PLUGIN_MANIFEST_GLOB) if path.is_file())
        if plugin_root.is_dir()
        else []
    )
    if not manifest_paths:
        issues.append(
            _issue(
                ISSUE_UNRESOLVED_PLUGIN_MANIFESTS,
                f"no plugin manifests found under {PLUGIN_ROOTS_REL_PATH.as_posix()}",
                SEVERITY_UNRESOLVED,
                [],
            )
        )
        return []

    host_map = _registry_host_map(root)
    rows: list[dict[str, Any]] = []
    for path in manifest_paths:
        relative = path.relative_to(root).as_posix()
        record = _load_json_declaration(path, root)
        _validate_manifest(record, relative)
        plugin_dir = _manifest_plugin_dir(path.relative_to(root))
        host = host_map.get(plugin_dir)
        host_source = "registry-package-path" if host else "unresolved"
        surface_id = f"plugin:manifest:{relative}"
        if host is None:
            issues.append(
                _issue(
                    ISSUE_UNRESOLVED_PLUGIN_HOST,
                    f"manifest {relative} names plugin {record['name']!r} but no host "
                    f"packagePath in {REGISTRY_REL_PATH.as_posix()} owns {plugin_dir}",
                    SEVERITY_WARNING,
                    [surface_id],
                )
            )
        rows.append(
            {
                "surface_id": surface_id,
                "kind": KIND_PLUGIN,
                "operation": OP_PLUGIN_DECLARATION,
                "source": f"{relative}:1",
                "metadata": {
                    "category": CATEGORY_PLUGIN_MANIFEST,
                    "path": relative,
                    "plugin_name": record["name"],
                    "plugin_dir": plugin_dir,
                    "host": host,
                    "host_source": host_source,
                    "sha256": _file_sha(path),
                    "record_sha256": _record_digest(record),
                    "record": record,
                },
            }
        )
    return rows


def _validate_manifest(record: Any, relative: str) -> None:
    if not isinstance(record, dict):
        raise DeclarationError(f"{relative}: /: must be an object")
    _require_declared_string(record, "name", relative, "/name")
    if "version" in record:
        _require_declared_string(record, "version", relative, "/version")
    if "keywords" in record:
        keywords = record["keywords"]
        if not isinstance(keywords, list) or not all(isinstance(item, str) for item in keywords):
            raise DeclarationError(f"{relative}: /keywords: must be an array of strings")


def _manifest_plugin_dir(relative: Path) -> str:
    parent = relative.parent
    plugin_dir = parent.parent if parent.name.startswith(".") else parent
    return plugin_dir.as_posix()


def _marketplace_rows(
    root: Path, manifest_rows: list[dict[str, Any]], issues: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    declared_paths = [path for _, path in MARKETPLACE_DECLARATIONS if (root / path).is_file()]
    if not declared_paths:
        issues.append(
            _issue(
                ISSUE_UNRESOLVED_PLUGIN_MARKETPLACES,
                "none of the known marketplace declarations exist: "
                + ", ".join(path.as_posix() for _, path in MARKETPLACE_DECLARATIONS),
                SEVERITY_UNRESOLVED,
                [],
            )
        )
        return []

    host_map = _registry_host_map(root)
    manifest_by_dir = {
        row["metadata"]["plugin_dir"]: row for row in manifest_rows if row["metadata"]["plugin_dir"]
    }
    rows: list[dict[str, Any]] = []
    for family, relative_path in MARKETPLACE_DECLARATIONS:
        path = root / relative_path
        if not path.is_file():
            continue
        relative = relative_path.as_posix()
        text = path.read_text(encoding="utf-8")
        record = _load_json_declaration(path, root)
        _validate_marketplace(record, relative)
        listed = record["plugins"]
        for index, entry in enumerate(listed):
            surface_id = None
            source_path = _normalize_source(entry.get("source"))
            if source_path is None:
                raise DeclarationError(
                    f"{relative}: /plugins/{index}/source: must be a relative path within the repository"
                )
            manifest_row = manifest_by_dir.get(source_path)
            if manifest_row is not None and manifest_row["metadata"]["host"]:
                host = manifest_row["metadata"]["host"]
                host_source = "plugin-manifest"
            elif source_path in host_map:
                host = host_map[source_path]
                host_source = "registry-package-path"
            else:
                host = family
                host_source = "marketplace-declaration"
            name = entry["name"]
            surface_id = f"plugin:marketplace:{host}:{name}"
            if manifest_row is None and source_path not in host_map:
                issues.append(
                    _issue(
                        ISSUE_MARKETPLACE_WITHOUT_MANIFEST,
                        f"marketplace entry {name!r} in {relative} points at {source_path!r} "
                        "which has no plugin manifest or registry host",
                        SEVERITY_WARNING,
                        [surface_id],
                    )
                )
            rows.append(
                {
                    "surface_id": surface_id,
                    "kind": KIND_PLUGIN,
                    "operation": OP_MARKETPLACE_LISTING,
                    "source": f"{relative}:{_entry_line(text, name)}",
                    "metadata": {
                        "category": CATEGORY_PLUGIN_MARKETPLACE,
                        "marketplace": record["name"],
                        "marketplace_path": relative,
                        "marketplace_sha256": _file_sha(path),
                        "entry": entry,
                        "entry_sha256": _record_digest(entry),
                        "source_path": source_path,
                        "source_declaration": entry.get("source"),
                        "host": host,
                        "host_source": host_source,
                    },
                }
            )
    return rows


def _validate_marketplace(record: Any, relative: str) -> None:
    if not isinstance(record, dict):
        raise DeclarationError(f"{relative}: /: must be an object")
    _require_declared_string(record, "name", relative, "/name")
    listed = record.get("plugins")
    if not isinstance(listed, list):
        raise DeclarationError(f"{relative}: /plugins: must be an array")
    for index, entry in enumerate(listed):
        pointer = f"/plugins/{index}"
        if not isinstance(entry, dict):
            raise DeclarationError(f"{relative}: {pointer}: must be an object")
        _require_declared_string(entry, "name", relative, f"{pointer}/name")
        if "source" not in entry:
            raise DeclarationError(f"{relative}: {pointer}/source: is required")


def _normalize_source(raw: Any) -> str | None:
    if isinstance(raw, str):
        value = raw
    elif isinstance(raw, dict) and isinstance(raw.get("path"), str):
        value = raw["path"]
    else:
        return None
    value = value.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if not value or value.startswith("/"):
        return None
    parts = [part for part in value.rstrip("/").split("/") if part not in {"", "."}]
    if not parts or any(part == ".." for part in parts):
        return None
    return "/".join(parts)


def _adapter_rows(root: Path, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    adapters_dir = root / ADAPTERS_REL_PATH
    if not adapters_dir.is_dir():
        issues.append(
            _issue(
                ISSUE_UNRESOLVED_ADAPTERS,
                f"no adapter implementations found under {ADAPTERS_REL_PATH.as_posix()}",
                SEVERITY_UNRESOLVED,
                [],
            )
        )
        return []

    base_path = adapters_dir / ADAPTER_BASE_NAME
    if not base_path.is_file():
        issues.append(
            _issue(
                ISSUE_UNRESOLVED_ADAPTERS,
                f"adapter base {ADAPTER_BASE_NAME} is missing from {ADAPTERS_REL_PATH.as_posix()}",
                SEVERITY_UNRESOLVED,
                [],
            )
        )
        return []
    base_tree = _parse_python(base_path, root)
    base_class = _find_class(base_tree, ADAPTER_BASE_CLASS)
    if base_class is None:
        raise DeclarationError(
            f"{base_path.relative_to(root).as_posix()}: class {ADAPTER_BASE_CLASS} not found"
        )
    base_attributes, base_methods = _class_declarations(base_class)
    base_sha = _file_sha(base_path)
    base_relative = base_path.relative_to(root).as_posix()

    registry_path = adapters_dir / "__init__.py"
    binding, binding_issue = _adapter_registry_binding(registry_path, root)
    if binding_issue is not None:
        issues.append(binding_issue)
    binding_label = "static" if binding is not None else "dynamic"

    rows: list[dict[str, Any]] = []
    issues_found: list[dict[str, Any]] = []
    seen: dict[str, str] = {}
    classes: dict[str, str | None] = {}
    for module in sorted(adapters_dir.glob("*.py")):
        if module.name in {"__init__.py", ADAPTER_BASE_NAME}:
            continue
        relative = module.relative_to(root).as_posix()
        tree = _parse_python(module, root)
        module_sha = _file_sha(module)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if not _subclasses_provider_adapter(node):
                continue
            attributes, methods = _class_declarations(node)
            provider_id = _provider_id(attributes, node.name)
            classes[node.name] = provider_id
            if provider_id is None:
                issues_found.append(
                    _issue(
                        ISSUE_UNRESOLVED_ADAPTER_PROVIDER_ID,
                        f"adapter class {node.name} in {relative} has no literal non-empty harness_id",
                        SEVERITY_UNRESOLVED,
                        [],
                    )
                )
                continue
            surface_id = f"routing:adapter:{provider_id}"
            if provider_id in seen:
                issues_found.append(
                    _issue(
                        ISSUE_DUPLICATE_ADAPTER_PROVIDER_ID,
                        f"adapter class {node.name} in {relative} duplicates provider_id "
                        f"{provider_id!r} already declared by {seen[provider_id]}",
                        SEVERITY_WARNING,
                        [surface_id],
                    )
                )
                continue
            seen[provider_id] = f"{relative}:{node.lineno}"
            for attribute, info in attributes.items():
                if not info["resolved"] and attribute != "harness_id":
                    issues_found.append(
                        _issue(
                            ISSUE_UNRESOLVED_ADAPTER_ATTRIBUTE,
                            f"adapter {provider_id} attribute {attribute} in {relative} is not a literal",
                            SEVERITY_WARNING,
                            [surface_id],
                        )
                    )
            rows.append(
                {
                    "surface_id": surface_id,
                    "kind": KIND_PROVIDER,
                    "operation": OP_ADAPTER_CONTRACT,
                    "source": f"{relative}:{node.lineno}",
                    "metadata": {
                        "category": CATEGORY_ADAPTER,
                        "provider_id": provider_id,
                        "class_name": node.name,
                        "module": relative,
                        "bases": [ast.unparse(base) for base in node.bases],
                        "attributes": _effective_attributes(
                            base_attributes, attributes, base_relative, relative
                        ),
                        "methods": _effective_methods(
                            base_methods, methods, base_relative, relative
                        ),
                        "source_sha256": module_sha,
                        "base_source": {
                            "path": base_relative,
                            "sha256": base_sha,
                        },
                        "registry_binding": binding_label,
                        "registry_class": None if binding is None else binding.get(provider_id),
                        "registry_provider_ids": sorted(binding) if binding is not None else None,
                    },
                }
            )

    if binding is not None:
        for provider_id in sorted(binding):
            if provider_id not in seen:
                issues_found.append(
                    _issue(
                        ISSUE_UNBOUND_ADAPTER_PROVIDER,
                        f"{registry_path.relative_to(root).as_posix()} binds {provider_id!r} to "
                        f"{binding[provider_id]} but no adapter class declares that harness_id",
                        SEVERITY_WARNING,
                        [],
                    )
                )
        for provider_id, class_name in sorted(binding.items()):
            declared = classes.get(class_name)
            if declared is not None and declared != provider_id:
                issues_found.append(
                    _issue(
                        ISSUE_ADAPTER_BINDING_MISMATCH,
                        f"{registry_path.relative_to(root).as_posix()} binds {provider_id!r} to "
                        f"{class_name}, but that class declares provider_id {declared!r}",
                        SEVERITY_WARNING,
                        [f"routing:adapter:{declared}"],
                    )
                )
        for class_name in sorted(classes):
            if class_name not in binding.values():
                issues_found.append(
                    _issue(
                        ISSUE_UNLISTED_ADAPTER_CLASS,
                        f"adapter class {class_name} is not listed in the static registry mapping",
                        SEVERITY_WARNING,
                        [],
                    )
                )
    issues.extend(issues_found)
    rows.sort(key=lambda row: row["surface_id"])
    return rows


def _parse_python(path: Path, root: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        raise DeclarationError(
            f"{path.relative_to(root).as_posix()}: cannot parse python at line {exc.lineno}: {exc.msg}"
        ) from exc


def _find_class(tree: ast.Module, name: str) -> ast.ClassDef | None:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _subclasses_provider_adapter(node: ast.ClassDef) -> bool:
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == ADAPTER_BASE_CLASS:
            return True
        if isinstance(base, ast.Attribute) and base.attr == ADAPTER_BASE_CLASS:
            return True
    return False


def _class_declarations(
    node: ast.ClassDef,
) -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    attributes: dict[str, dict[str, Any]] = {}
    methods: dict[str, int] = {}
    for child in node.body:
        if isinstance(child, ast.Assign) and len(child.targets) == 1:
            target = child.targets[0]
            if isinstance(target, ast.Name):
                attributes[target.id] = _literal(child.value, child.lineno)
        elif isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
            if child.value is None:
                attributes[child.target.id] = {
                    "value": None,
                    "lineno": child.lineno,
                    "resolved": False,
                }
            else:
                attributes[child.target.id] = _literal(child.value, child.lineno)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods[child.name] = child.lineno
    return attributes, methods


def _literal(value: ast.expr, lineno: int) -> dict[str, Any]:
    try:
        return {"value": ast.literal_eval(value), "lineno": lineno, "resolved": True}
    except ValueError, SyntaxError, TypeError:
        return {"value": None, "lineno": lineno, "resolved": False}


def _provider_id(attributes: dict[str, dict[str, Any]], class_name: str) -> str | None:
    info = attributes.get("harness_id")
    if info is None or not info["resolved"]:
        return None
    value = info["value"]
    if not isinstance(value, str) or not value:
        return None
    return value


def _effective_attributes(
    base: dict[str, dict[str, Any]],
    declared: dict[str, dict[str, Any]],
    base_relative: str,
    module_relative: str,
) -> dict[str, dict[str, Any]]:
    effective: dict[str, dict[str, Any]] = {}
    for name in sorted(set(base) | set(declared)):
        if name in declared:
            info = declared[name]
            effective[name] = {
                "value": info["value"],
                "declared": True,
                "inherited": False,
                "overrides_base": name in base,
                "source": f"{module_relative}:{info['lineno']}",
            }
        else:
            info = base[name]
            effective[name] = {
                "value": info["value"],
                "declared": False,
                "inherited": True,
                "overrides_base": False,
                "source": f"{base_relative}:{info['lineno']}",
            }
    return effective


def _effective_methods(
    base: dict[str, int],
    declared: dict[str, int],
    base_relative: str,
    module_relative: str,
) -> dict[str, dict[str, Any]]:
    effective: dict[str, dict[str, Any]] = {}
    for name in sorted(set(base) | set(declared)):
        if name in declared:
            effective[name] = {
                "declared": True,
                "inherited": False,
                "overrides_base": name in base,
                "source": f"{module_relative}:{declared[name]}",
            }
        else:
            effective[name] = {
                "declared": False,
                "inherited": True,
                "overrides_base": False,
                "source": f"{base_relative}:{base[name]}",
            }
    return effective


def _adapter_registry_binding(
    path: Path, root: Path
) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    relative = path.relative_to(root).as_posix()
    if not path.is_file():
        return None, _issue(
            ISSUE_UNRESOLVED_ADAPTER_REGISTRY,
            f"adapter registry {relative} is missing",
            SEVERITY_UNRESOLVED,
            [],
        )
    tree = _parse_python(path, root)
    for node in tree.body:
        target_value: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "_ADAPTERS" for target in node.targets
            )
            or (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "_ADAPTERS"
            )
        ):
            target_value = node.value
        if target_value is None:
            continue
        if isinstance(target_value, ast.Dict):
            mapping: dict[str, str] = {}
            for key, value in zip(target_value.keys, target_value.values, strict=True):
                if (
                    isinstance(key, ast.Constant)
                    and isinstance(key.value, str)
                    and isinstance(value, ast.Name)
                ):
                    mapping[key.value] = value.id
                else:
                    return None, _issue(
                        ISSUE_UNRESOLVED_ADAPTER_REGISTRY,
                        f"{relative}: /_ADAPTERS: entry {ast.unparse(key) if key is not None else '**'} -> "
                        f"{ast.unparse(value)} is not a static string-to-class binding",
                        SEVERITY_UNRESOLVED,
                        [],
                    )
            return mapping, None
        return None, _issue(
            ISSUE_UNRESOLVED_ADAPTER_REGISTRY,
            f"{relative}: /_ADAPTERS: value is {type(target_value).__name__}, not a literal dict",
            SEVERITY_UNRESOLVED,
            [],
        )
    return None, _issue(
        ISSUE_UNRESOLVED_ADAPTER_REGISTRY,
        f"{relative}: /_ADAPTERS: assignment not found",
        SEVERITY_UNRESOLVED,
        [],
    )


def _mcp_rows(root: Path, issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools_path = root / MCP_TOOLS_REL_PATH
    server_path = root / MCP_SERVER_REL_PATH
    if not tools_path.is_file() or not server_path.is_file():
        issues.append(
            _issue(
                ISSUE_UNRESOLVED_MCP,
                f"channel MCP sources are missing under {RUNTIME_REL_PATH.as_posix()}",
                SEVERITY_UNRESOLVED,
                [],
            )
        )
        return []

    payload = probe_channel_tools(root)
    tools_relative = tools_path.relative_to(root).as_posix()
    server_relative = server_path.relative_to(root).as_posix()
    tools_sha = _file_sha(tools_path)
    server_sha = _file_sha(server_path)
    source_lines = _tool_source_lines(tools_path, root)
    declaration_sources = _role_declaration_sources(server_path, root)

    roles_payload: dict[str, dict[str, Any]] = payload["roles"]
    definitions: dict[str, Any] = {}
    visibility: dict[str, list[str]] = {}
    declared_in: dict[str, list[str]] = {}
    for role in ROLES:
        entry = roles_payload[role]
        if entry["role"] != role:
            issues.append(
                _issue(
                    ISSUE_MCP_ROLE_BOUNDARY,
                    f"role {role} constructed a server reporting role {entry['role']!r}",
                    SEVERITY_WARNING,
                    [],
                )
            )
        declared = set(entry["declared"])
        visible = set(entry["tools"])
        for name in sorted(declared - visible):
            issues.append(
                _issue(
                    ISSUE_MCP_ROLE_MISMATCH,
                    f"{server_relative} declares tool {name!r} for role {role} but "
                    f"{tools_relative} does not register it for that role",
                    SEVERITY_WARNING,
                    [f"routing:mcp:{role}:{name}"],
                )
            )
        for name in sorted(visible - declared):
            issues.append(
                _issue(
                    ISSUE_MCP_ROLE_MISMATCH,
                    f"{tools_relative} registers tool {name!r} for role {role} but "
                    f"{server_relative} does not declare it for that role",
                    SEVERITY_WARNING,
                    [f"routing:mcp:{role}:{name}"],
                )
            )
        for name, definition in entry["tools"].items():
            if name in definitions and _record_digest(definitions[name]) != _record_digest(
                definition
            ):
                issues.append(
                    _issue(
                        ISSUE_MCP_DEFINITION_DRIFT,
                        f"tool {name!r} is registered with different definitions across roles",
                        SEVERITY_WARNING,
                        [f"routing:mcp:{role}:{name}"],
                    )
                )
            definitions.setdefault(name, definition)
            visibility.setdefault(name, []).append(role)
            if name in declared:
                declared_in.setdefault(name, []).append(role)
        if role == "misconfigured" and visible:
            issues.append(
                _issue(
                    ISSUE_MCP_MISCONFIGURED_TOOLS,
                    f"the misconfigured role registered tools: {', '.join(sorted(visible))}",
                    SEVERITY_WARNING,
                    [f"routing:mcp:{role}:{name}" for name in sorted(visible)],
                )
            )

    rows: list[dict[str, Any]] = []
    for role in ROLES:
        for name in sorted(roles_payload[role]["tools"]):
            definition = roles_payload[role]["tools"][name]
            if name not in source_lines:
                issues.append(
                    _issue(
                        ISSUE_UNRESOLVED_MCP_TOOL_SOURCE,
                        f"no literal tool definition for {name!r} was found in {tools_relative}",
                        SEVERITY_WARNING,
                        [f"routing:mcp:{role}:{name}"],
                    )
                )
            rows.append(
                {
                    "surface_id": f"routing:mcp:{role}:{name}",
                    "kind": KIND_MCP,
                    "operation": ROLE_OPERATIONS[role],
                    "source": f"{tools_relative}:{source_lines.get(name, 1)}",
                    "metadata": {
                        "category": CATEGORY_MCP,
                        "role": role,
                        "name": name,
                        "definition": definition,
                        "definition_sha256": _record_digest(definition),
                        "input_schema_sha256": _record_digest(definition.get("inputSchema", {})),
                        "enum_refs": _collect_enum_refs(definition.get("inputSchema", {})),
                        "roles": sorted(visibility[name]),
                        "declared_in_roles": sorted(declared_in.get(name, [])),
                        "source_sha256": tools_sha,
                        "server_source": f"{server_relative}:{declaration_sources.get(role, 1)}",
                        "server_sha256": server_sha,
                    },
                }
            )
    return rows


def _tool_source_lines(path: Path, root: Path) -> dict[str, int]:
    tree = _parse_python(path, root)
    lines: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value == "name"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                lines.setdefault(value.value, node.lineno)
    return lines


def _role_declaration_sources(path: Path, root: Path) -> dict[str, int]:
    tree = _parse_python(path, root)
    lines: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in {
                "SUBAGENT_TOOLS",
                "ORCHESTRATOR_TOOLS",
            }:
                lines["subagent" if target.id == "SUBAGENT_TOOLS" else "orchestrator"] = node.lineno
    return lines


def _collect_enum_refs(schema: Any) -> dict[str, list[Any]]:
    refs: dict[str, list[Any]] = {}

    def walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("enum"), list):
                refs[path] = sorted(node["enum"], key=repr)
            for key, value in node.items():
                if key == "enum":
                    continue
                if key == "properties" and isinstance(value, dict):
                    for name, sub in value.items():
                        walk(sub, f"{path}.{name}")
                elif key == "items":
                    walk(value, f"{path}.items")
                else:
                    walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    walk(schema, "inputSchema")
    return refs


def _registry_host_map(root: Path) -> dict[str, str]:
    path = root / REGISTRY_REL_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    hosts = data.get("hosts") if isinstance(data, dict) else None
    if not isinstance(hosts, dict):
        return {}
    mapping: dict[str, str] = {}
    for host_id, record in hosts.items():
        if isinstance(record, dict) and isinstance(record.get("packagePath"), str):
            package_path = record["packagePath"].rstrip("/")
            mapping[package_path] = host_id
            mapping[(PACKAGE_REL_PATH / package_path).as_posix()] = host_id
    return mapping


def _load_json_declaration(path: Path, root: Path) -> Any:
    relative = path.relative_to(root).as_posix()
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise DeclarationError(
            f"{relative}: invalid JSON at line {exc.lineno} column {exc.colno}"
        ) from exc


def _require_declared_string(
    record: dict[str, Any], field: str, relative: str, pointer: str
) -> None:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise DeclarationError(f"{relative}: {pointer}: must be a non-empty string")


def _entry_line(text: str, name: str) -> int:
    start = text.find('"plugins"')
    if start < 0:
        start = 0
    match = re.search(r'"name"\s*:\s*"' + re.escape(name) + r'"', text[start:])
    if match is None:
        return 1
    return text.count("\n", 0, start + match.start()) + 1


def _issue(code: str, message: str, severity: str, surface_ids: list[str]) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "severity": severity,
        "surface_ids": list(surface_ids),
    }


def _issue_sort_key(issue: dict[str, Any]) -> tuple[str, str, str]:
    return (issue["code"], ",".join(issue["surface_ids"]), issue["message"])


def _record_digest(record: Any) -> str:
    canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
