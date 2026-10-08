"""Discovery inventory for the broker's actual HTTP and MCP surfaces.

Schema ``release-acceptance-inventory.v1``
=========================================

``discover(root)`` returns a deterministic, sorted ``list[dict[str, Any]]``. Every row has
``surface_id`` (``rest:METHOD:PATH`` or ``mcp:NAME``), ``kind`` (``rest`` or
``mcp``), ``operation`` (HTTP method, or ``tool``), ``source`` (``path:line``
module reference), and ``metadata``.

The HTTP rows come from the imported ``pitwall.api.app`` route table, exactly as
FastAPI assembles it: lazy ``_IncludedRouter.effective_candidates`` entries are
expanded (FastAPI 0.139+), nested ``Mount`` routes are recursed with their path
prefixes, and the auto-generated ``/docs`` and ``/openapi.json`` HEAD/GET routes
are included. Each REST row carries its OpenAPI ``operationId``, tags, and a
canonical operation-schema digest so contract drift is detectable. The MCP rows
come from ``TOOL_REGISTRY`` at runtime, with each tool's input-schema digest, so
adding, removing, or reshaping a registered tool changes the inventory.

Discovery refuses to report a candidate from the wrong checkout: if an already
imported ``pitwall`` module resolves outside ``root/src``, it raises instead of
silently inventorying another tree. That check runs both before and after the
import so a cached parent package or submodule cannot contaminate the candidate.

Environment placeholders (``RUNPOD_API_KEY``, ``DATABASE_URL``, ``REDIS_URL``)
are set only when unset, before the import, because the app refuses to import
without them. They are non-secret placeholders; existing operator values are
never read, echoed, or overwritten. No lifespan runs, no network or database
call is made, and importing or discovering never mutates process state beyond
those placeholders.

The CLI writes ``schema_version``, ``surfaces``, and ``issues`` as JSON.
``issues`` records the inventories that this module deliberately does not
perform: CLI, TUI, gateway, routing, config, and ops.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import inspect
import json
import os
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "release-acceptance-inventory.v1"

_ENV_PLACEHOLDERS = {
    "RUNPOD_API_KEY": "release-acceptance-placeholder",
    "DATABASE_URL": "postgresql://placeholder:placeholder@127.0.0.1:1/placeholder",
    "REDIS_URL": "redis://127.0.0.1:1/0",
}

_SCHEMA_DIGEST_PREFIX = "sha256:"
_DIGESTED_KEYS = ("description", "parameters", "requestBody", "responses")
_PATH_CONVERTOR = re.compile(r"\{(?P<name>[^{}:]+):[^{}]+\}")

_DEFERRED_DOMAINS = {
    "cli": "CLI command inventory",
    "tui": "TUI screen inventory",
    "gateway": "gateway/provider inventory",
    "routing": "routing-policy inventory",
    "config": "config-surface inventory",
    "ops": "ops/runbook inventory",
}

_DEFERRED_ISSUES = tuple(
    {
        "domain": domain,
        "status": "deferred",
        "reason": f"{label} is not discovered by this HTTP/MCP unit",
    }
    for domain, label in _DEFERRED_DOMAINS.items()
)


def _schema_digest(schema: object) -> str:
    """Return a stable ``sha256:…`` digest for a JSON-serializable contract."""
    try:
        canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError, ValueError:
        canonical = repr(schema)
    return _SCHEMA_DIGEST_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _import_root(root: Path) -> None:
    """Make ``root/src`` importable first and set placeholder env only if unset."""
    root_src = str(root / "src")
    if root_src not in sys.path:
        sys.path.insert(0, root_src)
    for name, value in _ENV_PLACEHOLDERS.items():
        os.environ.setdefault(name, value)


def _assert_module_origin(root: Path) -> None:
    """Refuse to inventory surfaces whose modules resolve outside ``root/src``.

    Every currently loaded ``pitwall`` or ``pitwall.*`` module that has a
    ``__file__`` must live under ``root/src``; a cached parent package or
    submodule from another checkout would otherwise supply routes and tools
    silently. Modules without a ``__file__`` (test stubs) are skipped.
    """
    root_src = (root / "src").resolve()
    for name, module in list(sys.modules.items()):
        if name != "pitwall" and not name.startswith("pitwall."):
            continue
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        module_path = Path(origin).resolve()
        if not module_path.is_relative_to(root_src):
            raise RuntimeError(
                f"already-imported module {name!r} resolves outside the requested root "
                f"({module_path} is not under {root_src}); refusing to inventory the wrong candidate"
            )


def _source(root: Path, obj: Any) -> str:
    """Return a deterministic ``path:line`` reference for ``obj``.

    Repository files are relative to ``root`` (``src/pitwall/...:12``). Files
    outside the root, such as FastAPI's generated ``/docs`` handlers, are made
    portable by stripping the interpreter-specific prefix up to and including
    ``site-packages``; anything else falls back to ``external/<name>:<line>``.
    """
    try:
        file_path = inspect.getsourcefile(obj) or inspect.getfile(obj)
        _, line = inspect.getsourcelines(obj)
    except OSError, TypeError:
        return "unavailable:0"
    path = Path(file_path).resolve()
    for marker in ("site-packages", "dist-packages"):
        if marker in path.parts:
            index = len(path.parts) - 1 - path.parts[::-1].index(marker)
            return f"{Path(*path.parts[index + 1 :]).as_posix()}:{line}"
    try:
        return f"{path.relative_to(root).as_posix()}:{line}"
    except ValueError:
        return f"external/{path.name}:{line}"


def iter_effective_entries(routes: Iterable[object], prefix: str = "") -> list[tuple[object, str]]:
    """Expand flattened routes, lazy routers, and nested mounts into (route, path).

    ``_IncludedRouter.effective_candidates`` yields the concrete routes of a lazily
    included router, and those candidates may themselves be lazy routers or
    ``Mount``s, so candidates are expanded recursively rather than appended as
    opaque entries. ``Mount``-style routes are recursed with their path prefixes so
    no mounted surface is silently omitted.
    """
    entries: list[tuple[object, str]] = []
    for route in routes:
        route_path = getattr(route, "path", None) or ""
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            entries.extend(iter_effective_entries(candidates(), prefix))
            continue
        child_routes = getattr(route, "routes", None)
        if child_routes is not None:
            entries.extend(iter_effective_entries(child_routes, prefix + route_path))
            continue
        entries.append((route, prefix + route_path))
    return entries


def _openapi_document(app: object) -> dict[str, Any]:
    """Return the app's generated OpenAPI document, or fail discovery explicitly.

    An exception here means route contracts cannot be compared, so it is surfaced
    rather than silently degrading acceptance metadata.
    """
    from fastapi.openapi.utils import get_openapi

    try:
        schema = get_openapi(
            title=getattr(app, "title", ""),
            version=getattr(app, "version", ""),
            routes=getattr(app, "routes", []),
        )
    except Exception as exc:  # reason: fail discovery explicitly, never silently skip
        raise RuntimeError(f"cannot generate OpenAPI for discovery: {exc!r}") from exc
    if not isinstance(schema, dict):
        raise RuntimeError("cannot generate OpenAPI for discovery: unexpected document type")
    return schema


def _normalize_operation_path(path: str) -> str:
    """Return ``path`` with Starlette path convertors stripped (``{x:path}`` -> ``{x}``).

    OpenAPI documents key operations by FastAPI's ``path_format`` while the runtime
    route context exposes the compiled path with convertors, so both sides are
    normalized before lookup.
    """
    return _PATH_CONVERTOR.sub(r"{\g<name>}", path)


def _operation_digests(
    schema: dict[str, Any], prefix: str = ""
) -> dict[tuple[str, str], dict[str, str]]:
    """Return a digest per operation and one shared digest for ``components``.

    The components digest is folded into every operation digest so a referenced
    model-field change is detected even though ``$ref`` strings never change.
    ``prefix`` is prepended to each operation path so a mounted sub-application's
    own document keys line up with the mount-prefixed route path.
    """
    paths = schema.get("paths")
    operations: dict[tuple[str, str], dict[str, str]] = {}
    if not isinstance(paths, dict):
        return operations
    components_digest = _schema_digest(schema.get("components") or {})
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            body = {key: operation.get(key) for key in _DIGESTED_KEYS}
            key = (method.upper(), prefix + _normalize_operation_path(path))
            operations[key] = {
                "operation_id": str(operation.get("operationId") or ""),
                "tags": ", ".join(str(tag) for tag in operation.get("tags") or []),
                "operation_digest": _schema_digest(body),
                "components_digest": components_digest,
            }
    return operations


def _collect_operations(app: object) -> dict[tuple[str, str], dict[str, str]]:
    """Return digest-keyed operations for ``app``, including mounted sub-apps.

    The top-level OpenAPI document omits routes served by mounted sub-applications,
    so each mounted sub-app contributes its own generated document with its mount
    prefix folded into the operation keys and its own ``components`` digest folded
    into each operation digest. A mounted ASGI app without a ``routes`` table has
    no OpenAPI surface and is skipped; a declared app whose schema cannot be
    generated raises from ``_openapi_document`` rather than degrading silently.
    """
    operations = _operation_digests(_openapi_document(app), prefix="")
    for route in getattr(app, "routes", []):
        app_mount = getattr(route, "app", None)
        if app_mount is None or not hasattr(app_mount, "routes"):
            continue
        mount_path = getattr(route, "path", None) or ""
        mounted_operations = _collect_operations(app_mount)
        for (method, path), metadata in mounted_operations.items():
            operations[(method, mount_path + path)] = metadata
    return operations


def _rest_rows(
    root: Path,
    route: object,
    path: str,
    operations: dict[tuple[str, str], dict[str, Any]],
) -> list[dict[str, object]]:
    methods = getattr(route, "methods", None)
    if not methods or not path:
        return []
    endpoint = getattr(route, "endpoint", None) or route
    source = _source(root, endpoint)
    rows: list[dict[str, object]] = []
    for method in sorted(methods):
        operation = operations.get((method, _normalize_operation_path(path)))
        if operation is None:
            operation_id: str | None = None
            tags: str | None = None
            schema_digest: str | None = None
        else:
            operation_id = str(operation.get("operation_id") or "")
            tags = str(operation.get("tags") or "")
            schema_digest = _schema_digest(operation)
        metadata: dict[str, object] = {
            "name": getattr(route, "name", "") or "",
            "endpoint": getattr(getattr(route, "endpoint", None), "__name__", ""),
            "include_in_schema": bool(getattr(route, "include_in_schema", True)),
            "operation_id": operation_id,
            "tags": tags,
            "schema_digest": schema_digest,
        }
        rows.append(
            {
                "surface_id": f"rest:{method}:{path}",
                "kind": "rest",
                "operation": method,
                "source": source,
                "metadata": metadata,
            }
        )
    return rows


def _mcp_schema_metadata(handler: object) -> dict[str, object]:
    """Return the tool's MCPServer input-schema digest and parameter names.

    The registry name is authoritative, but a handler whose schema cannot be
    derived means MCP contract drift would go undetected, so the failure is
    surfaced with the handler identity instead of degrading to ``unavailable``.
    """
    from mcp.server.mcpserver.tools import Tool

    identity = (
        f"{getattr(handler, '__module__', '?')}.{getattr(handler, '__qualname__', handler)!r}"
    )
    try:
        if not callable(handler):
            raise TypeError("MCP handler is not callable")
        schema = Tool.from_function(handler).parameters
    except Exception as exc:  # reason: schema metadata is required, never silently unavailable
        raise RuntimeError(
            f"cannot derive MCP input schema for handler {identity}: {exc!r}"
        ) from exc
    if not isinstance(schema, dict):
        raise RuntimeError(
            f"cannot derive MCP input schema for handler {identity}: unexpected schema type "
            f"{type(schema).__name__}"
        )
    properties = schema.get("properties")
    names = sorted(properties) if isinstance(properties, dict) else []
    return {"input_schema_digest": _schema_digest(schema), "input_parameters": names}


def _mcp_rows(root: Path, registry: Iterable[object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for spec in registry:
        name = getattr(spec, "name", None)
        if not isinstance(name, str) or not name:
            continue
        handler = getattr(spec, "handler", None) or spec
        metadata: dict[str, object] = {
            "scope": getattr(spec, "scope", ""),
            "description": getattr(spec, "description", ""),
            "handler": getattr(getattr(spec, "handler", None), "__name__", ""),
        }
        metadata.update(_mcp_schema_metadata(handler))
        rows.append(
            {
                "surface_id": f"mcp:{name}",
                "kind": "mcp",
                "operation": "tool",
                "source": _source(root, handler),
                "metadata": metadata,
            }
        )
    return rows


def discover(root: Path) -> list[dict[str, Any]]:
    """Discover the actual broker HTTP and MCP surfaces without starting anything."""
    root = Path(root).resolve()
    _import_root(root)
    _assert_module_origin(root)
    app_module = importlib.import_module("pitwall.api.app")
    registry_module = importlib.import_module("pitwall.mcp.registry")
    _assert_module_origin(root)

    operations = _collect_operations(app_module.app)
    surfaces: list[dict[str, Any]] = []
    for route, path in iter_effective_entries(app_module.app.routes):
        surfaces.extend(_rest_rows(root, route, path, operations))
    surfaces.extend(_mcp_rows(root, registry_module.TOOL_REGISTRY))
    surfaces.sort(key=lambda row: (row["kind"], row["surface_id"]))
    return surfaces


def build_report(root: Path) -> dict[str, Any]:
    """Return the JSON-serializable inventory report for ``root``."""
    surfaces = discover(root)
    return {
        "schema_version": SCHEMA_VERSION,
        "surfaces": surfaces,
        "issues": [dict(issue) for issue in _DEFERRED_ISSUES],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="inventory",
        description="Discover the broker's actual HTTP and MCP surfaces (no live calls).",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root to discover.")
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path.")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    report = build_report(args.root)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
