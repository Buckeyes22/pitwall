"""Tests for :mod:`tools.release_acceptance.routing_contracts`.

Expected declarations are derived independently from the real plugin manifest
glob, the real marketplace JSON, the real provider registry, and an
independent subprocess probe of the channel MCP server, never hardcoded. The
tests also prove the negative guarantees: adapters are read by AST and are
never imported, MCP handlers are never called, the probe runs free of ambient
secrets and state, and the rows never duplicate the earlier registry
provider/host or shim rows.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import routing_contracts as rc
from tools.release_acceptance import routing_inventory

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / rc.PLUGIN_ROOTS_REL_PATH
RUNTIME_ROOT = REPO_ROOT / rc.RUNTIME_REL_PATH
MCP_TOOLS_PATH = REPO_ROOT / rc.MCP_TOOLS_REL_PATH
MCP_SERVER_PATH = REPO_ROOT / rc.MCP_SERVER_REL_PATH
MAILBOX_PATH = RUNTIME_ROOT / "pitwall" / "agents" / "mailbox.py"
ADAPTER_BASE_PATH = RUNTIME_ROOT / "pitwall" / "agents" / "harnesses" / rc.ADAPTER_BASE_NAME
COPY_IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc")

TOOL_DEFINITION_LINE = "tools/release_acceptance/routing_contracts.py"


@pytest.fixture(scope="module")
def real_report() -> dict[str, Any]:
    return rc.discover_with_issues(REPO_ROOT)


@pytest.fixture(scope="module")
def real_probe() -> dict[str, Any]:
    return rc.probe_channel_tools(REPO_ROOT)


def _row(report: dict[str, Any], surface_id: str) -> dict[str, Any]:
    for surface in report["surfaces"]:
        if surface["surface_id"] == surface_id:
            return surface
    raise AssertionError(f"missing surface {surface_id!r}")


def _digest(value: Any) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _registry_data() -> dict[str, Any]:
    return _load_json(REPO_ROOT / rc.REGISTRY_REL_PATH)


def _registry_provider_ids() -> list[str]:
    return sorted(_registry_data()["harnesses"])


def _registry_hosts() -> dict[str, str]:
    return {
        (rc.PACKAGE_REL_PATH / record["packagePath"].rstrip("/")).as_posix(): host_id
        for host_id, record in _registry_data()["hosts"].items()
    }


def _real_manifest_paths() -> list[Path]:
    return sorted(path for path in PLUGIN_ROOT.glob(rc.PLUGIN_MANIFEST_GLOB) if path.is_file())


def _manifest_plugin_dir(relative: str) -> str:
    parent = Path(relative).parent
    plugin_dir = parent.parent if parent.name.startswith(".") else parent
    return plugin_dir.as_posix()


def _marketplace_entries() -> list[tuple[str, Path, dict[str, Any]]]:
    entries: list[tuple[str, Path, dict[str, Any]]] = []
    for family, relative in rc.MARKETPLACE_DECLARATIONS:
        data = _load_json(REPO_ROOT / relative)
        for entry in data["plugins"]:
            entries.append((family, relative, entry))
    return entries


def _normalize_source(raw: Any) -> str:
    value = raw if isinstance(raw, str) else raw["path"]
    value = value.strip().replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return "/".join(part for part in value.rstrip("/").split("/") if part not in {"", "."})


def _copy_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(PLUGIN_ROOT, root / rc.PLUGIN_ROOTS_REL_PATH, ignore=COPY_IGNORE)
    for _, relative in rc.MARKETPLACE_DECLARATIONS:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    shutil.copytree(
        RUNTIME_ROOT / "pitwall" / "agents",
        root / rc.RUNTIME_REL_PATH / "pitwall" / "agents",
        ignore=COPY_IGNORE,
    )
    shutil.copy2(
        RUNTIME_ROOT / "pitwall" / "__init__.py",
        root / rc.RUNTIME_REL_PATH / "pitwall" / "__init__.py",
    )
    return root


def _assigned_value(path: Path, name: str) -> ast.expr:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            return node.value
    raise AssertionError(f"assignment {name!r} not found in {path}")


def _frozenset_constant(path: Path, name: str) -> set[Any]:
    value = _assigned_value(path, name)
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
        return set(ast.literal_eval(value.args[0]))
    return set(ast.literal_eval(value))


def _tuple_constant(path: Path, name: str) -> tuple[Any, ...]:
    return tuple(ast.literal_eval(_assigned_value(path, name)))


def _tool_definition_line(path: Path, name: str) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values, strict=True):
            if (
                isinstance(key, ast.Constant)
                and key.value == "name"
                and isinstance(value, ast.Constant)
                and value.value == name
            ):
                return node.lineno
    raise AssertionError(f"tool definition {name!r} not found in {path}")


def _role_declaration_line(path: Path, role: str) -> int:
    name = "SUBAGENT_TOOLS" if role == "subagent" else "ORCHESTRATOR_TOOLS"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.lineno
    raise AssertionError(f"role declaration {name} not found in {path}")


def _collect_enum_lists(node: Any) -> list[list[Any]]:
    found: list[list[Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list):
            found.append(node["enum"])
        for value in node.values():
            found.extend(_collect_enum_lists(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_collect_enum_lists(item))
    return found


def test_discover_covers_every_real_declaration(
    real_report: dict[str, Any], real_probe: dict[str, Any]
) -> None:
    ids = {row["surface_id"] for row in real_report["surfaces"]}

    expected_manifests = {
        f"plugin:manifest:{path.relative_to(REPO_ROOT).as_posix()}"
        for path in _real_manifest_paths()
    }
    assert {sid for sid in ids if sid.startswith("plugin:manifest:")} == expected_manifests

    hosts = _registry_hosts()
    expected_marketplaces = set()
    for family, _, entry in _marketplace_entries():
        source = _normalize_source(entry["source"])
        expected_marketplaces.add(f"plugin:marketplace:{hosts.get(source, family)}:{entry['name']}")
    assert {sid for sid in ids if sid.startswith("plugin:marketplace:")} == expected_marketplaces

    expected_adapters = {f"routing:adapter:{pid}" for pid in _registry_provider_ids()}
    assert {sid for sid in ids if sid.startswith("routing:adapter:")} == expected_adapters

    expected_mcp = {
        f"routing:mcp:{role}:{name}"
        for role, payload in real_probe["roles"].items()
        for name in payload["tools"]
    }
    assert {sid for sid in ids if sid.startswith("routing:mcp:")} == expected_mcp

    assert real_report["schema_version"] == rc.ROW_SCHEMA_VERSION
    assert real_report["issues"] == []


def test_manifest_rows_preserve_record_path_host_and_digests(real_report: dict[str, Any]) -> None:
    registry_hosts = _registry_hosts()
    for path in _real_manifest_paths():
        relative = path.relative_to(REPO_ROOT).as_posix()
        row = _row(real_report, f"plugin:manifest:{relative}")
        record = _load_json(path)
        plugin_dir = _manifest_plugin_dir(relative)

        assert row["kind"] == "plugin"
        assert row["metadata"]["category"] == "plugin-manifest"
        assert row["operation"] == "plugin-declaration"
        assert row["source"] == f"{relative}:1"
        assert row["metadata"]["record"] == record
        assert row["metadata"]["record_sha256"] == _digest(record)
        assert row["metadata"]["sha256"] == _file_digest(path)
        assert row["metadata"]["path"] == relative
        assert row["metadata"]["plugin_name"] == record["name"]
        assert row["metadata"]["plugin_dir"] == plugin_dir
        assert row["metadata"]["host"] == registry_hosts[plugin_dir]
        assert row["metadata"]["host_source"] == "registry-package-path"


def test_marketplace_rows_preserve_every_listing_and_ownership(real_report: dict[str, Any]) -> None:
    registry_hosts = _registry_hosts()
    for _, relative, entry in _marketplace_entries():
        path = REPO_ROOT / relative
        record = _load_json(path)
        source = _normalize_source(entry["source"])
        host = registry_hosts[source]
        row = _row(real_report, f"plugin:marketplace:{host}:{entry['name']}")

        assert row["kind"] == "plugin"
        assert row["metadata"]["category"] == "plugin-marketplace"
        assert row["operation"] == "marketplace-listing"
        assert row["source"].startswith(f"{relative.as_posix()}:")
        assert row["metadata"]["marketplace"] == record["name"]
        assert row["metadata"]["marketplace_path"] == relative.as_posix()
        assert row["metadata"]["marketplace_sha256"] == _file_digest(path)
        assert row["metadata"]["entry"] == entry
        assert row["metadata"]["entry_sha256"] == _digest(entry)
        assert row["metadata"]["source_declaration"] == entry["source"]
        assert row["metadata"]["source_path"] == source
        assert row["metadata"]["host"] == host
        assert row["metadata"]["host_source"] == "plugin-manifest"


def test_adapter_rows_capture_declared_contracts_and_base_defaults(
    real_report: dict[str, Any],
) -> None:
    provider_ids = _registry_provider_ids()
    rows = [row for row in real_report["surfaces"] if row["kind"] == "provider"]
    assert {row["surface_id"] for row in rows} == {f"routing:adapter:{pid}" for pid in provider_ids}
    base_sha = _file_digest(ADAPTER_BASE_PATH)

    for row in rows:
        meta = row["metadata"]
        assert meta["category"] == "adapter-contract"
        assert row["operation"] == "adapter-class-contract"
        assert meta["provider_id"] == row["surface_id"].rsplit(":", 1)[1]
        assert meta["attributes"]["harness_id"]["value"] == meta["provider_id"]
        assert meta["attributes"]["harness_id"]["declared"] is True
        assert meta["registry_binding"] == "static"
        assert meta["registry_class"] == meta["class_name"]
        assert meta["registry_provider_ids"] == provider_ids
        assert meta["source_sha256"] == _file_digest(REPO_ROOT / meta["module"])
        assert (
            meta["base_source"]["path"]
            == rc.ADAPTERS_REL_PATH.joinpath(rc.ADAPTER_BASE_NAME).as_posix()
        )
        assert meta["base_source"]["sha256"] == base_sha
        assert any("HarnessAdapter" in base for base in meta["bases"])
        for field in ("prompt_delivery", "missing_binary_ledger", "effort_values"):
            info = meta["attributes"][field]
            assert info["source"].split(":")[0] in {meta["module"], meta["base_source"]["path"]}
        for method in ("usage", "parse", "resolve_binary", "missing_binary_message", "prepare"):
            assert method in meta["methods"]
        assert all(
            set(info) == {"declared", "inherited", "overrides_base", "source"}
            for info in meta["methods"].values()
        )
        assert all(
            set(info) == {"value", "declared", "inherited", "overrides_base", "source"}
            for info in meta["attributes"].values()
        )

    claude = _row(real_report, "routing:adapter:claude")["metadata"]
    assert claude["attributes"]["endpoint_delivery"]["inherited"] is True
    assert claude["attributes"]["endpoint_delivery"]["value"] == "none"
    assert claude["attributes"]["endpoint_delivery"]["source"].startswith(
        claude["base_source"]["path"] + ":"
    )
    codex = _row(real_report, "routing:adapter:codex")["metadata"]
    assert codex["attributes"]["prompt_delivery"]["declared"] is True
    assert codex["attributes"]["prompt_delivery"]["overrides_base"] is True
    assert codex["methods"]["prepare"]["declared"] is True
    assert codex["methods"]["preflight"]["declared"] is False


def test_mcp_rows_capture_full_schemas_role_boundaries_and_enum_refs(
    real_report: dict[str, Any], real_probe: dict[str, Any]
) -> None:
    rows = {row["surface_id"]: row for row in real_report["surfaces"] if row["kind"] == "mcp"}
    subagent_tools = set(_tuple_constant(MCP_SERVER_PATH, "SUBAGENT_TOOLS"))
    orchestrator_tools = set(_tuple_constant(MCP_SERVER_PATH, "ORCHESTRATOR_TOOLS"))
    tools_rel = rc.MCP_TOOLS_REL_PATH.as_posix()
    server_rel = rc.MCP_SERVER_REL_PATH.as_posix()
    tools_sha = _file_digest(MCP_TOOLS_PATH)
    server_sha = _file_digest(MCP_SERVER_PATH)

    for role, payload in real_probe["roles"].items():
        assert payload["role"] == role
        declared = subagent_tools if role == "subagent" else orchestrator_tools
        expected = declared if role != "misconfigured" else set()
        assert set(payload["declared"]) == expected
        assert set(payload["tools"]) == expected
        for name, definition in payload["tools"].items():
            row = rows[f"routing:mcp:{role}:{name}"]
            meta = row["metadata"]
            assert meta["definition"] == definition
            assert meta["definition_sha256"] == _digest(definition)
            assert meta["input_schema_sha256"] == _digest(definition["inputSchema"])
            enums_in_schema = _collect_enum_lists(definition["inputSchema"])
            assert len(meta["enum_refs"]) == len(enums_in_schema)
            assert sorted(
                json.dumps(sorted(values, key=repr)) for values in enums_in_schema
            ) == sorted(json.dumps(values) for values in meta["enum_refs"].values())
            assert meta["roles"] == sorted(
                other
                for other, other_payload in real_probe["roles"].items()
                if name in other_payload["tools"]
            )
            assert meta["declared_in_roles"] == sorted(
                other
                for other, other_payload in real_probe["roles"].items()
                if name in other_payload["declared"]
            )
            assert row["source"] == f"{tools_rel}:{_tool_definition_line(MCP_TOOLS_PATH, name)}"
            assert meta["source_sha256"] == tools_sha
            assert (
                meta["server_source"]
                == f"{server_rel}:{_role_declaration_line(MCP_SERVER_PATH, role)}"
            )
            assert meta["server_sha256"] == server_sha

    ask = rows["routing:mcp:subagent:ask_orchestrator"]
    assert ask["operation"] == "subagent-tool"
    schema = ask["metadata"]["definition"]["inputSchema"]
    assert set(schema["properties"]["blocked_on"]["enum"]) == _frozenset_constant(
        MAILBOX_PATH, "BLOCKED_ON"
    )
    assert set(schema["properties"]["severity"]["enum"]) == _frozenset_constant(
        MAILBOX_PATH, "SEVERITIES"
    )
    assert (
        schema["properties"]["options"]["maxItems"]
        == _assigned_value(MAILBOX_PATH, "MAX_OPTIONS").value
    )
    assert (
        schema["properties"]["deadline_s"]["maximum"]
        == _assigned_value(MAILBOX_PATH, "MAX_DEADLINE_S").value
    )
    enums_in_schema = _collect_enum_lists(schema)
    assert len(ask["metadata"]["enum_refs"]) == len(enums_in_schema)
    assert sorted(json.dumps(sorted(values, key=repr)) for values in enums_in_schema) == sorted(
        json.dumps(values) for values in ask["metadata"]["enum_refs"].values()
    )

    answer = rows["routing:mcp:orchestrator:answer_ask"]["metadata"]
    assert set(answer["definition"]["inputSchema"]["properties"]["answered_by"]["enum"]) == {
        "orchestrator",
        "operator",
    }
    assert answer["declared_in_roles"] == ["orchestrator"]
    assert ask["metadata"]["declared_in_roles"] == ["subagent"]
    assert rows["routing:mcp:orchestrator:inbox"]["metadata"]["roles"] == ["orchestrator"]


def test_misconfigured_role_has_zero_tools_and_no_rows(
    real_report: dict[str, Any], real_probe: dict[str, Any]
) -> None:
    misconfigured = real_probe["roles"]["misconfigured"]
    assert misconfigured["role"] == "misconfigured"
    assert misconfigured["tools"] == {}
    assert misconfigured["declared"] == []
    assert not [
        row
        for row in real_report["surfaces"]
        if row["surface_id"].startswith("routing:mcp:misconfigured:")
    ]
    assert not [
        issue
        for issue in real_report["issues"]
        if issue["code"] == "mcp-misconfigured-role-has-tools"
    ]


def test_tool_contract_change_changes_row(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    tools_path = root / rc.MCP_TOOLS_REL_PATH
    before = {row["surface_id"]: row for row in rc.discover_with_issues(root)["surfaces"]}
    text = tools_path.read_text(encoding="utf-8")
    changed = text.replace(
        '"question": {"type": "string", "minLength": 1}',
        '"question": {"type": "string", "minLength": 2}',
    )
    assert changed != text
    tools_path.write_text(changed, encoding="utf-8")

    after = {row["surface_id"]: row for row in rc.discover_with_issues(root)["surfaces"]}
    old = before["routing:mcp:subagent:ask_orchestrator"]["metadata"]
    new = after["routing:mcp:subagent:ask_orchestrator"]["metadata"]
    assert new["input_schema_sha256"] != old["input_schema_sha256"]
    assert new["definition_sha256"] != old["definition_sha256"]
    assert new["source_sha256"] != old["source_sha256"]

    renamed = text.replace('"name": "inbox",', '"name": "inbox_v2",')
    tools_path.write_text(renamed, encoding="utf-8")
    report = rc.discover_with_issues(root)
    ids = {row["surface_id"] for row in report["surfaces"]}
    assert "routing:mcp:orchestrator:inbox_v2" not in ids
    assert "routing:mcp:orchestrator:inbox" not in ids
    assert "routing:mcp:orchestrator:answer_ask" in ids
    mismatches = [issue for issue in report["issues"] if issue["code"] == "mcp-role-tool-mismatch"]
    assert any("routing:mcp:orchestrator:inbox" in issue["surface_ids"] for issue in mismatches)


def test_malformed_known_json_raises_diagnostic(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    manifest = root / "plugins/copilot/plugin.json"
    marketplace = root / ".claude-plugin/marketplace.json"

    manifest.write_text("{ not json", encoding="utf-8")
    with pytest.raises(rc.DeclarationError, match="invalid JSON"):
        rc.discover_with_issues(root)

    manifest.write_text("[]", encoding="utf-8")
    with pytest.raises(rc.DeclarationError, match="must be an object"):
        rc.discover_with_issues(root)

    manifest.write_text('{"version": "0.1.0"}', encoding="utf-8")
    with pytest.raises(rc.DeclarationError, match="/name"):
        rc.discover_with_issues(root)

    manifest.write_text('{"name": "pitwall-copilot", "keywords": [1]}', encoding="utf-8")
    with pytest.raises(rc.DeclarationError, match="/keywords"):
        rc.discover_with_issues(root)

    shutil.copy2(REPO_ROOT / ".claude-plugin/marketplace.json", manifest)
    marketplace.write_text("{ not json", encoding="utf-8")
    with pytest.raises(rc.DeclarationError, match="invalid JSON"):
        rc.discover_with_issues(root)

    data = _load_json(REPO_ROOT / ".claude-plugin/marketplace.json")
    data["plugins"] = {"name": "pitwall"}
    _write_json(marketplace, data)
    with pytest.raises(rc.DeclarationError, match="/plugins"):
        rc.discover_with_issues(root)

    data = _load_json(REPO_ROOT / ".claude-plugin/marketplace.json")
    del data["plugins"][0]["source"]
    _write_json(marketplace, data)
    with pytest.raises(rc.DeclarationError, match="/source"):
        rc.discover_with_issues(root)

    data = _load_json(REPO_ROOT / ".claude-plugin/marketplace.json")
    data["plugins"][0]["source"] = "/etc/passwd"
    _write_json(marketplace, data)
    with pytest.raises(rc.DeclarationError, match="/source"):
        rc.discover_with_issues(root)


def test_manifest_and_marketplace_changes_and_removals(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    manifest = root / "plugins/copilot/plugin.json"
    marketplace = root / ".claude-plugin/marketplace.json"
    baseline = {row["surface_id"]: row for row in rc.discover_with_issues(root)["surfaces"]}
    manifest_id = "plugin:manifest:plugins/copilot/plugin.json"

    record = _load_json(manifest)
    record["description"] = "changed by the test"
    _write_json(manifest, record)
    report = rc.discover_with_issues(root)
    row = _row(report, manifest_id)
    assert row["metadata"]["record_sha256"] != baseline[manifest_id]["metadata"]["record_sha256"]
    assert row["metadata"]["sha256"] != baseline[manifest_id]["metadata"]["sha256"]

    data = _load_json(marketplace)
    data["plugins"].append(
        {
            "name": "ghost",
            "source": "./plugins/ghost",
        }
    )
    _write_json(marketplace, data)
    report = rc.discover_with_issues(root)
    ghost = _row(report, "plugin:marketplace:claude:ghost")
    assert ghost["metadata"]["host"] == "claude"
    assert ghost["metadata"]["host_source"] == "marketplace-declaration"
    assert any(
        issue["code"] == "marketplace-entry-without-plugin-manifest"
        and issue["surface_ids"] == ["plugin:marketplace:claude:ghost"]
        for issue in report["issues"]
    )

    manifest.unlink()
    report = rc.discover_with_issues(root)
    ids = {row["surface_id"] for row in report["surfaces"]}
    assert manifest_id not in ids
    copilot = _row(report, "plugin:marketplace:copilot:pitwall-copilot")
    assert copilot["metadata"]["host_source"] == "registry-package-path"
    assert "plugin:marketplace:claude:pitwall-copilot" not in ids

    for path in _real_manifest_paths():
        manifest_path = root / path.relative_to(REPO_ROOT)
        if manifest_path.exists():
            manifest_path.unlink()
    report = rc.discover_with_issues(root)
    assert any(issue["code"] == "unresolved-plugin-manifests" for issue in report["issues"])
    for _family, _relative, entry in _marketplace_entries():
        expected_host = _registry_hosts()[_normalize_source(entry["source"])]
        fallback = _row(report, f"plugin:marketplace:{expected_host}:{entry['name']}")
        assert fallback["metadata"]["host_source"] == "registry-package-path"

    for _, relative in rc.MARKETPLACE_DECLARATIONS:
        (root / relative).unlink()
    report = rc.discover_with_issues(root)
    assert any(issue["code"] == "unresolved-plugin-marketplaces" for issue in report["issues"])
    assert not [
        row
        for row in report["surfaces"]
        if row["kind"] == "plugin" and row["metadata"]["category"] == "plugin-marketplace"
    ]


def test_adapter_add_remove_change_and_dynamic_registry(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    providers = root / rc.ADAPTERS_REL_PATH
    registry_init = providers / "__init__.py"

    (providers / "muse.py").unlink()
    report = rc.discover_with_issues(root)
    assert "routing:adapter:muse" not in {row["surface_id"] for row in report["surfaces"]}
    assert any(issue["code"] == "unbound-adapter-provider" for issue in report["issues"])

    (providers / "zeta.py").write_text(
        "from .base import HarnessAdapter\n\n\n"
        "class ZetaAdapter(HarnessAdapter):\n"
        '    harness_id = "zeta"\n'
        '    prompt_delivery = "argv"\n'
        '    binary_override_env = "ZETA_BIN"\n',
        encoding="utf-8",
    )
    report = rc.discover_with_issues(root)
    zeta = _row(report, "routing:adapter:zeta")
    assert zeta["metadata"]["class_name"] == "ZetaAdapter"
    assert zeta["metadata"]["registry_binding"] == "static"
    assert zeta["metadata"]["registry_class"] is None
    assert any(issue["code"] == "unlisted-adapter-class" for issue in report["issues"])

    text = registry_init.read_text(encoding="utf-8")
    registry_init.write_text(
        text.replace('"muse": MuseAdapter,', '"zeta": ZetaAdapter,'), encoding="utf-8"
    )
    report = rc.discover_with_issues(root)
    zeta = _row(report, "routing:adapter:zeta")
    assert zeta["metadata"]["registry_class"] == "ZetaAdapter"
    assert not [
        issue
        for issue in report["issues"]
        if issue["code"] in {"unbound-adapter-provider", "unlisted-adapter-class"}
    ]

    registry_init.write_text(
        text.replace('"agy": AgyAdapter,', '"agy": KimiAdapter,'), encoding="utf-8"
    )
    report = rc.discover_with_issues(root)
    mismatch = [
        issue for issue in report["issues"] if issue["code"] == "adapter-registry-binding-mismatch"
    ]
    assert mismatch
    assert mismatch[0]["surface_ids"] == ["routing:adapter:kimi"]

    registry_init.write_text(
        "from .base import HarnessAdapter\n"
        "from .agy import AgyAdapter\n\n\n"
        '_ADAPTERS: dict[str, type[HarnessAdapter]] = dict([("agy", AgyAdapter)])\n',
        encoding="utf-8",
    )
    report = rc.discover_with_issues(root)
    unresolved = [
        issue for issue in report["issues"] if issue["code"] == "unresolved-adapter-registry"
    ]
    assert unresolved and unresolved[0]["severity"] == "unresolved"
    rows = {
        row["surface_id"]: row
        for row in report["surfaces"]
        if row["kind"] == "provider" and row["metadata"]["category"] == "adapter-contract"
    }
    assert "routing:adapter:zeta" in rows
    assert rows["routing:adapter:agy"]["metadata"]["registry_binding"] == "dynamic"
    assert rows["routing:adapter:agy"]["metadata"]["registry_class"] is None
    assert rows["routing:adapter:agy"]["metadata"]["registry_provider_ids"] is None

    kimi = providers / "kimi.py"
    kimi.write_text(
        kimi.read_text(encoding="utf-8").replace('harness_id = "kimi"', 'harness_id = "ki" + "mi"'),
        encoding="utf-8",
    )
    report = rc.discover_with_issues(root)
    assert "routing:adapter:kimi" not in {row["surface_id"] for row in report["surfaces"]}
    assert any(issue["code"] == "unresolved-adapter-provider-id" for issue in report["issues"])


def test_wrong_module_origin_is_rejected(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    tools_path = root / rc.MCP_TOOLS_REL_PATH
    tools_path.write_text(
        "import os\n\n"
        "__file__ = os.path.join(\n"
        '    os.path.dirname(os.path.realpath(__file__)), "shadow", "mcp_tools.py"\n'
        ")\n\n"
        "class _Stub:\n"
        '    role = "orchestrator"\n\n'
        "    def _visible(self):\n"
        "        return {}\n\n"
        "def build_server(env, stdin, stdout):\n"
        '    raise RuntimeError("build_server must not be reached")\n',
        encoding="utf-8",
    )

    with pytest.raises(rc.McpProbeError, match="module origin mismatch before construction"):
        rc.probe_channel_tools(root)
    with pytest.raises(rc.McpProbeError, match="module origin mismatch before construction"):
        rc.discover_with_issues(root)

    real_probe = rc.probe_channel_tools(REPO_ROOT)
    assert Path(real_probe["module"]).resolve() == MCP_TOOLS_PATH.resolve()
    assert Path(real_probe["server_module"]).resolve() == MCP_SERVER_PATH.resolve()


def test_provider_and_handler_modules_are_never_executed(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    adapter = root / rc.ADAPTERS_REL_PATH / "agy.py"
    adapter.write_text(
        'raise RuntimeError("adapter module must not be imported")\n', encoding="utf-8"
    )
    tools_path = root / rc.MCP_TOOLS_REL_PATH
    tools_path.write_text(
        tools_path.read_text(encoding="utf-8").replace(
            "        store = self._store()\n        config = load_channel_config(store.path)\n",
            '        raise RuntimeError("handler must not be called")\n'
            "        store = self._store()\n        config = load_channel_config(store.path)\n",
            1,
        ),
        encoding="utf-8",
    )

    report = rc.discover_with_issues(root)
    ids = {row["surface_id"] for row in report["surfaces"]}
    assert "routing:adapter:agy" not in ids
    assert "routing:mcp:subagent:ask_orchestrator" in ids
    assert any(issue["code"] == "unbound-adapter-provider" for issue in report["issues"])


def test_no_ambient_secrets_or_state_accessed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret = "sekret-release-acceptance-value"
    ambient_state = tmp_path / "ambient-state"
    ambient_state.mkdir()
    (ambient_state / "tripwire.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("PITWALL_RELEASE_ACCEPTANCE_SECRET", secret)
    monkeypatch.setenv("PITWALL_AGENTS_CHANNEL_DISPATCH_ID", str(uuid.uuid4()))
    monkeypatch.setenv("PITWALL_AGENTS_CHANNEL_STATE_ROOT", str(ambient_state))
    before = sorted(path.name for path in ambient_state.iterdir())

    payload = rc.probe_channel_tools(REPO_ROOT)

    assert sorted(path.name for path in ambient_state.iterdir()) == before
    assert payload["state_entries_after"] == []
    assert payload["env_keys"] == sorted(rc.PROBE_ENV_KEYS)
    assert payload["roles"]["orchestrator"]["role"] == "orchestrator"
    assert payload["roles"]["misconfigured"]["tools"] == {}
    assert secret not in json.dumps(rc.discover_with_issues(REPO_ROOT), sort_keys=True)


def test_module_imports_standard_library_only() -> None:
    tree = ast.parse(Path(rc.__file__).read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    assert roots <= {
        "__future__",
        "ast",
        "hashlib",
        "json",
        "pathlib",
        "re",
        "subprocess",
        "sys",
        "tempfile",
        "textwrap",
        "typing",
        "uuid",
    }


def test_rows_do_not_duplicate_registry_provider_host_or_shim_rows(
    real_report: dict[str, Any],
) -> None:
    ours = {row["surface_id"] for row in real_report["surfaces"]}
    assert not any(
        surface_id.startswith(("routing:host:", "routing:provider:", "shim:"))
        for surface_id in ours
    )
    earlier = {row["surface_id"] for row in routing_inventory.discover(REPO_ROOT)}
    assert ours.isdisjoint(earlier)


def test_discover_returns_sorted_surface_rows_only(real_report: dict[str, Any]) -> None:
    rows = rc.discover(REPO_ROOT)
    ids = [row["surface_id"] for row in rows]
    assert ids == sorted(ids)
    assert all(
        set(row) == {"surface_id", "kind", "operation", "source", "metadata"} for row in rows
    )
    assert ids == [row["surface_id"] for row in real_report["surfaces"]]


def test_conflicting_role_schemas_are_retained_not_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roles = {}
    for role, value_type in (("orchestrator", "string"), ("subagent", "integer")):
        roles[role] = {
            "role": role,
            "declared": ["shared"],
            "tools": {"shared": {"name": "shared", "inputSchema": {"type": value_type}}},
        }
    roles["misconfigured"] = {"role": "misconfigured", "declared": [], "tools": {}}
    monkeypatch.setattr(rc, "probe_channel_tools", lambda root: {"roles": roles})
    report = rc.discover_with_issues(REPO_ROOT)
    for role, value_type in (("orchestrator", "string"), ("subagent", "integer")):
        row = _row(report, f"routing:mcp:{role}:shared")
        assert row["metadata"]["definition"]["inputSchema"]["type"] == value_type
    assert any(issue["code"] == rc.ISSUE_MCP_DEFINITION_DRIFT for issue in report["issues"])
