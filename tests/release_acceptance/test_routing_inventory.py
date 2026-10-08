"""Tests for :mod:`tools.release_acceptance.routing_inventory`.

The expected provider, host, and shim counts are derived independently from
the real registry JSON and the real shim directory, never hardcoded, so the
tests prove the extractor against the actual tree.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from pitwall.agents.installation import shim_script
from tools.release_acceptance import routing_inventory

REPO_ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = REPO_ROOT / routing_inventory.REGISTRY_REL_PATH


def _load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def _real_shim_names() -> list[str]:
    """The shims install generates: one per registered harness, plus route-shim.sh."""

    names = {record["shim"] for record in _load_registry()["harnesses"].values()}
    return sorted(names | {routing_inventory.ROUTE_SHIM_FILENAME})


def _real_provider_ids() -> list[str]:
    return sorted(_load_registry()["harnesses"])


def _real_host_ids() -> list[str]:
    return sorted(_load_registry()["hosts"])


def _surface(report: dict[str, Any], surface_id: str) -> dict[str, Any]:
    for surface in report["surfaces"]:
        if surface["surface_id"] == surface_id:
            return surface
    raise AssertionError(f"missing surface {surface_id!r}")


def _provider_surfaces(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [surface for surface in report["surfaces"] if surface["kind"] == "provider"]


def _shim_surfaces(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [surface for surface in report["surfaces"] if surface["kind"] == "shim"]


def _copy_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    registry = root / routing_inventory.REGISTRY_REL_PATH
    registry.parent.mkdir(parents=True)
    registry.write_bytes(REGISTRY_PATH.read_bytes())
    return root


def _write_registry(root: Path, data: dict[str, Any]) -> None:
    path = root / routing_inventory.REGISTRY_REL_PATH
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_discover_covers_every_real_provider_host_and_shim() -> None:
    report = routing_inventory.discover_with_issues(REPO_ROOT)

    provider_ids = {surface["surface_id"] for surface in _provider_surfaces(report)}
    assert provider_ids == {f"routing:provider:{pid}" for pid in _real_provider_ids()}

    host_ids = {
        surface["surface_id"] for surface in report["surfaces"] if surface["kind"] == "plugin"
    }
    assert host_ids == {f"routing:host:{hid}" for hid in _real_host_ids()}

    shim_stems = {surface["surface_id"] for surface in _shim_surfaces(report)}
    assert shim_stems == {f"shim:{name[: -len('-shim.sh')]}" for name in _real_shim_names()}
    assert report["schema_version"] == routing_inventory.SUPPORTED_SCHEMA_VERSION


def test_provider_row_preserves_canonical_record_and_digest() -> None:
    registry = _load_registry()
    report = routing_inventory.discover_with_issues(REPO_ROOT)

    for provider_id, record in registry["harnesses"].items():
        surface = _surface(report, f"routing:provider:{provider_id}")
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert surface["metadata"]["record"] == record
        assert surface["metadata"]["sha256"] == expected
        assert surface["metadata"]["json_pointer"] == f"/harnesses/{provider_id}"


def test_host_row_preserves_native_providers_and_package_path() -> None:
    registry = _load_registry()
    report = routing_inventory.discover_with_issues(REPO_ROOT)

    for host_id, record in registry["hosts"].items():
        surface = _surface(report, f"routing:host:{host_id}")
        assert surface["metadata"]["record"] == record
        assert surface["metadata"]["native_providers"] == record["nativeHarnesses"]
        assert surface["metadata"]["package_path"] == record["packagePath"]
        assert (
            surface["metadata"]["sha256"]
            == hashlib.sha256(
                json.dumps(
                    record, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest()
        )
    native_host = _surface(report, "routing:host:claude")
    assert (
        native_host["metadata"]["native_providers"]
        == registry["hosts"]["claude"]["nativeHarnesses"]
    )


def test_shim_rows_hash_files_and_associate_providers() -> None:
    registry = _load_registry()
    report = routing_inventory.discover_with_issues(REPO_ROOT)

    expected: dict[str, list[str]] = {}
    for provider_id, record in registry["harnesses"].items():
        expected.setdefault(record["shim"], []).append(provider_id)

    for name in _real_shim_names():
        stem = name[: -len("-shim.sh")]
        surface = _surface(report, f"shim:{stem}")
        assert surface["source"].startswith(f"{routing_inventory.SHIM_SOURCE_REL_PATH.as_posix()}:")
        assert (
            surface["metadata"]["sha256"]
            == hashlib.sha256(shim_script(name).encode("utf-8")).hexdigest()
        )
        assert surface["metadata"]["providers"] == sorted(expected.get(name, []))


def test_route_shim_is_explicit_separate_dispatcher() -> None:
    report = routing_inventory.discover_with_issues(REPO_ROOT)

    route = _surface(report, "shim:route")
    assert route["operation"] == "route-dispatcher"
    assert route["metadata"]["special_dispatcher"] is True
    assert route["metadata"]["providers"] == []
    assert route["metadata"]["json_pointer"] is None
    assert route["kind"] == "shim"

    for surface in _shim_surfaces(report):
        if surface["surface_id"] == "shim:route":
            continue
        assert surface["metadata"]["special_dispatcher"] is False
        assert surface["operation"] == "compatibility-shim"
        assert surface["metadata"]["providers"] == sorted(surface["metadata"]["providers"])
        assert surface["metadata"]["providers"]


def test_source_changed_provider_digest() -> None:
    baseline = routing_inventory.discover_with_issues(REPO_ROOT)
    registry = _load_registry()
    provider_id = _real_provider_ids()[0]
    before = _surface(baseline, f"routing:provider:{provider_id}")["metadata"]["sha256"]

    mutated = json.loads(json.dumps(registry))
    mutated["harnesses"][provider_id]["displayName"] = "mutated display name"
    report = routing_inventory.discover_with_issues(_tree_with_registry_overlay(mutated))
    after = _surface(report, f"routing:provider:{provider_id}")["metadata"]["sha256"]
    assert after != before


def _tree_with_registry_overlay(data: dict[str, Any]) -> Path:
    import tempfile

    root = Path(tempfile.mkdtemp(prefix="routing-inventory-"))
    registry = root / routing_inventory.REGISTRY_REL_PATH
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return root


def test_added_and_removed_provider_change_ids_and_issues(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    registry = _load_registry()

    added = json.loads(json.dumps(registry))
    added["harnesses"]["newcli"] = json.loads(
        json.dumps(registry["harnesses"][_real_provider_ids()[0]])
    )
    added["harnesses"]["newcli"]["shim"] = "newcli-shim.sh"
    _write_registry(root, added)

    report = routing_inventory.discover_with_issues(root)
    assert _surface(report, "routing:provider:newcli")["operation"] == "provider-registration"
    assert _surface(report, "shim:newcli")["metadata"]["providers"] == ["newcli"]
    assert not [i for i in report["issues"] if i["code"] == "missing-referenced-shim"]

    removed = json.loads(json.dumps(registry))
    del removed["harnesses"]["goose"]
    _write_registry(root, removed)

    report = routing_inventory.discover_with_issues(root)
    assert "routing:provider:goose" not in {surface["surface_id"] for surface in report["surfaces"]}
    assert "shim:goose" not in {surface["surface_id"] for surface in report["surfaces"]}


def test_ungeneratable_shim_name_is_an_issue_but_the_provider_row_is_retained(
    tmp_path: Path,
) -> None:
    root = _copy_tree(tmp_path)
    data = _load_registry()
    data["harnesses"]["codex"]["shim"] = "codex.sh"
    _write_registry(root, data)

    report = routing_inventory.discover_with_issues(root)

    assert _surface(report, "routing:provider:codex")["metadata"]["shim"] == "codex.sh"
    assert "shim:codex" not in {surface["surface_id"] for surface in report["surfaces"]}
    issues = [issue for issue in report["issues"] if issue["code"] == "missing-referenced-shim"]
    assert any(issue["surface_ids"] == ["routing:provider:codex"] for issue in issues)
    assert all(issue["severity"] == "warning" for issue in issues)


def test_shim_surfaces_are_exactly_the_generated_shims() -> None:
    report = routing_inventory.discover_with_issues(REPO_ROOT)
    assert {surface["surface_id"] for surface in _shim_surfaces(report)} == {
        f"shim:{name.removesuffix('-shim.sh')}" for name in _real_shim_names()
    }


def test_malformed_registry_raises_diagnostic(tmp_path: Path) -> None:
    root = _copy_tree(tmp_path)
    registry_path = root / routing_inventory.REGISTRY_REL_PATH

    registry_path.write_text("{ not json", encoding="utf-8")
    with pytest.raises(routing_inventory.RegistryError, match="invalid JSON"):
        routing_inventory.discover_with_issues(root)

    registry = _load_registry()
    registry["schemaVersion"] = 2
    _write_registry(root, registry)
    with pytest.raises(routing_inventory.RegistryError, match="schemaVersion"):
        routing_inventory.discover_with_issues(root)

    registry = _load_registry()
    registry["harnesses"]["codex"]["capabilities"] = "yes"
    _write_registry(root, registry)
    with pytest.raises(routing_inventory.RegistryError, match="capabilities"):
        routing_inventory.discover_with_issues(root)

    registry = _load_registry()
    registry["hosts"]["claude"]["packagePath"] = 7
    _write_registry(root, registry)
    with pytest.raises(routing_inventory.RegistryError, match="packagePath"):
        routing_inventory.discover_with_issues(root)

    registry_path.write_text("[]", encoding="utf-8")
    with pytest.raises(routing_inventory.RegistryError, match="registry root"):
        routing_inventory.discover_with_issues(root)

    registry_path.unlink()
    with pytest.raises(routing_inventory.RegistryError, match="not found"):
        routing_inventory.discover_with_issues(root)


def test_unresolved_scopes_are_explicit_and_separate() -> None:
    report = routing_inventory.discover_with_issues(REPO_ROOT)
    unresolved = [issue for issue in report["issues"] if issue["severity"] == "unresolved"]
    codes = {issue["code"] for issue in unresolved}
    assert codes == {
        "unresolved-adapter-class-contracts",
        "unresolved-plugin-manifests-and-marketplaces",
        "unresolved-channel-routing-mcp-tools",
    }
    assert all(issue["surface_ids"] == [] for issue in unresolved)


def test_discover_returns_sorted_surface_rows_only() -> None:
    rows = routing_inventory.discover(REPO_ROOT)
    ids = [row["surface_id"] for row in rows]
    assert ids == sorted(ids)
    assert all(
        set(row) == {"surface_id", "kind", "operation", "source", "metadata"} for row in rows
    )
    assert ids == [
        row["surface_id"] for row in routing_inventory.discover_with_issues(REPO_ROOT)["surfaces"]
    ]
