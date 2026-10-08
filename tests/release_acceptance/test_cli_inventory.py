"""Tests for root CLI command and console-entrypoint discovery inventory.

The tests prove discovery is grounded in the real ``main`` dispatcher and the
literal ``[project.scripts]`` tables: adding or removing a command, alias, or
entrypoint changes the rows; comments, unrelated literals, and nested function
bodies never do; runs are deterministic with no duplicate surface_ids. Every
row has ``kind == "cli"``; the original surface class is
``metadata["category"]``. Dynamic group predicates surface as explicit
UNRESOLVED issues rather than being silently dropped. Nothing imports
``pitwall`` or inspects the runtime.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.release_acceptance import cli_inventory

BODY = """def main(argv=None):
    args = argv or []
    if not args:
        return cmd_dashboard([])
    if args in (["-h"], ["--help"], ["help"]):
        return 0
    group = args[0]
    if {dispatch}:
        return cmd_one(args[1:])
    print("Unknown command group")
    return 1
"""
ROOT_BODY = BODY.format(dispatch='group == "probe"')
ROOT_COMMANDS = {
    "agents",
    "budget",
    "burn-rate",
    "config",
    "cost",
    "create-capability",
    "dashboard",
    "db",
    "doctor",
    "gateway",
    "guardrails",
    "init",
    "leases",
    "mcp",
    "models",
    "provider-ops",
    "quotas",
    "register-endpoint",
    "register-template",
    "retention",
    "routing",
    "runpod",
    "runpod-onboard",
    "seed",
    "serve",
    "set-provider-health",
    "setup",
    "status",
    "stop",
    "terminate-pod",
    "usage",
    "volume-files",
    "warm-volume",
    "workbench",
}


def _root(tmp_path: Path, body: str, scripts: str | None = None) -> Path:
    module = tmp_path / cli_inventory.CLI_MODULE
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(body, encoding="utf-8")
    if scripts is not None:
        (tmp_path / "pyproject.toml").write_text(f"[project.scripts]\n{scripts}", encoding="utf-8")
    return tmp_path


def _ids(rows: list[dict]) -> list[str]:
    return [row["surface_id"] for row in rows]


def _row(rows: list[dict], surface_id: str) -> dict:
    return next(row for row in rows if row["surface_id"] == surface_id)


def _category(rows: list[dict], category: str) -> list[dict]:
    return [row for row in rows if row["metadata"]["category"] == category]


def _semantics(rows: list[dict]) -> list[tuple]:
    return [
        (r["surface_id"], r["kind"], r["operation"], tuple(sorted(r["metadata"].items())))
        for r in rows
    ]


def test_root_commands_and_entrypoints_match_repo() -> None:
    rows = cli_inventory.discover(cli_inventory.ROOT)
    assert {row["kind"] for row in rows} == {"cli"}
    commands = {row["operation"] for row in _category(rows, "command")}
    assert {"serve", "setup", "stop", "gateway"} <= commands
    assert commands == ROOT_COMMANDS
    assert all(
        re.fullmatch(r"src/pitwall/cli/__init__\.py:\d+", r["source"])
        for r in rows
        if r["metadata"]["category"] in {"command", "flag", "default"}
    )
    entry = {r["metadata"]["name"]: r for r in _category(rows, "entrypoint")}
    assert entry["pitwall"]["source"].startswith("pyproject.toml:")
    assert entry["pitwall"]["metadata"]["target"] == "pitwall.cli:main"
    assert "pitwall-agent-routing" not in entry


def test_real_manifest_entrypoint_lines_are_exact() -> None:
    rows = cli_inventory.discover(cli_inventory.ROOT)
    for manifest in cli_inventory.MANIFESTS:
        text = (cli_inventory.ROOT / manifest).read_text(encoding="utf-8")
        if "[project.scripts]" not in text:
            continue
        line = text.splitlines().index("[project.scripts]") + 1
        sources = {
            row["source"]
            for row in _category(rows, "entrypoint")
            if row["metadata"]["manifest"] == manifest
        }
        assert sources == {f"{manifest}:{line}"}


def test_rows_kind_is_cli_with_category_metadata(tmp_path: Path) -> None:
    rows = cli_inventory.discover(_root(tmp_path, ROOT_BODY, 'pitwall = "pitwall.cli:main"\n'))
    assert [row["kind"] for row in rows] == ["cli"] * len(rows)
    assert _row(rows, "cli:pitwall:probe")["metadata"] == {
        "category": "command",
        "name": "probe",
        "aliases": [],
    }
    assert _row(rows, "cli:pitwall:flag:-h")["metadata"] == {"category": "flag", "flag": "-h"}
    default = _row(rows, "cli:pitwall:default")
    assert default["metadata"] == {"category": "default", "handler": "cmd_dashboard"}
    assert default["operation"] == "dashboard"
    entry = _row(rows, "cli:entrypoint:pitwall")
    assert entry["metadata"] == {
        "category": "entrypoint",
        "name": "pitwall",
        "target": "pitwall.cli:main",
        "manifest": "pyproject.toml",
    }


def test_flags_default_and_source_line(tmp_path: Path) -> None:
    rows = cli_inventory.discover(_root(tmp_path, ROOT_BODY))
    assert _ids(rows) == [
        "cli:pitwall:default",
        "cli:pitwall:flag:--help",
        "cli:pitwall:flag:-h",
        "cli:pitwall:flag:help",
        "cli:pitwall:probe",
    ]
    line = next(n for n, text in enumerate(ROOT_BODY.splitlines(), 1) if 'group == "probe"' in text)
    assert _row(rows, "cli:pitwall:probe")["source"] == f"{cli_inventory.CLI_MODULE}:{line}"


def test_entrypoint_source_is_literal_declaration_line(tmp_path: Path) -> None:
    manifest = (
        "[project]\n"
        'name = "demo"\n'
        "\n"
        "[project.scripts]\n"
        'pitwall = "pitwall.cli:main"\n'
        'pitwall-extra = "pitwall.extra:main"\n'
    )
    root = _root(tmp_path, ROOT_BODY)
    (root / "pyproject.toml").write_text(manifest, encoding="utf-8")

    line = manifest.splitlines().index("[project.scripts]") + 1
    rows = cli_inventory.discover(root)
    assert _row(rows, "cli:entrypoint:pitwall")["source"] == f"pyproject.toml:{line}"
    assert _row(rows, "cli:entrypoint:pitwall-extra")["source"] == f"pyproject.toml:{line}"
    assert all(
        re.fullmatch(r"(?:[^:]+/)?pyproject\.toml:\d+", r["source"])
        for r in _category(rows, "entrypoint")
    )


def test_groups_table_entries_are_commands_sourced_at_their_line(tmp_path: Path) -> None:
    body = """GROUPS = (
    ("alpha", "pkg.alpha", "cmd_alpha"),
    ("beta", "pkg.beta", "cmd_beta"),
)
_DEFAULT_GROUP = ("dashboard", "pkg.dash", "cmd_dashboard")


def main(argv=None):
    args = argv or []
    if not args:
        return _run_group(_DEFAULT_GROUP[1], _DEFAULT_GROUP[2], [])
    return 1
"""
    rows = cli_inventory.discover(_root(tmp_path, body))
    assert _ids(rows) == ["cli:pitwall:alpha", "cli:pitwall:beta", "cli:pitwall:default"]
    assert _row(rows, "cli:pitwall:beta")["source"] == f"{cli_inventory.CLI_MODULE}:3"
    default = _row(rows, "cli:pitwall:default")
    assert default["operation"] == "dashboard"
    assert default["metadata"]["handler"] == "cmd_dashboard"


def test_added_and_removed_command_changes_discovery(tmp_path: Path) -> None:
    assert "cli:pitwall:extra" not in _ids(cli_inventory.discover(_root(tmp_path, ROOT_BODY)))
    added = ROOT_BODY.replace(
        'if group == "probe":',
        'if group == "extra":\n        return cmd_extra([])\n    if group == "probe":',
    )
    assert "cli:pitwall:extra" in _ids(cli_inventory.discover(_root(tmp_path, added)))
    removed = cli_inventory.discover(_root(tmp_path, BODY.format(dispatch='group == "other"')))
    assert "cli:pitwall:probe" not in _ids(removed)


def test_alias_recorded_and_comments_or_strings_ignored(tmp_path: Path) -> None:
    aliased = BODY.format(dispatch='group in ("probe", "probe-alias")')
    rows = cli_inventory.discover(_root(tmp_path, aliased))
    assert _row(rows, "cli:pitwall:probe")["metadata"]["aliases"] == ["probe-alias"]
    for body in (
        ROOT_BODY.replace('print("Unknown command group")', 'print("routing names a command")'),
        ROOT_BODY.replace("group = args[0]", "group = args[0]\n    # routing in a comment"),
    ):
        assert "cli:pitwall:routing" not in _ids(cli_inventory.discover(_root(tmp_path, body)))


def test_set_literal_alias_regression(tmp_path: Path) -> None:
    body = BODY.format(dispatch='group in {"probe", "probe-alias"}')
    rows = cli_inventory.discover(_root(tmp_path, body))
    assert _row(rows, "cli:pitwall:probe")["metadata"]["aliases"] == ["probe-alias"]
    report = cli_inventory.discover_with_issues(_root(tmp_path, body))
    assert not [i for i in report["issues"] if i["topic"] == "dynamic_group_predicate"]


def test_dynamic_group_predicate_is_unresolved_not_omitted(tmp_path: Path) -> None:
    body = """def main(argv=None):
    args = argv or []
    if not args:
        return cmd_dashboard([])
    group = args[0]
    expected = group
    other = "probe"
    if other == "probe":
        print("unrelated comparison")
    if group == expected:
        return cmd_expected([])
    if group == "probe":
        return cmd_one(args[1:])
    return 1
"""
    root = _root(tmp_path, body)
    assert _ids(cli_inventory.discover(root)) == [
        "cli:pitwall:default",
        "cli:pitwall:probe",
    ]
    assert "cli:pitwall:expected" not in _ids(cli_inventory.discover(root))
    report = cli_inventory.discover_with_issues(root)
    dynamic = [i for i in report["issues"] if i["topic"] == "dynamic_group_predicate"]
    line = next(
        n for n, text in enumerate(body.splitlines(), 1) if text.strip() == "if group == expected:"
    )
    assert len(dynamic) == 1
    assert dynamic[0]["status"] == "unresolved"
    assert "UNRESOLVED" in dynamic[0]["reason"]
    assert dynamic[0]["source"] == f"{cli_inventory.CLI_MODULE}:{line}"


def test_nested_function_comparisons_are_ignored(tmp_path: Path) -> None:
    body = """def main(argv=None):
    args = argv or []
    if not args:
        return cmd_dashboard([])
    group = args[0]

    def helper():
        if group == "nested":
            return cmd_nested([])
        if group == dynamic:
            return 1

    if group == "probe":
        return cmd_one(args[1:])
    return 1
"""
    rows = cli_inventory.discover(_root(tmp_path, body))
    assert "cli:pitwall:nested" not in _ids(rows)
    assert "cli:pitwall:probe" in _ids(rows)
    report = cli_inventory.discover_with_issues(_root(tmp_path, body))
    assert not [i for i in report["issues"] if i["topic"] == "dynamic_group_predicate"]


def test_no_duplicate_surface_ids_and_sorted_order(tmp_path: Path) -> None:
    body = """def main(argv=None):
    args = argv or []
    if not args:
        return cmd_dashboard([])
    group = args[0]
    if group in ("probe", "probe-alias"):
        return cmd_one(args[1:])
    if group == "probe":
        return cmd_one(args[1:])
    return 1
"""
    rows = cli_inventory.discover(_root(tmp_path, body))
    ids = _ids(rows)
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert _row(rows, "cli:pitwall:probe")["metadata"]["aliases"] == ["probe-alias"]


def test_added_and_removed_entrypoint_changes_discovery(tmp_path: Path) -> None:
    root = _root(tmp_path, ROOT_BODY, 'pitwall = "pitwall.cli:main"\n')
    assert "cli:entrypoint:pitwall" in _ids(cli_inventory.discover(root))
    assert "cli:entrypoint:pitwall-agent-routing" not in _ids(cli_inventory.discover(root))
    root = _root(
        tmp_path, ROOT_BODY, 'pitwall = "pitwall.cli:main"\npitwall-extra = "pitwall.extra:main"\n'
    )
    assert "cli:entrypoint:pitwall-extra" in _ids(cli_inventory.discover(root))
    root = _root(tmp_path, ROOT_BODY, 'pid = "p:main"\n')
    assert "cli:entrypoint:pitwall-extra" not in _ids(cli_inventory.discover(root))
    assert "cli:entrypoint:pid" in _ids(cli_inventory.discover(root))


def test_comments_never_change_discovery(tmp_path: Path) -> None:
    root = _root(tmp_path, ROOT_BODY, 'pitwall = "pitwall.cli:main"\n')
    before = _semantics(cli_inventory.discover(root))
    module = root / cli_inventory.CLI_MODULE
    module.write_text(
        '# group == "commented"\n' + module.read_text(encoding="utf-8"), encoding="utf-8"
    )
    manifest = root / "pyproject.toml"
    manifest.write_text(
        '# pitwall-commented = "x:y"\n' + manifest.read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert _semantics(cli_inventory.discover(root)) == before


def test_output_is_deterministic_and_sorted(tmp_path: Path) -> None:
    root = _root(tmp_path, ROOT_BODY, 'zeta = "z:main"\nalpha = "a:main"\n')
    first = cli_inventory.discover(root)
    assert first == cli_inventory.discover(root)
    ids = _ids(first)
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert [r["surface_id"] for r in _category(first, "entrypoint")] == [
        "cli:entrypoint:alpha",
        "cli:entrypoint:zeta",
    ]


def test_issues_state_the_unresolved_scope_explicitly() -> None:
    report = cli_inventory.discover_with_issues(cli_inventory.ROOT)
    assert report["schema_version"] == cli_inventory.SCHEMA_VERSION
    assert report["surfaces"] == cli_inventory.discover(cli_inventory.ROOT)
    issues = {issue["topic"]: issue for issue in report["issues"]}
    for topic in ("subcommands", "options", "confirmation", "output_modes"):
        assert issues[topic]["status"] == "unresolved"
        assert "UNRESOLVED" in issues[topic]["reason"]
    assert issues["scope"]["status"] == "partial"
    assert all(row["kind"] == "cli" for row in report["surfaces"])


def test_missing_main_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="main"):
        cli_inventory.discover(_root(tmp_path, "def not_main():\n    return 0\n"))
