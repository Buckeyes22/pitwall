"""Independent fixtures for the static index; discovery must never execute tests."""

from __future__ import annotations

import json
from pathlib import Path

from tools.release_acceptance import test_index as index


def source(root: Path, relative: str, text: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def ids(report: dict) -> set[str]:
    return {node["node_id"] for node in report["nodes"]}


def test_python_discovery_does_not_execute_and_preserves_nested_identity(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_sample.py",
        "raise RuntimeError('MUST NOT EXECUTE')\n"
        "def test_top(): pass\n"
        "class TestOuter:\n"
        "    def test_outer(self): pass\n"
        "    class TestInner:\n"
        "        async def test_inner(self): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    assert ids(report) == {
        "tests/test_sample.py::test_top",
        "tests/test_sample.py::TestOuter::test_outer",
        "tests/test_sample.py::TestOuter::TestInner::test_inner",
    }
    assert all(row["framework"] == "pytest" for row in report["nodes"])


def test_unittest_inheritance_and_imported_testcase(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/agents/test_unit.py",
        "import unittest\nfrom unittest import TestCase\n"
        "class Base(unittest.TestCase):\n    def test_base(self): pass\n"
        "class Child(Base):\n    def test_child(self): pass\n"
        "class Imported(TestCase):\n    def test_imported(self): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    prefix = "tests/agents/test_unit.py::"
    assert ids(report) == {
        prefix + "Base::test_base",
        prefix + "Child::test_base",
        prefix + "Child::test_child",
        prefix + "Imported::test_imported",
    }
    assert all(row["framework"] == "unittest" for row in report["nodes"])


def test_markers_are_indications_and_parameters_stay_templates(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_params.py",
        "import pytest\npytestmark = pytest.mark.security\n"
        "@pytest.mark.parametrize('value', [1, 2])\n"
        "def test_parameter(value): pass\n"
        "@pytest.mark.skipif(SOME_DYNAMIC_CONDITION, reason='conditional')\n"
        "def test_conditional(): pass\n"
        "@pytest.mark.xfail\n"
        "def test_expected_failure(): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    assert not any("test_parameter" in node for node in ids(report))
    assert len(report["templates"]) == 1
    assert report["templates"][0]["unresolved_reason"] == "parameterized"
    nodes = {r["node_id"].split("::")[-1]: r for r in report["nodes"]}
    assert nodes["test_conditional"]["skip_indicated"] is True
    assert nodes["test_expected_failure"]["expected_failure"] is True
    assert all("security" in row["markers"] for row in nodes.values())


def test_inherited_methods_follow_c3_and_overrides(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_mro.py",
        "class Base:\n    def test_value(self): pass\n"
        "class Left(Base): pass\n"
        "class Right(Base):\n    def test_value(self): pass\n"
        "class TestChild(Left, Right): pass\n"
        "class TestOverride(TestChild):\n    def test_value(self): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    child = next(r for r in report["nodes"] if "::TestChild::" in r["node_id"])
    assert child["metadata"]["inherited_from"] == "Right"
    override = [r for r in report["nodes"] if "::TestOverride::" in r["node_id"]]
    assert len(override) == 1
    assert "inherited_from" not in override[0]["metadata"]


def test_unittest_alias_and_custom_initializer(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/agents/test_alias.py",
        "from unittest import TestCase as Case\n"
        "class Example(Case):\n"
        "    def __init__(self, methodName='runTest'): super().__init__(methodName)\n"
        "    def test_example(self): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    assert len(report["nodes"]) == 1
    assert report["nodes"][0]["framework"] == "unittest"
    assert report["nodes"][0]["kind"] == "unittest-method"


def test_equal_class_names_in_different_files_do_not_collide(tmp_path: Path) -> None:
    for name in ["one", "two"]:
        source(
            tmp_path, f"tests/test_{name}.py", "class TestSame:\n    def test_same(self): pass\n"
        )
    report = index.discover_with_issues(tmp_path)
    assert len(report["nodes"]) == 2
    assert not any(i["code"] == "duplicate-node-id" for i in report["issues"])


def test_duplicate_declarations_are_explicit(tmp_path: Path) -> None:
    source(tmp_path, "tests/test_dup.py", "def test_same(): pass\ndef test_same(): pass\n")
    report = index.discover_with_issues(tmp_path)
    assert len(report["nodes"]) == 1
    assert any(issue["code"] == "duplicate-node-id" for issue in report["issues"])


def test_comments_strings_helpers_and_fixtures_are_not_nodes(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_literals.py",
        '# def test_comment(): pass\nTEXT = "def test_string(): pass"\n'
        "import pytest\n@pytest.fixture\ndef test_fixture(): pass\n"
        "def helper():\n    def test_nested(): pass\n"
        "def test_actual(): pass\n",
    )
    source(tmp_path, "tests/conftest.py", "def test_conftest(): pass\n")
    source(tmp_path, "tests/fixtures/test_fixture_file.py", "def test_data(): pass\n")
    assert ids(index.discover_with_issues(tmp_path)) == {"tests/test_literals.py::test_actual"}


def test_associations_never_claim_surface_coverage(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_hint.py",
        'def test_hint():\n    values = ["/v1/models", "pitwall_health", ["pitwall", "status"]]\n',
    )
    report = index.discover_with_issues(tmp_path)
    hints = report["nodes"][0]["metadata"]["suggested_associations"]
    assert {h["value"] for h in hints} >= {"/v1/models", "pitwall_health"}
    assert all(h["review_status"] == "unreviewed" for h in hints)
    assert "not-coverage" in report["associations_label"]


def test_add_remove_and_body_change_are_detected(tmp_path: Path) -> None:
    p = source(tmp_path, "tests/test_change.py", "def test_first(): assert 1 == 1\n")
    first = index.discover_with_issues(tmp_path)
    p.write_text("def test_first(): assert 2 == 2\ndef test_second(): pass\n")
    second = index.discover_with_issues(tmp_path)
    assert ids(second) - ids(first) == {"tests/test_change.py::test_second"}
    assert first["files"][0]["sha256"] != second["files"][0]["sha256"]
    p.unlink()
    assert index.discover_with_issues(tmp_path)["nodes"] == []


def test_invalid_python_is_reported_without_losing_other_files(tmp_path: Path) -> None:
    source(tmp_path, "tests/test_bad.py", "def test_broken(:\n")
    source(tmp_path, "tests/test_good.py", "def test_good(): pass\n")
    report = index.discover_with_issues(tmp_path)
    assert ids(report) == {"tests/test_good.py::test_good"}
    assert any(issue["path"] == "tests/test_bad.py" for issue in report["issues"])


def test_cyclic_class_declarations_are_explicit_not_recursion_failure(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_cycle.py",
        "class TestCycle(TestCycle):\n    def test_cycle(self): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    assert any("cycle" in issue["code"] or "base" in issue["code"] for issue in report["issues"])


def test_disabled_class_does_not_create_concrete_nodes(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_disabled.py",
        "class TestDisabled:\n    __test__ = False\n    def test_no(self): pass\n",
    )
    assert index.discover_with_issues(tmp_path)["nodes"] == []


def test_unknown_imported_base_is_explicit(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_unknown.py",
        "from helpers import External\nclass TestChild(External):\n    def test_own(self): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    assert any(issue["code"] == "unknown-test-base" for issue in report["issues"])


def test_class_parameterization_does_not_fabricate_concrete_methods(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_class_params.py",
        "import pytest\n@pytest.mark.parametrize('x', [1,2])\n"
        "class TestGroup:\n    def test_value(self,x): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    assert report["nodes"] == []
    assert len(report["templates"]) == 1


def test_cli_output_is_deterministic(tmp_path: Path) -> None:
    source(tmp_path, "tests/test_one.py", "def test_one(): pass\n")
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert index.main(["--root", str(tmp_path), "--output", str(a)]) == 0
    assert index.main(["--root", str(tmp_path), "--output", str(b)]) == 0
    assert a.read_bytes() == b.read_bytes()
    assert json.loads(a.read_text())["nodes"][0]["node_id"] == "tests/test_one.py::test_one"


def test_module_skip_is_retained_as_conditional_collection_issue(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_optional.py",
        "import pytest\nif not AVAILABLE:\n"
        "    pytest.skip('not configured', allow_module_level=True)\n"
        "def test_optional(): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    assert report["nodes"][0]["skip_indicated"] is True
    assert any(i["code"] == "module-skip-condition" for i in report["issues"])


def test_module_parametrize_is_not_a_concrete_node(tmp_path: Path) -> None:
    source(
        tmp_path,
        "tests/test_module_params.py",
        "import pytest\npytestmark = pytest.mark.parametrize('x', [1,2])\n"
        "def test_value(x): pass\n",
    )
    report = index.discover_with_issues(tmp_path)
    assert report["nodes"] == []
    assert len(report["templates"]) == 1
    assert report["templates"][0]["metadata"]["parameterized_by"] == "module-pytestmark"
