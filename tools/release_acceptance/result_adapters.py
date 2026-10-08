"""Strict adapters for structured component test results.

The release matrix uses pytest node ids for the broker. Component test runners
have different identities, so this module keeps the adaptation explicit rather
than matching suffixes or inferring a pass from a report summary.

``match_unittest_report`` accepts node ids in the form
``<repository-project>/<source-file>::<test-id tail>``. The project prefix is
required for every requested node, and each node must match exactly once with a
passing result.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


class ResultAdapterError(ValueError):
    """Raised when structured component results cannot prove requested nodes."""


@dataclass(frozen=True)
class UnittestResultCase:
    """One validated unittest report row."""

    test_id: str
    status: str
    source_file: str | None
    source_sha256: str | None


@dataclass(frozen=True)
class UnittestReportArtifact:
    """Validated JSON emitted by ``run_unittest_discovery``."""

    report_path: str
    report_sha256: str
    component_root: str
    equivalent_command: tuple[str, ...]
    exit_code: int
    counts: dict[str, int]
    tests: tuple[UnittestResultCase, ...]
    source_files: dict[str, str]


@dataclass(frozen=True)
class UnittestMatchReport:
    """Exact requested unittest rows matched from one report."""

    artifact: UnittestReportArtifact
    matched: tuple[UnittestResultCase, ...]


_UNITTEST_STATUSES = {
    "pass",
    "failure",
    "error",
    "import_error",
    "skipped",
    "xfailed",
    "unexpected_success",
}
_UNITTEST_COUNT_FIELDS = {
    "tests",
    "pass",
    "failure",
    "error",
    "import_error",
    "skipped",
    "xfailed",
    "unexpected_success",
    "subtests",
    "subtest_failures",
    "duplicate_test_ids",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ResultAdapterError(f"unittest report {field} must be a non-empty string")
    return value


def _require_digest(value: Any, field: str) -> str:
    text = _require_string(value, field)
    if not _SHA256_RE.fullmatch(text):
        raise ResultAdapterError(f"unittest report {field} must be a 64-character SHA-256")
    return text


def _canonical_report_digest(payload: dict[str, Any]) -> str:
    body = dict(payload)
    body.pop("report_sha256", None)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_source_file(
    source_file: Any,
    source_sha256: Any,
    *,
    test_id: str,
    status: str,
) -> tuple[str | None, str | None]:
    if source_file is None or source_sha256 is None:
        if status == "import_error" and source_file is None and source_sha256 is None:
            return None, None
        raise ResultAdapterError(f"unittest report source metadata is missing for {test_id!r}")
    relative = _require_string(source_file, f"tests[{test_id}].source_file")
    relative_path = PurePosixPath(relative)
    if (
        relative != relative.strip()
        or relative_path.is_absolute()
        or "." in relative_path.parts
        or ".." in relative_path.parts
        or "\\" in relative
    ):
        raise ResultAdapterError(f"unittest report source_file is not relative for {test_id!r}")
    return relative, _require_digest(source_sha256, f"tests[{test_id}].source_sha256")


def read_unittest_report(report_path: Path) -> UnittestReportArtifact:
    """Validate and read a report emitted by ``run_unittest_discovery``."""

    path = Path(report_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ResultAdapterError(f"cannot read unittest report {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != "unittest-report.v1":
        raise ResultAdapterError("unittest report schema_version must be unittest-report.v1")
    report_sha256 = _require_digest(payload.get("report_sha256"), "report_sha256")
    if _canonical_report_digest(payload) != report_sha256:
        raise ResultAdapterError("unittest report report_sha256 does not match its contents")
    # This is retained provenance, not a permission to read the current host.
    # Source hashes are checked for shape and consistency with source_files;
    # the trusted candidate/run integration owns comparison with its checkout.
    component_root = _require_string(payload.get("component_root"), "component_root")
    equivalent_command_raw = payload.get("equivalent_command")
    if (
        not isinstance(equivalent_command_raw, list)
        or not equivalent_command_raw
        or not all(isinstance(item, str) and item for item in equivalent_command_raw)
    ):
        raise ResultAdapterError(
            "unittest report equivalent_command must be a non-empty string list"
        )
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ResultAdapterError("unittest report exit_code must be an integer")
    counts = payload.get("counts")
    if not isinstance(counts, dict) or set(counts) != _UNITTEST_COUNT_FIELDS:
        raise ResultAdapterError("unittest report counts do not match the v1 schema")
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in counts.values()
    ):
        raise ResultAdapterError("unittest report counts must be non-negative integers")
    rows = payload.get("tests")
    if not isinstance(rows, list):
        raise ResultAdapterError("unittest report tests must be a list")
    tests: list[UnittestResultCase] = []
    source_files: dict[str, str] = {}
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ResultAdapterError(f"unittest report tests[{index}] must be an object")
        test_id = _require_string(row.get("test_id"), f"tests[{index}].test_id")
        status = _require_string(row.get("status"), f"tests[{index}].status")
        if status not in _UNITTEST_STATUSES:
            raise ResultAdapterError(
                f"unittest report test {test_id!r} has unknown status {status!r}"
            )
        source_file, source_sha256 = _validate_source_file(
            row.get("source_file"),
            row.get("source_sha256"),
            test_id=test_id,
            status=status,
        )
        subtests = row.get("subtests")
        if not isinstance(subtests, list):
            raise ResultAdapterError(f"unittest report subtests must be a list for {test_id!r}")
        for subtest in subtests:
            if not isinstance(subtest, dict) or not _require_string(
                subtest.get("subtest_id"), "subtest_id"
            ):
                raise ResultAdapterError(f"unittest report has malformed subtest for {test_id!r}")
            if subtest.get("status") not in {"pass", "failure", "error"}:
                raise ResultAdapterError(f"unittest report has malformed subtest for {test_id!r}")
        if test_id in {test.test_id for test in tests}:
            raise ResultAdapterError(f"unittest report contains duplicate test_id {test_id!r}")
        test = UnittestResultCase(test_id, status, source_file, source_sha256)
        tests.append(test)
        if source_file is not None and source_sha256 is not None:
            source_files[source_file] = source_sha256
    declared_sources = payload.get("source_files")
    if not isinstance(declared_sources, dict) or declared_sources != source_files:
        raise ResultAdapterError("unittest report source_files does not match test metadata")
    observed_counts = {
        "tests": len(tests),
        "pass": sum(test.status == "pass" for test in tests),
        "failure": sum(test.status == "failure" for test in tests),
        "error": sum(test.status == "error" for test in tests),
        "import_error": sum(test.status == "import_error" for test in tests),
        "skipped": sum(test.status == "skipped" for test in tests),
        "xfailed": sum(test.status == "xfailed" for test in tests),
        "unexpected_success": sum(test.status == "unexpected_success" for test in tests),
        "subtests": sum(len(row.get("subtests", [])) for row in rows),
        "subtest_failures": sum(
            sum(
                subtest.get("status") in {"failure", "error"} for subtest in row.get("subtests", [])
            )
            for row in rows
        ),
        "duplicate_test_ids": 0,
    }
    if counts != observed_counts:
        raise ResultAdapterError(
            f"unittest report counts do not match observed results: {counts!r} != {observed_counts!r}"
        )
    return UnittestReportArtifact(
        report_path=str(path),
        report_sha256=report_sha256,
        component_root=str(component_root),
        equivalent_command=tuple(equivalent_command_raw),
        exit_code=exit_code,
        counts=counts,
        tests=tuple(tests),
        source_files=source_files,
    )


def _static_unittest_tail(test_id: str, source_file: str) -> str:
    """Convert a full unittest id to the repository index's ``Class::method``."""

    source_path = PurePosixPath(source_file)
    if source_path.suffix != ".py" or not source_path.stem:
        raise ResultAdapterError(f"unittest source_file is not a Python module: {source_file!r}")
    module = ".".join((*source_path.parts[:-1], source_path.stem))
    module_prefix = f"{module}." if module else ""
    if not test_id.startswith(module_prefix):
        raise ResultAdapterError(
            f"unittest id {test_id!r} does not belong to source_file {source_file!r}"
        )
    tail = test_id[len(module_prefix) :]
    if not tail or any(part == "" for part in tail.split(".")):
        raise ResultAdapterError(f"unittest id {test_id!r} has no class/method suffix")
    return "::".join(tail.split("."))


def _validate_project_prefix(project_prefix: str) -> None:
    prefix_path = PurePosixPath(project_prefix)
    if (
        not project_prefix
        or project_prefix != project_prefix.strip()
        or prefix_path.is_absolute()
        or "." in prefix_path.parts
        or ".." in prefix_path.parts
        or "\\" in project_prefix
    ):
        raise ResultAdapterError(
            "project_prefix must be a non-empty repository-relative POSIX path"
        )


def match_unittest_report(
    report_path: Path,
    requested_node_ids: list[str] | tuple[str, ...],
    *,
    project_prefix: str,
) -> UnittestMatchReport:
    """Match ``<repository-project>/<source-file>::<Class>::<method>`` exactly.

    Report rows retain the full dotted unittest id. The requested id follows
    the repository's static ``test_index`` convention; its class/method tail
    is expanded deterministically from the report's source module and full id.
    """

    artifact = read_unittest_report(report_path)
    if artifact.exit_code != 0:
        raise ResultAdapterError(f"unittest report exit_code must be 0, got {artifact.exit_code}")
    if not requested_node_ids:
        raise ResultAdapterError("requested_node_ids must be non-empty")
    _validate_project_prefix(project_prefix)
    requested: list[tuple[str, str]] = []
    for node_id in requested_node_ids:
        file_part, separator, static_tail = node_id.partition("::")
        prefix = f"{project_prefix}/"
        if separator != "::" or not file_part.startswith(prefix) or not static_tail:
            raise ResultAdapterError(
                f"unittest node {node_id!r} must use {project_prefix!r}/<source-file>::<Class>::<method>"
            )
        source_file = file_part[len(prefix) :]
        source_path = PurePosixPath(source_file)
        if (
            not source_file
            or source_path.is_absolute()
            or "." in source_path.parts
            or ".." in source_path.parts
            or "\\" in source_file
        ):
            raise ResultAdapterError(f"unittest node {node_id!r} has an invalid source file")
        key = (source_file, static_tail)
        if key in requested:
            raise ResultAdapterError(f"duplicate requested unittest node {node_id!r}")
        requested.append(key)
    matched: list[UnittestResultCase] = []
    for source_file, static_tail in requested:
        candidates = [
            test
            for test in artifact.tests
            if test.source_file == source_file
            and test.source_file is not None
            and _static_unittest_tail(test.test_id, test.source_file) == static_tail
        ]
        if not candidates:
            raise ResultAdapterError(
                f"requested unittest node {project_prefix}/{source_file}::{static_tail} is missing"
            )
        if len(candidates) != 1:
            raise ResultAdapterError(
                f"requested unittest node {project_prefix}/{source_file}::{static_tail} is ambiguous"
            )
        test = candidates[0]
        if test.status != "pass":
            raise ResultAdapterError(
                f"requested unittest node {project_prefix}/{source_file}::{static_tail} has result {test.status!r}"
            )
        if test.source_file is None or test.source_sha256 is None:
            raise ResultAdapterError(
                f"requested unittest node {project_prefix}/{source_file}::{static_tail} lacks source metadata"
            )
        matched.append(test)
    return UnittestMatchReport(artifact=artifact, matched=tuple(matched))
