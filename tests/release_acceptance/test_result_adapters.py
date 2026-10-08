"""Strict structured result adapter tests.

These fixtures bind only explicit component-prefixed node ids with exact test ids.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from tools.release_acceptance.result_adapters import (
    ResultAdapterError,
    match_unittest_report,
    read_unittest_report,
)
from tools.release_acceptance.unittest_report import run_unittest_discovery


def _unittest_fixture_root(tmp_path: Path, body: str, *, name: str = "test_cases.py") -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("", encoding="utf-8")
    (tests / name).write_text(body, encoding="utf-8")
    return tmp_path


def test_unittest_report_records_callbacks_sources_subtests_and_hashes(tmp_path: Path) -> None:
    root = _unittest_fixture_root(
        tmp_path,
        """\
import unittest


class Cases(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(1, 1)

    @unittest.skip("fixture skip")
    def test_skip(self):
        self.fail("not reached")

    @unittest.expectedFailure
    def test_expected_failure(self):
        self.fail("expected")

    @unittest.expectedFailure
    def test_unexpected_success(self):
        pass

    def test_subtests(self):
        with self.subTest(case="pass"):
            self.assertTrue(True)
        with self.subTest(case="failure"):
            self.fail("subtest")
""",
    )
    output = tmp_path / "evidence" / "unittest.json"
    report = run_unittest_discovery(root, output_path=output)

    assert report.exit_code == 1
    assert report.counts == {
        "tests": 5,
        "pass": 1,
        "failure": 1,
        "error": 0,
        "import_error": 0,
        "skipped": 1,
        "xfailed": 1,
        "unexpected_success": 1,
        "subtests": 2,
        "subtest_failures": 1,
        "duplicate_test_ids": 0,
    }
    by_id = {test.test_id: test for test in report.tests}
    assert by_id["tests.test_cases.Cases.test_pass"].status == "pass"
    assert by_id["tests.test_cases.Cases.test_skip"].status == "skipped"
    assert by_id["tests.test_cases.Cases.test_expected_failure"].status == "xfailed"
    assert by_id["tests.test_cases.Cases.test_unexpected_success"].status == "unexpected_success"
    subtests = by_id["tests.test_cases.Cases.test_subtests"].subtests
    assert [subtest.status for subtest in subtests] == ["pass", "failure"]
    assert all(test.source_file == "tests/test_cases.py" for test in report.tests)
    assert (
        report.source_files["tests/test_cases.py"]
        == by_id["tests.test_cases.Cases.test_pass"].source_sha256
    )
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["exit_code"] == 1
    assert saved["report_sha256"] == report.report_sha256


def test_unittest_report_records_import_error_without_fabricating_source(tmp_path: Path) -> None:
    root = _unittest_fixture_root(
        tmp_path,
        """\
raise RuntimeError("fixture import failure")
""",
        name="test_broken.py",
    )
    report = run_unittest_discovery(root)
    assert report.exit_code == 1
    assert report.counts["tests"] == 1
    assert report.counts["import_error"] == 1
    assert report.tests[0].test_id == "unittest.loader._FailedTest.tests.test_broken"
    assert report.tests[0].status == "import_error"
    assert report.tests[0].source_file is None
    assert report.tests[0].source_sha256 is None


def test_unittest_report_preserves_full_ids_for_same_suffixes(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("", encoding="utf-8")
    body = """\
import unittest


class SameName(unittest.TestCase):
    def test_shared_suffix(self):
        pass
"""
    (tests / "test_first.py").write_text(body, encoding="utf-8")
    (tests / "test_second.py").write_text(body, encoding="utf-8")
    output = tmp_path / "unittest.json"
    report = run_unittest_discovery(tmp_path, output_path=output)
    ids = [test.test_id for test in report.tests]
    assert ids == [
        "tests.test_first.SameName.test_shared_suffix",
        "tests.test_second.SameName.test_shared_suffix",
    ]
    assert report.counts["duplicate_test_ids"] == 0
    matched = match_unittest_report(
        output,
        ["components/sample/tests/test_first.py::SameName::test_shared_suffix"],
        project_prefix="components/sample",
    )
    assert matched.matched[0].source_file == "tests/test_first.py"
    with pytest.raises(ResultAdapterError, match="missing"):
        match_unittest_report(
            output,
            ["components/sample/tests/test_second.py::OtherName::test_shared_suffix"],
            project_prefix="components/sample",
        )
    with pytest.raises(ResultAdapterError, match="missing"):
        match_unittest_report(
            output,
            ["components/sample/tests/test_first.py::SameName::missing"],
            project_prefix="components/sample",
        )


def _rewrite_report(path: Path, mutate: Callable[[dict[str, object]], None]) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    mutate(payload)
    body = dict(payload)
    body.pop("report_sha256", None)
    payload["report_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def test_unittest_reader_rejects_duplicate_rows(tmp_path: Path) -> None:
    root = _unittest_fixture_root(
        tmp_path,
        "import unittest\n\nclass Cases(unittest.TestCase):\n    def test_pass(self):\n        pass\n",
    )
    output = tmp_path / "duplicate.json"
    run_unittest_discovery(root, output_path=output)

    def duplicate(payload: dict[str, object]) -> None:
        rows = payload["tests"]
        assert isinstance(rows, list)
        rows.append(dict(rows[0]))
        counts = payload["counts"]
        assert isinstance(counts, dict)
        counts["tests"] = 2
        counts["pass"] = 2

    _rewrite_report(output, duplicate)
    with pytest.raises(ResultAdapterError, match="duplicate test_id"):
        read_unittest_report(output)


def test_unittest_reader_rejects_source_metadata_map_mismatch(tmp_path: Path) -> None:
    root = _unittest_fixture_root(
        tmp_path,
        "import unittest\n\nclass Cases(unittest.TestCase):\n    def test_pass(self):\n        pass\n",
    )
    output = tmp_path / "source-map-mismatch.json"
    run_unittest_discovery(root, output_path=output)

    def mismatch(payload: dict[str, object]) -> None:
        rows = payload["tests"]
        assert isinstance(rows, list)
        row = rows[0]
        assert isinstance(row, dict)
        row["source_sha256"] = "0" * 64

    _rewrite_report(output, mismatch)
    with pytest.raises(ResultAdapterError, match="source_files does not match"):
        read_unittest_report(output)


def test_unittest_match_rejects_full_id_source_mismatch(tmp_path: Path) -> None:
    root = _unittest_fixture_root(
        tmp_path,
        "import unittest\n\nclass Cases(unittest.TestCase):\n    def test_pass(self):\n        pass\n",
    )
    output = tmp_path / "source-mismatch.json"
    run_unittest_discovery(root, output_path=output)

    def mismatch(payload: dict[str, object]) -> None:
        rows = payload["tests"]
        assert isinstance(rows, list)
        row = rows[0]
        assert isinstance(row, dict)
        row["test_id"] = "tests.other_module.Cases.test_pass"

    _rewrite_report(output, mismatch)
    with pytest.raises(ResultAdapterError, match="does not belong to source_file"):
        match_unittest_report(
            output,
            ["components/sample/tests/test_cases.py::Cases::test_pass"],
            project_prefix="components/sample",
        )


def test_unittest_reader_does_not_read_component_root_or_source_files(tmp_path: Path) -> None:
    root = _unittest_fixture_root(
        tmp_path,
        "import unittest\n\nclass Cases(unittest.TestCase):\n    def test_pass(self):\n        pass\n",
    )
    output = tmp_path / "offline.json"
    run_unittest_discovery(root, output_path=output)

    def externalize(payload: dict[str, object]) -> None:
        payload["component_root"] = str(tmp_path / "external-canary-that-must-not-be-read")
        rows = payload["tests"]
        assert isinstance(rows, list)
        row = rows[0]
        assert isinstance(row, dict)
        row["source_sha256"] = "0" * 64
        sources = payload["source_files"]
        assert isinstance(sources, dict)
        source_file = row["source_file"]
        assert isinstance(source_file, str)
        sources[source_file] = "0" * 64

    _rewrite_report(output, externalize)
    artifact = read_unittest_report(output)
    assert artifact.component_root.endswith("external-canary-that-must-not-be-read")


def test_unittest_match_rejects_nonpassing_requested_status(tmp_path: Path) -> None:
    root = _unittest_fixture_root(
        tmp_path,
        "import unittest\n\nclass Cases(unittest.TestCase):\n    @unittest.skip('fixture')\n    def test_skip(self):\n        pass\n",
    )
    output = tmp_path / "skip.json"
    run_unittest_discovery(root, output_path=output)
    with pytest.raises(ResultAdapterError, match="has result 'skipped'"):
        match_unittest_report(
            output,
            ["components/sample/tests/test_cases.py::Cases::test_skip"],
            project_prefix="components/sample",
        )
