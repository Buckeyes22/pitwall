"""Structured, in-process ``unittest`` discovery reports.

The wrapper runs the standard library test loader and records the result
callbacks directly. It never parses runner text and keeps complete unittest
ids and component-relative source files, including import errors and subtests.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
import unittest
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType
from typing import Any


class UnittestReportError(ValueError):
    """Raised when discovery arguments or report output are invalid."""


@dataclass(frozen=True)
class SubtestResult:
    subtest_id: str
    status: str
    exception_type: str | None


@dataclass(frozen=True)
class TestResult:
    test_id: str
    status: str
    source_file: str | None
    source_sha256: str | None
    exception_type: str | None
    subtests: tuple[SubtestResult, ...]


@dataclass(frozen=True)
class UnittestReport:
    """One structured unittest discovery execution."""

    component_root: str
    start_dir: str
    pattern: str
    top_level_dir: str
    equivalent_command: tuple[str, ...]
    exit_code: int
    counts: dict[str, int]
    tests: tuple[TestResult, ...]
    source_files: dict[str, str]
    report_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "unittest-report.v1",
            "component_root": self.component_root,
            "start_dir": self.start_dir,
            "pattern": self.pattern,
            "top_level_dir": self.top_level_dir,
            "equivalent_command": list(self.equivalent_command),
            "exit_code": self.exit_code,
            "counts": dict(self.counts),
            "tests": [
                {
                    **asdict(test),
                    "subtests": [asdict(subtest) for subtest in test.subtests],
                }
                for test in self.tests
            ],
            "source_files": dict(self.source_files),
            "report_sha256": self.report_sha256,
        }

    def write_json(self, path: Path) -> None:
        """Write this report as canonical JSON and refuse a non-regular target."""

        if path.exists() and not path.is_file():
            raise UnittestReportError(f"report output is not a regular file: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )


@dataclass
class _MutableSubtest:
    subtest_id: str
    status: str
    exception_type: str | None


@dataclass
class _MutableTest:
    test_id: str
    status: str = "pass"
    source_file: str | None = None
    source_sha256: str | None = None
    exception_type: str | None = None
    subtests: list[_MutableSubtest] | None = None

    def frozen(self) -> TestResult:
        return TestResult(
            test_id=self.test_id,
            status=self.status,
            source_file=self.source_file,
            source_sha256=self.source_sha256,
            exception_type=self.exception_type,
            subtests=tuple(SubtestResult(**asdict(subtest)) for subtest in (self.subtests or [])),
        )


def _test_id(test: unittest.case.TestCase) -> str:
    try:
        value = test.id()
    except Exception as exc:  # pragma: no cover  # reason: a custom TestCase.id() may raise anything; re-raised as UnittestReportError
        raise UnittestReportError(f"unittest test id failed: {exc}") from exc
    if not isinstance(value, str) or not value:
        raise UnittestReportError("unittest test id must be a non-empty string")
    return value


def _source_metadata(test: unittest.case.TestCase, root: Path) -> tuple[str | None, str | None]:
    try:
        source = inspect.getsourcefile(test.__class__) or inspect.getfile(test.__class__)
    except OSError, TypeError:
        return None, None
    if not source:
        return None, None
    path = Path(source).resolve()
    if not path.is_file():
        return None, None
    try:
        relative = path.relative_to(root)
    except ValueError:
        # unittest's _FailedTest objects point at unittest/loader.py rather
        # than the missing module. They remain explicitly source-less.
        return None, None
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise UnittestReportError(f"cannot hash unittest source {path}: {exc}") from exc
    return relative.as_posix(), digest


_ErrorInfo = tuple[type[BaseException], BaseException, TracebackType]
_Outcome = _ErrorInfo | tuple[None, None, None]


def _exception_type(error: _Outcome) -> str:
    return error[0].__name__ if error[0] is not None else "unknown"


class _RecordingResult(unittest.TestResult):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root
        self.records: list[_MutableTest] = []
        self._by_object: dict[int, _MutableTest] = {}

    def startTest(self, test: unittest.case.TestCase) -> None:
        super().startTest(test)
        source_file, source_sha256 = _source_metadata(test, self.root)
        record = _MutableTest(
            test_id=_test_id(test),
            source_file=source_file,
            source_sha256=source_sha256,
            subtests=[],
        )
        self.records.append(record)
        self._by_object[id(test)] = record

    def _record(self, test: unittest.case.TestCase) -> _MutableTest:
        record = self._by_object.get(id(test))
        if record is None:
            raise UnittestReportError(
                f"unittest callback arrived before startTest for {_test_id(test)}"
            )
        return record

    def _status(
        self,
        test: unittest.case.TestCase,
        status: str,
        exception_type: str | None = None,
    ) -> None:
        record = self._record(test)
        if record.status == "pass" or status in {"error", "import_error"}:
            record.status = status
            record.exception_type = exception_type

    def addSuccess(self, test: unittest.case.TestCase) -> None:
        super().addSuccess(test)
        self._status(test, "pass")

    def addFailure(
        self,
        test: unittest.case.TestCase,
        err: _Outcome,
    ) -> None:
        super().addFailure(test, err)
        self._status(test, "failure", _exception_type(err))

    def addError(
        self,
        test: unittest.case.TestCase,
        err: _Outcome,
    ) -> None:
        super().addError(test, err)
        status = "import_error" if type(test).__name__ == "_FailedTest" else "error"
        self._status(test, status, _exception_type(err))

    def addSkip(self, test: unittest.case.TestCase, reason: str) -> None:
        super().addSkip(test, reason)
        self._status(test, "skipped")

    def addExpectedFailure(
        self,
        test: unittest.case.TestCase,
        err: _Outcome,
    ) -> None:
        super().addExpectedFailure(test, err)
        self._status(test, "xfailed", _exception_type(err))

    def addUnexpectedSuccess(self, test: unittest.case.TestCase) -> None:
        super().addUnexpectedSuccess(test)
        self._status(test, "unexpected_success")

    def addSubTest(
        self,
        test: unittest.case.TestCase,
        subtest: unittest.case.TestCase,
        outcome: _Outcome | None,
    ) -> None:
        super().addSubTest(test, subtest, outcome)
        record = self._record(test)
        assert record.subtests is not None
        description = getattr(subtest, "_subDescription", None)
        suffix = description() if callable(description) else str(subtest)
        subtest_id = f"{_test_id(test)}::{suffix}"
        if outcome is None or outcome[0] is None:
            status, exception_type = "pass", None
        else:
            failure_type = getattr(test, "failureException", AssertionError)
            status = "failure" if issubclass(outcome[0], failure_type) else "error"
            exception_type = _exception_type(outcome)
            self._status(test, status, exception_type)
        record.subtests.append(
            _MutableSubtest(
                subtest_id=subtest_id,
                status=status,
                exception_type=exception_type,
            )
        )


def _under(root: Path, value: str, label: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise UnittestReportError(f"{label} must be relative to component_root")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root):
        raise UnittestReportError(f"{label} escapes component_root")
    return resolved


def _report_digest(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body.pop("report_sha256", None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def run_unittest_discovery(
    component_root: Path,
    *,
    start_dir: str = "tests",
    pattern: str = "test*.py",
    top_level_dir: str = ".",
    output_path: Path | None = None,
) -> UnittestReport:
    """Run stdlib unittest discovery and return callback-derived provenance."""

    root = Path(component_root).resolve()
    if not root.is_dir():
        raise UnittestReportError(f"component_root is not a directory: {component_root}")
    if not pattern or "\x00" in pattern:
        raise UnittestReportError("pattern must be non-empty and contain no NUL")
    start = _under(root, start_dir, "start_dir")
    top = _under(root, top_level_dir, "top_level_dir")
    if not start.is_dir():
        raise UnittestReportError(f"start_dir is not a directory: {start}")
    if not top.is_dir():
        raise UnittestReportError(f"top_level_dir is not a directory: {top}")
    loader = unittest.TestLoader()
    package_parts = start.relative_to(top).parts
    package_prefix = package_parts[0] if package_parts else None
    previous_path = list(sys.path)
    removed_modules = (
        {
            name: module
            for name, module in list(sys.modules.items())
            if package_prefix is not None
            and (name == package_prefix or name.startswith(f"{package_prefix}."))
        }
        if package_prefix is not None
        else {}
    )
    for name in removed_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(top))
    try:
        suite = loader.discover(str(start), pattern=pattern, top_level_dir=str(top))
        result = _RecordingResult(root)
        suite.run(result)
    finally:
        sys.path[:] = previous_path
        for name in list(sys.modules):
            if package_prefix is not None and (
                name == package_prefix or name.startswith(f"{package_prefix}.")
            ):
                sys.modules.pop(name, None)
        sys.modules.update(removed_modules)
    tests = tuple(record.frozen() for record in result.records)
    by_id: dict[str, int] = {}
    for test in tests:
        by_id[test.test_id] = by_id.get(test.test_id, 0) + 1
    counts = {
        "tests": len(tests),
        "pass": sum(test.status == "pass" for test in tests),
        "failure": sum(test.status == "failure" for test in tests),
        "error": sum(test.status == "error" for test in tests),
        "import_error": sum(test.status == "import_error" for test in tests),
        "skipped": sum(test.status == "skipped" for test in tests),
        "xfailed": sum(test.status == "xfailed" for test in tests),
        "unexpected_success": sum(test.status == "unexpected_success" for test in tests),
        "subtests": sum(len(test.subtests) for test in tests),
        "subtest_failures": sum(
            sum(subtest.status in {"failure", "error"} for subtest in test.subtests)
            for test in tests
        ),
        "duplicate_test_ids": sum(count > 1 for count in by_id.values()),
    }
    source_files = {
        test.source_file: test.source_sha256
        for test in tests
        if test.source_file is not None and test.source_sha256 is not None
    }
    equivalent_command = (
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        start_dir,
        "-p",
        pattern,
        "-t",
        top_level_dir,
    )
    exit_code = 0 if result.wasSuccessful() else 1
    payload = {
        "schema_version": "unittest-report.v1",
        "component_root": str(root),
        "start_dir": start_dir,
        "pattern": pattern,
        "top_level_dir": top_level_dir,
        "equivalent_command": list(equivalent_command),
        "exit_code": exit_code,
        "counts": counts,
        "tests": [
            {
                **asdict(test),
                "subtests": [asdict(subtest) for subtest in test.subtests],
            }
            for test in tests
        ],
        "source_files": source_files,
    }
    report = UnittestReport(
        component_root=str(root),
        start_dir=start_dir,
        pattern=pattern,
        top_level_dir=top_level_dir,
        equivalent_command=equivalent_command,
        exit_code=exit_code,
        counts=counts,
        tests=tests,
        source_files=source_files,
        report_sha256=_report_digest(payload),
    )
    if output_path is not None:
        report.write_json(output_path)
    return report
