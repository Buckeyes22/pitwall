"""Tests for the compact argparse declaration extractor.

The tests prove discovery is grounded in the real target sources: real parser
and subcommand declarations, literal option names, literal kwargs, and a
stable per-declaration contract digest. Adding or removing a declaration
changes the rows; a changed default or choices drifts the digest; comments,
docstrings, and unrelated string literals never fabricate rows. Nested
parsers resolve their parent command only from same-function literal
bindings; dynamic names and unresolvable bindings surface as explicit
UNRESOLVED issues and never fabricate a full command path. Same-named parser
variables in different functions retain distinct rows. Nothing imports,
executes, or runtime-introspects ``pitwall`` or the agent-routing runtime.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from tools.release_acceptance import cli_arguments as ca
from tools.release_acceptance import review_records, test_index

NESTED = """import argparse


def build(argv):
    parser = argparse.ArgumentParser(prog="demo")
    subcommands = parser.add_subparsers(dest="command", required=True)
    run = subcommands.add_parser("run", help="Run it.")
    run.add_argument("target", choices=("a", "b"), default="a")
    run.add_argument("--json", action="store_true")
    return parser.parse_args(argv)
"""

DYNAMIC_PARSER = """import argparse


def build(argv, name):
    parser = argparse.ArgumentParser()
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(name)
    return parser.parse_args(argv)
"""

DUP_VARIABLES = """import argparse


def alpha(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--shared", default=1)
    return parser.parse_args(argv)


def beta(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--shared", default=2)
    return parser.parse_args(argv)
"""

NOISE = """import argparse

ARGUMENT_TEXT = 'add_parser("fake") and --bogus'


def build(argv):
    # parser.add_argument("--commented")
    parser = argparse.ArgumentParser()
    parser.add_argument("--real", help="add_argument('--in-help')")
    return parser.parse_args(argv)
"""

_IMPORT_GUARD = """\
import builtins
import importlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

_original_import = builtins.__import__


def _guarded(name, *args, **kwargs):
    if name == "pitwall" or name.startswith("pitwall.") or name.startswith("model_routing"):
        raise AssertionError(f"runtime import attempted: {name}")
    return _original_import(name, *args, **kwargs)


builtins.__import__ = _guarded
module = importlib.import_module("tools.release_acceptance.cli_arguments")
rows = module.discover(module.ROOT)
print("NO_RUNTIME_IMPORT_OK", len(rows))
"""


def _root(tmp_path: Path, source: str, name: str = "cli_demo.py") -> Path:
    module = tmp_path / ca.CLI_GLOB_DIR / name
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source, encoding="utf-8")
    return tmp_path


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [row["surface_id"] for row in rows]


def _row(rows: list[dict[str, Any]], suffix: str) -> dict[str, Any]:
    return next(row for row in rows if row["surface_id"].endswith(suffix))


def test_added_option_changes_discovery(tmp_path: Path) -> None:
    root = _root(tmp_path, NESTED)
    assert "cli:argument:src/pitwall/cli/cli_demo.py:build:run:--json" in _ids(ca.discover(root))
    assert "cli:argument:src/pitwall/cli/cli_demo.py:build:run:--extra" not in _ids(
        ca.discover(root)
    )
    added = NESTED.replace(
        "    return parser.parse_args(argv)",
        '    run.add_argument("--extra")\n    return parser.parse_args(argv)',
    )
    rows = ca.discover(_root(tmp_path, added))
    assert "cli:argument:src/pitwall/cli/cli_demo.py:build:run:--extra" in _ids(rows)
    removed = ca.discover(
        _root(tmp_path, NESTED.replace('    run.add_argument("--json", action="store_true")\n', ""))
    )
    assert "cli:argument:src/pitwall/cli/cli_demo.py:build:run:--json" not in _ids(removed)


def test_changed_default_or_choices_drifts_contract_digest(tmp_path: Path) -> None:
    base = _row(ca.discover(_root(tmp_path, NESTED)), "run:target")
    changed = NESTED.replace('choices=("a", "b"), default="a"', 'choices=("a", "b"), default="b"')
    assert (
        _row(ca.discover(_root(tmp_path, changed)), "run:target")["metadata"]["contract_digest"]
        != base["metadata"]["contract_digest"]
    )
    narrowed = NESTED.replace('choices=("a", "b")', 'choices=("a",)')
    assert (
        _row(ca.discover(_root(tmp_path, narrowed)), "run:target")["metadata"]["contract_digest"]
        != base["metadata"]["contract_digest"]
    )
    assert base["metadata"]["kwargs"]["default"] == "a"
    assert base["metadata"]["kwargs"]["choices"] == ["a", "b"]


def test_nested_parser_and_parent_command(tmp_path: Path) -> None:
    rows = ca.discover(_root(tmp_path, NESTED))
    assert _row(rows, ":build:run:run")["operation"] == "subcommand"
    assert _row(rows, ":build:run:run")["metadata"]["command"] == "run"
    assert _row(rows, ":build:run:run")["metadata"]["parent_command"] is None
    option = _row(rows, ":build:run:--json")
    assert option["metadata"]["parent_command"] == "run"
    assert option["metadata"]["receiver"] == "run"
    assert option["metadata"]["unresolved"] == []
    assert _row(rows, ":build:run:target")["metadata"]["flags"] == ["target"]


def test_dynamic_parser_name_is_explicit_unresolved_issue(tmp_path: Path) -> None:
    root = _root(tmp_path, DYNAMIC_PARSER)
    ids = _ids(ca.discover(root))
    assert not [surface_id for surface_id in ids if "add_parser" in surface_id]
    assert "cli:subcommand:src/pitwall/cli/cli_demo.py:build:subcommands:<dynamic>" not in ids
    report = ca.discover_with_issues(root)
    dynamic = [issue for issue in report["issues"] if issue["topic"] == ca.ISSUE_DYNAMIC_COMMAND]
    assert len(dynamic) == 1
    assert dynamic[0]["status"] == "unresolved"
    assert "UNRESOLVED" in dynamic[0]["reason"]
    assert dynamic[0]["source"] == "src/pitwall/cli/cli_demo.py:7"
    assert "full command path" not in str(report)
    assert all(
        row["metadata"].get("parent_command") is None
        for row in report["surfaces"]
        if row["operation"] == "argument"
    )


def test_comments_and_string_literals_never_fabricate_rows(tmp_path: Path) -> None:
    report = ca.discover_with_issues(_root(tmp_path, NOISE))
    ids = _ids(report["surfaces"])
    assert "cli:argument:src/pitwall/cli/cli_demo.py:build:parser:--real" in ids
    for fabricated in ("--commented", "--bogus", "--in-help", "fake"):
        assert not [surface_id for surface_id in ids if surface_id.endswith(fabricated)]
    assert [row for row in report["surfaces"] if row["operation"] == "argument"] == [
        _row(report["surfaces"], ":build:parser:--real")
    ]


def test_duplicate_parser_variables_keep_distinct_rows(tmp_path: Path) -> None:
    rows = ca.discover(_root(tmp_path, DUP_VARIABLES))
    alpha = _row(rows, ":alpha:parser:--shared")
    beta = _row(rows, ":beta:parser:--shared")
    assert alpha["surface_id"] != beta["surface_id"]
    assert alpha["metadata"]["kwargs"]["default"] == 1
    assert beta["metadata"]["kwargs"]["default"] == 2
    assert alpha["metadata"]["contract_digest"] != beta["metadata"]["contract_digest"]
    assert _ids(rows) == sorted(_ids(rows))
    assert len(_ids(rows)) == len(set(_ids(rows)))


def test_duplicate_option_occurrence_is_explicit_issue(tmp_path: Path) -> None:
    source = NESTED.replace(
        '    run.add_argument("--json", action="store_true")',
        '    run.add_argument("--json", action="store_true")\n'
        '    run.add_argument("--json", action="store_true")',
    )
    report = ca.discover_with_issues(_root(tmp_path, source))
    ids = _ids(report["surfaces"])
    assert len(ids) == len(set(ids))
    duplicates = [issue for issue in report["issues"] if issue["topic"] == ca.ISSUE_DUPLICATE]
    assert len(duplicates) == 1
    assert duplicates[0]["status"] == "unresolved"
    assert duplicates[0]["duplicate_sources"] == [
        "src/pitwall/cli/cli_demo.py:9",
        "src/pitwall/cli/cli_demo.py:10",
    ]
    assert "UNRESOLVED" in duplicates[0]["reason"]


def test_contract_digest_ignores_lines_and_comments(tmp_path: Path) -> None:
    base = _row(ca.discover(_root(tmp_path, NESTED)), ":build:run:--json")
    shifted = NESTED.replace(
        '    run.add_argument("--json", action="store_true")',
        '    # a comment line\n\n    run.add_argument("--json", action="store_true")',
    )
    moved = _row(ca.discover(_root(tmp_path, shifted)), ":build:run:--json")
    assert moved["source"] != base["source"]
    assert moved["metadata"]["contract_digest"] == base["metadata"]["contract_digest"]


def test_real_root_rows_kind_and_sorted_unique() -> None:
    rows = ca.discover(ca.ROOT)
    report = ca.discover_with_issues(ca.ROOT)
    assert report["surfaces"] == rows
    assert report["schema_version"] == ca.SCHEMA_VERSION
    assert rows
    assert {row["kind"] for row in rows} == {"cli"}
    operations = {row["operation"] for row in rows}
    assert {"parser", "argument", "subcommand", "subparsers"} <= operations
    ids = _ids(rows)
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
    assert all(row["source"].startswith(row["metadata"]["file"] + ":") for row in rows)
    assert "cli:subcommand:src/pitwall/cli/leases.py:_parse_leases_args:stop_parser:stop" in _ids(
        rows
    )
    entry = _row(rows, "_parse_leases_args:stop_parser:stop")
    assert entry["metadata"]["command"] == "stop"
    assert entry["metadata"]["parent_command"] is None
    assert _row(rows, ":stop_parser:--reason")["metadata"]["parent_command"] == "stop"
    unresolved = [issue for issue in report["issues"] if issue["topic"] == ca.ISSUE_UNBOUND_PARSER]
    assert unresolved
    assert all(issue["status"] == "unresolved" for issue in unresolved)


def test_scope_issues_state_unresolved_open_hierarchy() -> None:
    report = ca.discover_with_issues(ca.ROOT)
    topics = {issue["topic"]: issue for issue in report["issues"]}
    for topic in ("aliased_constructors", "helper_added_arguments", "runtime_behavior"):
        assert topics[topic]["status"] == "unresolved"
        assert "UNRESOLVED" in topics[topic]["reason"]
    assert topics["scope"]["status"] == "partial"
    assert "dynamic hierarchy remains open" in topics["scope"]["reason"]


def test_discovery_never_imports_runtime_packages() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_GUARD],
        cwd=ca.ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("NO_RUNTIME_IMPORT_OK")


def test_all_positional_aliases_are_preserved(tmp_path: Path) -> None:
    rows = ca.discover(_root(tmp_path, 'p = ArgumentParser()\np.add_argument("-v", "--verbose")\n'))
    assert _row(rows, ":p:-v")["metadata"]["flags"] == ["-v", "--verbose"]


def test_same_method_names_in_classes_remain_distinct(tmp_path: Path) -> None:
    source = "\n".join(
        f'class {name}:\n    def build(self):\n        p = ArgumentParser()\n        p.add_argument("--x")'
        for name in ("A", "B")
    )
    rows = ca.discover(_root(tmp_path, source))
    assert {row["metadata"]["function"] for row in rows if row["operation"] == "argument"} == {
        "A.build",
        "B.build",
    }


def test_cyclic_binding_is_unresolved_without_recursion(tmp_path: Path) -> None:
    source = 'a = b.add_argument_group("a")\nb = a.add_argument_group("b")\na.add_argument("--x")\n'
    report = ca.discover_with_issues(_root(tmp_path, source))
    assert _row(report["surfaces"], ":a:--x")["metadata"]["unresolved"]


def test_later_binding_does_not_change_earlier_argument(tmp_path: Path) -> None:
    source = (
        "p = ArgumentParser()\ns = p.add_subparsers()\n"
        'x = s.add_parser("first")\nx.add_argument("--before")\n'
        'x = s.add_parser("second")\nx.add_argument("--after")\n'
    )
    rows = ca.discover(_root(tmp_path, source))
    assert _row(rows, ":x:--before")["metadata"]["parent_command"] == "first"
    assert _row(rows, ":x:--after")["metadata"]["parent_command"] == "second"


def test_unknown_reassignment_invalidates_parser_binding(tmp_path: Path) -> None:
    source = 'p = ArgumentParser()\np = unknown_factory()\np.add_argument("--x")\n'
    report = ca.discover_with_issues(_root(tmp_path, source))
    assert _row(report["surfaces"], ":p:--x")["metadata"]["unresolved"]


def test_cli_source_review_and_bindings_are_current_and_not_run() -> None:
    review_path = ca.ROOT / "release_acceptance/cli-source-review.json"
    bindings_path = ca.ROOT / "release_acceptance/reviewed-bindings-cli.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))

    assert review["status"] == "source_review_only"
    # The retained inventory is checked against the live discovery and against its own review
    # lists, so these counts move with the source instead of being pinned here.
    live = ca.discover_with_issues(ca.ROOT)["issues"]
    inventory = review["inventory"]
    assert inventory["issue_count"] == len(live)
    assert inventory["issue_topics"] == dict(Counter(str(issue["topic"]) for issue in live))
    assert inventory["reviewed_issue_count"] == len(review["issue_reviews"])
    assert inventory["retained_unresolved_scope_count"] == len(review["unresolved_scope"])
    assert inventory["issue_count"] == (
        inventory["reviewed_issue_count"] + inventory["retained_unresolved_scope_count"]
    )
    assert {item["topic"] for item in review["unresolved_scope"]} == {
        str(issue["topic"])
        for issue in live
        if issue["topic"] not in {review_["issue_topic"] for review_ in review["issue_reviews"]}
    }
    stale = [
        dependency["path"]
        for dependency in review["dependencies"]
        if dependency["normalized_sha256"]
        != review_records.file_digest(ca.ROOT, dependency["path"])
    ]
    assert stale == [], (
        f"reviewed files changed in content: {stale}; re-read them, then "
        f"{review_records.ACCEPT_HINT}"
    )

    assert bindings["candidate_id"] is None
    assert bindings["bindings"]
    assert all(binding["status"] == "not_run" for binding in bindings["bindings"])
    indexed_nodes = [row["node_id"] for row in test_index.build_report(ca.ROOT)["nodes"]]
    assert all(
        binding["review_state"].endswith("execution_evidence_pending")
        for binding in bindings["bindings"]
    )
    for binding in bindings["bindings"]:
        source_path = ca.ROOT / binding["source"].rsplit(":", 1)[0]
        assert binding["test_source_sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()
        assert indexed_nodes.count(binding["test_node_id"]) == 1
