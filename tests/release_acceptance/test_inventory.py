"""Tests for the HTTP/MCP release-acceptance discovery inventory.

These tests prove that the inventory is grounded in the actual runtime surfaces
(``pitwall.api.app`` route table including lazy ``_IncludedRouter`` entries and
FastAPI's generated docs routes, plus ``pitwall.mcp.registry.TOOL_REGISTRY``),
that adding or removing a registered surface changes the discovery output, and
that IDs, ordering, source references, and CLI output are deterministic. No test
starts the app lifespan, opens a socket, or touches a database.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import pytest
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from tools.release_acceptance import inventory

PROBE_ROUTE = "/__inventory_probe"
PROBE_TOOL = "pitwall_inventory_probe"
SECRET_SENTINEL = "super-secret-sentinel-value"


class _StubRoute:
    def __init__(self, path: str, methods: set[str], endpoint: object) -> None:
        self.path = path
        self.methods = methods
        self.endpoint = endpoint
        self.name = getattr(endpoint, "__name__", "stub")
        self.include_in_schema = True


class _StubApp:
    def __init__(self, routes: list[object]) -> None:
        self.routes = routes


class _StubMount:
    """A ``Mount``-shaped route whose children are plain stub routes."""

    def __init__(self, path: str, routes: list[object]) -> None:
        self.path = path
        self.routes = routes


class _StubTool:
    def __init__(self, name: str, handler: object) -> None:
        self.name = name
        self.handler = handler
        self.scope = "general"
        self.description = "stub tool"


def _probe_endpoint() -> None:
    return None


class _DigestModelV1(BaseModel):
    value: str


class _DigestModelV2(BaseModel):
    value: str
    extra: int


def _bare_app() -> FastAPI:
    """Return a FastAPI app with no auto-generated docs routes of its own."""
    return FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _stub_modules(
    monkeypatch: pytest.MonkeyPatch, routes: list[object], tools: list[object]
) -> tuple[_StubApp, list[object]]:
    """Stub the two runtime modules with an app-shaped object.

    Any real ``pitwall`` package cached by an earlier real-root test is hidden for
    the duration of the stub test so the origin check sees only the stubs (which
    have no ``__file__``); the stub app's ``routes`` list is the same list
    ``discover`` reads and the test mutates.
    """
    for name in [name for name in sys.modules if name == "pitwall" or name.startswith("pitwall.")]:
        monkeypatch.delitem(sys.modules, name)
    stub_app = _StubApp(routes)
    app_module = types.ModuleType("pitwall.api.app")
    app_module.app = stub_app
    registry_module = types.ModuleType("pitwall.mcp.registry")
    registry_module.TOOL_REGISTRY = tools
    monkeypatch.setitem(sys.modules, "pitwall.api.app", app_module)
    monkeypatch.setitem(sys.modules, "pitwall.mcp.registry", registry_module)
    return stub_app, tools


def _ids(rows: list[dict]) -> list[str]:
    return [row["surface_id"] for row in rows]


def _expand_runtime(routes: object, prefix: str = "") -> list[tuple[object, str]]:
    """Independently expand the runtime route table the way FastAPI dispatches it.

    Kept separate from ``inventory.iter_effective_entries`` so the route-ID test
    compares two implementations rather than restating the same one.
    """
    entries: list[tuple[object, str]] = []
    for route in routes:  # type: ignore[union-attr]  # reason: runtime route list
        route_path = getattr(route, "path", None) or ""
        candidates = getattr(route, "effective_candidates", None)
        if callable(candidates):
            entries.extend(_expand_runtime(candidates(), prefix))
            continue
        child_routes = getattr(route, "routes", None)
        if child_routes is not None:
            entries.extend(_expand_runtime(child_routes, prefix + route_path))
            continue
        entries.append((route, prefix + route_path))
    return entries


def _runtime_http_pairs(root: Path) -> set[tuple[str, str]]:
    inventory._import_root(root)
    app_module = importlib.import_module("pitwall.api.app")
    pairs: set[tuple[str, str]] = set()
    for route, path in _expand_runtime(app_module.app.routes):
        for method in getattr(route, "methods", None) or ():
            pairs.add((method, path))
    return pairs


def _runtime_tool_names(root: Path) -> set[str]:
    inventory._import_root(root)
    registry_module = importlib.import_module("pitwall.mcp.registry")
    return {spec.name for spec in registry_module.TOOL_REGISTRY}


def _load_probe_module(root: Path) -> types.ModuleType:
    path = root / "src" / "probe_mod.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def probe_endpoint():\n    return None\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("probe_mod", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discover_ids_match_actual_runtime_http_routes() -> None:
    root = inventory.ROOT
    discovered = {
        tuple(row["surface_id"].removeprefix("rest:").split(":", 1))
        for row in inventory.discover(root)
        if row["kind"] == "rest"
    }
    assert discovered == _runtime_http_pairs(root)
    assert ("GET", "/docs") in discovered
    assert ("HEAD", "/docs") in discovered
    assert ("GET", "/openapi.json") in discovered


def test_discover_ids_match_actual_runtime_mcp_tools() -> None:
    root = inventory.ROOT
    discovered = {
        row["surface_id"].removeprefix("mcp:")
        for row in inventory.discover(root)
        if row["kind"] == "mcp"
    }
    expected = _runtime_tool_names(root)
    assert discovered == expected
    assert discovered


def test_source_resolution_prefers_repo_relative_paths() -> None:
    rows = inventory.discover(inventory.ROOT)
    assert any(row["source"].startswith("src/pitwall/api/routes/") for row in rows)
    assert any(row["source"].startswith("src/pitwall/mcp/") for row in rows)
    assert not any(row["source"].startswith(".venv/") for row in rows)
    assert not any("site-packages" in row["source"] for row in rows)
    docs_route = next(row for row in rows if row["surface_id"] == "rest:GET:/docs")
    assert docs_route["source"].startswith("fastapi/")
    assert docs_route["source"].endswith(":0") is False


def test_rest_metadata_round_trips_openapi_operation_contract() -> None:
    rows = inventory.discover(inventory.ROOT)
    health = next(row for row in rows if row["surface_id"] == "rest:GET:/health")
    metadata = health["metadata"]
    assert metadata["operation_id"] == "health_health_get"
    assert isinstance(metadata["schema_digest"], str)
    assert metadata["schema_digest"].startswith("sha256:")
    docs = next(row for row in rows if row["surface_id"] == "rest:GET:/docs")
    assert docs["metadata"]["schema_digest"] is None


def test_rest_schema_digest_changes_when_referenced_model_changes() -> None:
    """A model field behind an unchanged ``$ref`` must still move the digest."""

    def probe_v1(payload: _DigestModelV1) -> None:
        return None

    app = _bare_app()
    app.add_api_route("/model", probe_v1, methods=["POST"])
    before = inventory._collect_operations(app)[("POST", "/model")]["components_digest"]

    def probe_v2(payload: _DigestModelV2) -> None:
        return None

    app.routes.clear()
    app.add_api_route("/model", probe_v2, methods=["POST"])
    after = inventory._collect_operations(app)[("POST", "/model")]["components_digest"]
    assert before != after


def test_real_nested_lazy_routers_expand_with_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def leaf() -> None:
        return None

    inner = APIRouter()
    inner.get("/leaf")(leaf)
    middle = APIRouter()
    middle.include_router(inner, prefix="/inner")
    outer = APIRouter()
    outer.include_router(middle, prefix="/outer")
    app = _bare_app()
    app.include_router(outer, prefix="/top")

    _stub_modules(monkeypatch, [*app.routes], [])
    assert _ids(inventory.discover(tmp_path)) == ["rest:GET:/top/outer/inner/leaf"]


def test_real_nested_lazy_route_addition_and_removal_change_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def leaf() -> None:
        return None

    def extra() -> None:
        return None

    inner = APIRouter()
    inner.get("/leaf")(leaf)
    top = APIRouter()
    top.include_router(inner, prefix="/inner")
    app = _bare_app()
    app.include_router(top, prefix="/top")
    _stub_modules(monkeypatch, [*app.routes], [])

    assert _ids(inventory.discover(tmp_path)) == ["rest:GET:/top/inner/leaf"]
    inner.add_api_route("/extra", extra, methods=["POST"])
    assert _ids(inventory.discover(tmp_path)) == [
        "rest:GET:/top/inner/leaf",
        "rest:POST:/top/inner/extra",
    ]
    inner.routes = [route for route in inner.routes if getattr(route, "path", None) != "/extra"]
    inner._routes_version += 1
    assert _ids(inventory.discover(tmp_path)) == ["rest:GET:/top/inner/leaf"]


def test_lazy_router_operation_metadata_is_resolved() -> None:
    def leaf() -> None:
        return None

    router = APIRouter()
    router.get("/leaf", operation_id="leaf_op", tags=["leaf"])(leaf)
    app = _bare_app()
    app.include_router(router, prefix="/lazy")

    operations = inventory._collect_operations(app)
    assert operations[("GET", "/lazy/leaf")]["operation_id"] == "leaf_op"
    assert operations[("GET", "/lazy/leaf")]["tags"] == "leaf"


def test_path_convertor_operations_resolve_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``{name:path}`` route must still find its document operation and digest."""

    def read_object(object_key: str) -> None:
        return None

    app = _bare_app()
    app.add_api_route("/objects/{object_key:path}", read_object, methods=["GET"])
    _stub_modules(monkeypatch, [*app.routes], [])

    row = next(
        row
        for row in inventory.discover(tmp_path)
        if row["surface_id"] == "rest:GET:/objects/{object_key:path}"
    )
    assert row["metadata"]["operation_id"]
    assert row["metadata"]["schema_digest"].startswith("sha256:")


def test_mounted_path_convertor_operations_resolve_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Converter normalization must compose with a mount prefix."""

    def read_object(object_key: str) -> None:
        return None

    subapp = _bare_app()
    subapp.add_api_route("/objects/{object_key:path}", read_object, methods=["GET"])
    top = _bare_app()
    top.mount("/mounted", subapp)
    _stub_modules(monkeypatch, [*top.routes], [])

    row = next(
        row
        for row in inventory.discover(tmp_path)
        if row["surface_id"] == "rest:GET:/mounted/objects/{object_key:path}"
    )
    assert row["metadata"]["operation_id"]
    assert row["metadata"]["schema_digest"].startswith("sha256:")


def test_every_schema_visible_rest_route_has_a_digest() -> None:
    """Only FastAPI's hidden docs routes may lack a contract digest."""
    rows = inventory.discover(inventory.ROOT)
    for row in rows:
        if row["kind"] != "rest" or not row["metadata"]["include_in_schema"]:
            continue
        assert row["metadata"]["schema_digest"] is not None, row["surface_id"]
        assert row["metadata"]["schema_digest"].startswith("sha256:")
        assert row["metadata"]["operation_id"], row["surface_id"]


def test_mcp_metadata_carries_input_schema_digest() -> None:
    rows = inventory.discover(inventory.ROOT)
    audit = next(row for row in rows if row["surface_id"] == "mcp:pitwall_audit_log")
    metadata = audit["metadata"]
    assert metadata["input_schema_digest"].startswith("sha256:")
    assert metadata["input_parameters"] == ["action", "entity_id", "entity_type", "limit"]
    assert all(
        row["metadata"]["input_schema_digest"] != "unavailable"
        for row in rows
        if row["kind"] == "mcp"
    )


def test_mcp_input_schema_change_changes_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def probe_tool(entity_type: str | None = None) -> dict[str, object]:
        return {"entity_type": entity_type}

    _, tools = _stub_modules(monkeypatch, [], [_StubTool(PROBE_TOOL, probe_tool)])
    before = inventory.discover(tmp_path)[0]["metadata"]["input_schema_digest"]

    def probe_tool(  # noqa: F811  # reason: deliberately changed tool schema
        entity_type: str | None = None, action: str | None = None
    ) -> dict[str, object]:
        return {"entity_type": entity_type, "action": action}

    tools[0].handler = probe_tool
    after = inventory.discover(tmp_path)[0]["metadata"]
    assert after["input_schema_digest"] != before
    assert after["input_parameters"] == ["action", "entity_type"]


def test_mcp_unresolvable_handler_schema_raises_with_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_modules(monkeypatch, [], [_StubTool(PROBE_TOOL, object())])
    with pytest.raises(RuntimeError, match="cannot derive MCP input schema"):
        inventory.discover(tmp_path)


def test_real_mounted_subapp_routes_are_recursed_with_path_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def child() -> None:
        return None

    def sibling() -> None:
        return None

    subapp = _bare_app()
    subapp.add_api_route("/child", child, methods=["GET"])
    top = _bare_app()
    top.mount("/mounted", subapp)
    _stub_modules(monkeypatch, [*top.routes], [])

    assert _ids(inventory.discover(tmp_path)) == ["rest:GET:/mounted/child"]
    subapp.add_api_route("/new", sibling, methods=["POST"])
    assert _ids(inventory.discover(tmp_path)) == [
        "rest:GET:/mounted/child",
        "rest:POST:/mounted/new",
    ]
    subapp.routes.pop(1)
    assert _ids(inventory.discover(tmp_path)) == ["rest:GET:/mounted/child"]


def test_mounted_operation_metadata_comes_from_mounted_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def child() -> None:
        return None

    subapp = _bare_app()
    subapp.add_api_route(
        "/child", child, methods=["GET"], operation_id="mounted_child", tags=["sub"]
    )
    top = _bare_app()
    top.mount("/mounted", subapp)
    _stub_modules(monkeypatch, [*top.routes], [])

    row = next(
        row
        for row in inventory.discover(tmp_path)
        if row["surface_id"] == "rest:GET:/mounted/child"
    )
    assert row["metadata"]["operation_id"] == "mounted_child"
    assert row["metadata"]["tags"] == "sub"
    assert row["metadata"]["schema_digest"].startswith("sha256:")


def test_mount_routes_are_recursed_with_path_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_modules(
        monkeypatch,
        [
            _StubMount(
                "/mounted",
                [_StubRoute("/child", {"GET"}, _probe_endpoint)],
            ),
            _StubRoute("/top", {"GET"}, _probe_endpoint),
        ],
        [],
    )
    assert _ids(inventory.discover(tmp_path)) == [
        "rest:GET:/mounted/child",
        "rest:GET:/top",
    ]


def test_foreign_module_origin_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    with pytest.raises(RuntimeError, match="refusing to inventory the wrong candidate"):
        inventory.discover(tmp_path)


def test_cached_pitwall_parent_from_another_root_is_rejected(tmp_path: Path) -> None:
    """A cached ``pitwall`` package from another checkout must not be inventoried."""
    foreign_package = tmp_path / "foreign" / "src" / "pitwall"
    foreign_package.mkdir(parents=True)
    (foreign_package / "__init__.py").write_text("", encoding="utf-8")
    spec = importlib.util.spec_from_file_location(
        "pitwall",
        foreign_package / "__init__.py",
        submodule_search_locations=[str(foreign_package)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.__file__ is not None

    candidate_root = tmp_path / "candidate"
    candidate_package = candidate_root / "src" / "pitwall"
    (candidate_package / "api").mkdir(parents=True)
    (candidate_package / "mcp").mkdir(parents=True)
    (candidate_package / "api" / "app.py").write_text("app = None\n", encoding="utf-8")
    (candidate_package / "mcp" / "registry.py").write_text("TOOL_REGISTRY = []\n", encoding="utf-8")

    previous = sys.modules.get("pitwall")
    sys.modules["pitwall"] = module
    try:
        with pytest.raises(RuntimeError, match="refusing to inventory the wrong candidate"):
            inventory.discover(candidate_root)
    finally:
        if previous is None:
            sys.modules.pop("pitwall", None)
        else:
            sys.modules["pitwall"] = previous


def test_route_addition_and_removal_change_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_app, _ = _stub_modules(monkeypatch, [_StubRoute("/health", {"GET"}, _probe_endpoint)], [])
    assert _ids(inventory.discover(tmp_path)) == ["rest:GET:/health"]

    stub_app.routes.append(_StubRoute(PROBE_ROUTE, {"GET", "POST"}, _probe_endpoint))
    assert _ids(inventory.discover(tmp_path)) == [
        "rest:GET:/__inventory_probe",
        "rest:GET:/health",
        "rest:POST:/__inventory_probe",
    ]

    stub_app.routes.pop(0)
    assert _ids(inventory.discover(tmp_path)) == [
        "rest:GET:/__inventory_probe",
        "rest:POST:/__inventory_probe",
    ]


def test_mcp_tool_addition_and_removal_change_inventory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, tools = _stub_modules(monkeypatch, [], [_StubTool("pitwall_alpha", _probe_endpoint)])
    assert _ids(inventory.discover(tmp_path)) == ["mcp:pitwall_alpha"]

    tools.append(_StubTool(PROBE_TOOL, _probe_endpoint))
    assert _ids(inventory.discover(tmp_path)) == ["mcp:pitwall_alpha", f"mcp:{PROBE_TOOL}"]

    tools.pop(0)
    assert _ids(inventory.discover(tmp_path)) == [f"mcp:{PROBE_TOOL}"]


def test_source_is_root_relative_and_line_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_probe_module(tmp_path)
    _stub_modules(
        monkeypatch,
        [_StubRoute("/probe", {"GET"}, module.probe_endpoint)],
        [_StubTool(PROBE_TOOL, module.probe_endpoint)],
    )
    sources = {row["surface_id"]: row["source"] for row in inventory.discover(tmp_path)}
    assert sources["rest:GET:/probe"] == "src/probe_mod.py:1"
    assert sources[f"mcp:{PROBE_TOOL}"] == "src/probe_mod.py:1"


def test_output_is_deterministic_and_ordering_is_stable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_modules(
        monkeypatch,
        [
            _StubRoute("/b", {"POST"}, _probe_endpoint),
            _StubRoute("/a", {"GET"}, _probe_endpoint),
        ],
        [_StubTool("pitwall_zeta", _probe_endpoint), _StubTool("pitwall_alpha", _probe_endpoint)],
    )
    first = json.dumps(inventory.build_report(tmp_path), sort_keys=True)
    second = json.dumps(inventory.build_report(tmp_path), sort_keys=True)
    assert first == second
    assert _ids(inventory.discover(tmp_path)) == [
        "mcp:pitwall_alpha",
        "mcp:pitwall_zeta",
        "rest:GET:/a",
        "rest:POST:/b",
    ]


def test_discovered_surface_ids_are_unique_and_well_formed() -> None:
    rows = inventory.discover(inventory.ROOT)
    ids = _ids(rows)
    assert len(ids) == len(set(ids))
    for row in rows:
        assert row["kind"] in {"rest", "mcp"}
        assert row["operation"]
        if row["kind"] == "rest":
            assert (
                row["surface_id"] == f"rest:{row['operation']}:{row['surface_id'].split(':', 2)[2]}"
            )
            assert row["operation"] == row["surface_id"].split(":")[1]
        else:
            assert row["operation"] == "tool"
            assert row["surface_id"].startswith("mcp:")


def test_cli_writes_schema_surfaces_and_deferred_issues(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "inventory.json"
    assert inventory.main(["--root", str(inventory.ROOT), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == inventory.SCHEMA_VERSION
    assert report["surfaces"] == inventory.discover(inventory.ROOT)
    assert {issue["domain"] for issue in report["issues"]} == {
        "cli",
        "tui",
        "gateway",
        "routing",
        "config",
        "ops",
    }
    assert all(issue["status"] == "deferred" for issue in report["issues"])


def test_cli_stdout_matches_the_written_document(capsys: pytest.CaptureFixture[str]) -> None:
    assert inventory.main(["--root", str(inventory.ROOT)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["surfaces"] == inventory.discover(inventory.ROOT)


def test_placeholder_env_never_overwrites_operator_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RUNPOD_API_KEY", SECRET_SENTINEL)
    monkeypatch.setenv("DATABASE_URL", SECRET_SENTINEL)
    monkeypatch.setenv("REDIS_URL", SECRET_SENTINEL)
    payload = json.dumps(inventory.build_report(inventory.ROOT))
    assert os.environ["RUNPOD_API_KEY"] == SECRET_SENTINEL
    assert SECRET_SENTINEL not in payload
