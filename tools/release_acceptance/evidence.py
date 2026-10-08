"""Canonical release-acceptance matrix validator and deterministic Markdown renderer.

Schema ``acceptance-evidence.v1``
================================

A matrix is one JSON object::

    {
      "schema_version": "acceptance-evidence.v1",
      "candidate": {
        "candidate_id": "rc-20260920-1",
        "broker_commit": "<40-hex git sha>",
        "artifact_digests": {"wheel": "sha256:<64 hex>"}
      },
      "surfaces": [
        {
          "surface_id": "rest:GET:/health",
          "surface_kind": "rest",
          "declared_source": ["docs/support-matrix.md:12"]
        }
      ],
      "dispositions": [
        {
          "scope": "rest:GET:/debug",
          "reason": "not shipped in this candidate",
          "source": "docs/support-matrix.md:40",
          "approved_by": "release-owner",
          "approval_receipt": "ops/receipts/2026-09-20-debug.md"
        }
      ],
      "cases": [
        {
          "case_id": "REST-HEALTH-01",
          "surface_id": "rest:GET:/health",
          "scenario_id": "linux-wheel-loopback-noauth",
          "journey_id": null,
          "applicability": "required",
          "required_lanes": ["hermetic"],
          "test_node_ids": ["tests/api/test_health.py::test_health"],
          "lane_test_node_ids": null,
          "manual_steps": [],
          "oracle": {
            "expected": "HTTP 200",
            "actual": "HTTP 200",
            "observed_success": true
          },
          "status": "pass",
          "lane_results": {"hermetic": "pass"},
          "evidence": [
            {
              "lane": "hermetic",
              "format": "junit",
              "path": "runs/run-1/junit.xml",
              "sha256": "<64 hex>",
              "candidate_id": "rc-20260920-1",
              "exit_code": 0
            }
          ],
          "exception": null
        }
      ]
    }

Vocabulary
----------

* ``status`` and every ``lane_results`` value: ``pass``, ``fail``, ``blocked``, ``not_run``,
  ``stale``, ``approved_exception``. ``skip``, ``xfail``, and deselection are not statuses and
  can never be recorded as a pass.
* ``applicability``: ``required``, ``optional``, ``unsupported``, ``deferred``.
* ``required_lanes`` and evidence ``lane``: ``hermetic``, ``integration``, ``release``,
  ``manual``, ``live``. All except ``manual`` are automated and prove a pass with a
  structured ``junit`` result; ``manual`` requires a reviewer-observed record.
* ``test_node_ids`` are the case-global pytest node ids used only when no
  ``lane_test_node_ids`` mapping is supplied and exactly one automated lane is required. When
  two or more automated lanes are required, each automated lane must bind its own node ids with
  ``lane_test_node_ids`` (lane name -> list of node ids), because a single hermetic test must
  not be allowed to satisfy the integration lane for the same case. A supplied mapping is
  honored whenever present, including for a single automated lane.
* ``evidence[].format``: ``junit`` (pytest), ``unittest-json``,
  or ``text``. Component formats require an explicit repository-relative
  ``project_prefix`` and exact repository-relative test nodes. One component
  report proves a lane; combining or duplicating component reports is rejected.
  Arbitrary prose can never prove an automated
  pass: a ``junit`` entry is parsed as XML and each ``test_node_ids``/``lane_test_node_ids``
  entry is validated separately against its own lane's JUnit runs; each node must be collected
  exactly once, pass, and come from a run whose ``exit_code`` is 0.

Structured provenance
---------------------

Every claimed ``pass`` is verified against its artifact, never against prose:

* a JUnit entry is parsed with the stdlib XML parser; the case's pytest node ids are bound to
  collected ``testcase`` elements per lane: the case-global ``test_node_ids`` only prove the
  single automated lane when no lane mapping is supplied, and ``lane_test_node_ids`` entries
  are checked only against their own lane's JUnit runs. Module/class binding is
  repository-relative and exact: a JUnit ``classname`` must equal the normalized module plus
  the node's class qualifiers (or the module alone for a module-level test), so ``test_health``
  and ``tests.api.test_health`` never match ``tests/api/test_health.py::TestHealth::test_health``.
  A missing node (zero matches), a ``failure``/``error``/``skipped``/``rerun``/
  ``flaky`` result, a non-zero or missing run ``exit_code``, or a text entry substituting for a
  JUnit result all reject the pass;
* duplicate collected results for a bound node, non-JUnit XML roots, and declared
  suite counts that disagree with the contained test results reject the pass;
* a manual entry must carry a reviewer, ``observed_success: true``, a timezone-aware ISO
  timestamp, non-empty steps, and the candidate identity. Nothing is inferred or fabricated;
* ``oracle.observed_success`` is the sole success signal for the oracle. Words such as
  ``failure`` inside legitimate negative-case prose (for example an expected-message
  assertion) are never treated as a verdict, and a non-boolean value rejects the pass.

Release mode
------------

``validate(..., release=False)`` accepts draft matrices: rows may be incomplete and ``not_run``
while identity, unique IDs, vocabulary, exception shape, and surface references remain valid.
A ``pass`` row is still held to the oracle and binding rules in draft mode.

``validate(..., release=True)`` additionally requires a nonempty discovered surface inventory,
a nonempty case set with at least one case for every declared surface (or a source-grounded
``dispositions`` entry), every required case to be ``pass`` or an unexpired
``approved_exception`` with an approval receipt, compensating controls, and the exact candidate
commit, every pass to prove every required lane with candidate-matched ``junit``/manual
evidence, and every evidence path to stay under ``evidence_root`` as a non-empty regular file
whose sha256 matches. Each evidence path is containment-checked with
``Path.is_relative_to(evidence_root.resolve())`` before any file read or XML parse, so neither a
``..`` traversal nor a symlink escape can be opened.

Discovery input can be a surface list or a canonical discovery report. Report
diagnostics are preserved: an unresolved status, nonempty issues, malformed issue
list, or unsupported report schema blocks release validation.

An ``approved_exception`` stays in the total denominator and is excluded from the passing
numerator. An exception never waives a credential, quota, or provider gap automatically.

This module never executes tests, hooks, or commands found in a matrix; evidence checks read
bytes only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, TypeGuard
from xml.etree import ElementTree

from tools.release_acceptance import result_adapters

SCHEMA_VERSION = "acceptance-evidence.v1"
SUPPORTED_STATUSES = ("pass", "fail", "blocked", "not_run", "stale", "approved_exception")
SUPPORTED_APPLICABILITIES = ("required", "optional", "unsupported", "deferred")
SUPPORTED_LANES = ("hermetic", "integration", "release", "manual", "live")
AUTOMATED_LANES = ("hermetic", "integration", "release", "live")
MANUAL_LANE = "manual"
EVIDENCE_FORMATS = ("junit", "unittest-json", "text")
STRUCTURED_FORMATS = ("junit", "unittest-json")
JUNIT_RESULT_TAGS = ("failure", "error", "skipped", "rerun", "flaky")
RESOLVED_STATUSES = ("pass", "approved_exception")
BLOCKING_STATUSES = ("fail", "blocked", "not_run", "stale")

_SHA256_RE = re.compile(r"^(?:sha256:)?([0-9a-fA-F]{64})$")
_GIT_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SOURCE_REF_RE = re.compile(r"^[A-Za-z0-9_./-]+:\d+(?:-\d+)?$")


def _is_non_empty_str(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and bool(value.strip())


def _normalize_sha256(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _SHA256_RE.match(value.strip())
    return match.group(1).lower() if match else None


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_expiry(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return date.fromisoformat(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if _is_non_empty_str(item)]


def _is_source_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(_SOURCE_REF_RE.match(value.strip()))


# --------------------------------------------------------------------------------------
# JUnit structured provenance
# --------------------------------------------------------------------------------------


def _slug(value: str) -> str:
    return value.replace("::", ".").replace("/", ".").strip(".")


@dataclass(frozen=True)
class _JunitTest:
    slug: str
    classname: str
    name: str
    tag: str


def _junit_tests(path: Path) -> tuple[list[_JunitTest], list[str]]:
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError) as exc:
        return [], [f"cannot parse junit XML ({exc})"]
    if root.tag not in {"testsuite", "testsuites"}:
        return [], ["junit root must be testsuite or testsuites"]
    errors: list[str] = []
    for suite in root.iter():
        if suite.tag not in {"testsuite", "testsuites"}:
            continue
        cases = list(suite.iter("testcase"))
        counts = {
            "tests": len(cases),
            **{
                field: sum(any(child.tag == tag for child in case) for case in cases)
                for field, tag in (
                    ("failures", "failure"),
                    ("errors", "error"),
                    ("skipped", "skipped"),
                )
            },
        }
        for field, actual in counts.items():
            declared = suite.get(field)
            if declared is not None and (not declared.isdecimal() or int(declared) != actual):
                errors.append(f"junit {field} count {declared!r} differs from observed {actual}")
    tests: list[_JunitTest] = []
    for element in root.iter("testcase"):
        name = (element.get("name") or "").strip()
        if not name:
            continue
        classname = (element.get("classname") or "").strip()
        status = (element.get("status") or "").strip().lower()
        tag = "pass" if status in {"", "pass", "passed"} else status
        for child in element:
            if child.tag in JUNIT_RESULT_TAGS:
                tag = child.tag
                break
            if child.tag not in {"system-out", "system-err", "properties"}:
                tag = f"unrecognized-result:{child.tag}"
                break
        slug = _slug(f"{classname}.{name}" if classname else name)
        tests.append(_JunitTest(slug=slug, classname=classname, name=name, tag=tag))
    return tests, errors


def _normalize_repo_module(value: str) -> str:
    """Reduce a repository-relative path or dotted module to its canonical dotted name."""

    text = value.strip().strip("/")
    if text.endswith(".py"):
        text = text[:-3]
    return text.replace("/", ".")


def _node_parts(node_id: Any) -> tuple[str, list[str]] | None:
    if not _is_non_empty_str(node_id):
        return None
    parts = [part.strip() for part in node_id.split("::")]
    if len(parts) < 2 or any(not part for part in parts) or not parts[0].endswith(".py"):
        return None
    return parts[0], parts[1:]


def _node_matches(test: _JunitTest, file_part: str, tail: list[str]) -> bool:
    """Match a pytest node id against a JUnit testcase exactly.

    The JUnit ``classname`` must equal the node's repository-relative module plus every class
    qualifier of the node id (or the module alone when the node has no class qualifier), and
    ``name`` must equal the node's final component. A module-only ``classname`` never matches
    a class-method node, and a bare suffix such as ``test_health`` never matches
    ``tests/api/test_health.py``.
    """

    if not test.classname:
        return False
    module = _normalize_repo_module(file_part)
    expected_classname = ".".join([module, *tail[:-1]]) if len(tail) > 1 else module
    classname = test.classname.strip().strip(".")
    return classname == expected_classname and test.name == tail[-1]


def _resolved_evidence_path(raw_path: str, evidence_root: Path) -> Path | None:
    """Resolve a relative evidence path and require containment under ``evidence_root``.

    Resolution happens before any read or parse, so ``..`` traversal and symlink escapes are
    rejected without opening the target. Returns ``None`` when containment fails.
    """

    if Path(raw_path).is_absolute():
        return None
    root = evidence_root.resolve()
    resolved = (root / raw_path).resolve()
    if not resolved.is_relative_to(root):
        return None
    return resolved


def _junit_errors(
    case_id: str,
    case: dict[str, Any],
    entries: list[dict[str, Any]],
    evidence_root: Path | None,
    *,
    release: bool,
) -> list[str]:
    if not release or case.get("status") != "pass" or evidence_root is None:
        return []
    errors: list[str] = []
    junit_entries = [
        entry
        for entry in entries
        if entry.get("lane") in AUTOMATED_LANES and entry.get("format") == "junit"
    ]
    if not junit_entries:
        return []
    required_lanes = _string_list(case.get("required_lanes"))
    automated = [lane for lane in required_lanes if lane in AUTOMATED_LANES]
    per_lane = case.get("lane_test_node_ids")
    per_lane_mapping = per_lane if isinstance(per_lane, dict) else None
    lane_nodes: dict[str, list[str]] = {}
    target_lanes: list[str] = []
    if len(automated) > 1:
        if per_lane is None:
            errors.append(
                f"case {case_id}: pass requires lane_test_node_ids when multiple automated "
                f"lanes are required ({', '.join(automated)}); a single global test_node_ids "
                "list cannot prove separate lanes"
            )
        elif per_lane_mapping is None:
            errors.append(f"case {case_id}: lane_test_node_ids must be a mapping of lane to nodes")
        else:
            target_lanes = list(automated)
    elif automated:
        if per_lane_mapping is None:
            nodes = _string_list(case.get("test_node_ids"))
            if nodes:
                lane_nodes[automated[0]] = nodes
        else:
            target_lanes = [automated[0]]
    for lane in target_lanes:
        lane_id_nodes = per_lane_mapping.get(lane) if per_lane_mapping is not None else None
        if not isinstance(lane_id_nodes, list) or not all(
            _is_non_empty_str(node) for node in lane_id_nodes
        ):
            errors.append(
                f"case {case_id}: lane_test_node_ids requires a non-empty list of node "
                f"ids for automated lane {lane!r}"
            )
            continue
        if not lane_id_nodes:
            errors.append(f"case {case_id}: lane_test_node_ids[{lane!r}] must not be empty")
            continue
        lane_nodes[lane] = [node for node in lane_id_nodes if _is_non_empty_str(node)]
    if len(automated) <= 1:
        for lane in automated:
            if not lane_nodes.get(lane):
                errors.append(
                    f"case {case_id}: pass requires test_node_ids for automated lane {lane!r}"
                )
    if not lane_nodes:
        return errors
    parsed: dict[str, list[tuple[str, list[_JunitTest]]]] = {lane: [] for lane in lane_nodes}
    for entry in junit_entries:
        entry_lane = entry.get("lane")
        if not isinstance(entry_lane, str) or entry_lane not in lane_nodes:
            continue
        raw_path = entry.get("path")
        if not _is_non_empty_str(raw_path):
            continue
        resolved = _resolved_evidence_path(raw_path, evidence_root)
        if resolved is None:
            continue
        if not resolved.is_file():
            continue
        tests, parse_errors = _junit_tests(resolved)
        for parse_error in parse_errors:
            errors.append(f"case {case_id}: junit evidence {raw_path!r} {parse_error}")
        if not tests:
            errors.append(
                f"case {case_id}: junit evidence {raw_path!r} contains no testcase elements"
            )
            continue
        parsed[entry_lane].append((raw_path, tests))
    for lane in sorted(lane_nodes):
        lane_parsed = parsed[lane]
        files = ", ".join(raw_path for raw_path, _ in lane_parsed) or "the supplied junit evidence"
        for node in lane_nodes[lane]:
            parts = _node_parts(node)
            if parts is None:
                errors.append(
                    f"case {case_id}: lane {lane!r} test node {node!r} is not a pytest node id "
                    "(expected path/to/test_file.py::test_name)"
                )
                continue
            file_part, tail = parts
            matches = [
                (raw_path, test)
                for raw_path, tests in lane_parsed
                for test in tests
                if _node_matches(test, file_part, tail)
            ]
            if not matches:
                errors.append(
                    f"case {case_id}: lane {lane!r} test node {node!r} was not collected in "
                    f"{files}; a pass cannot cite an absent test"
                )
                continue
            if len(matches) != 1:
                errors.append(
                    f"case {case_id}: lane {lane!r} test node {node!r} was collected "
                    f"{len(matches)} times; exactly one collected result is required"
                )
            bad = next(((raw_path, test) for raw_path, test in matches if test.tag != "pass"), None)
            if bad is not None:
                bad_path, bad_test = bad
                errors.append(
                    f"case {case_id}: lane {lane!r} test node {node!r} result is {bad_test.tag!r} "
                    f"in {bad_path!r} (only a collected pass proves a pass)"
                )
    return errors


# --------------------------------------------------------------------------------------
# Evidence entries
# --------------------------------------------------------------------------------------


def _component_result_errors(
    case_id: str,
    case: dict[str, Any],
    entries: list[dict[str, Any]],
    evidence_root: Path | None,
    *,
    release: bool,
) -> list[str]:
    if not release or case.get("status") != "pass" or evidence_root is None:
        return []
    errors: list[str] = []
    automated = [
        lane for lane in _string_list(case.get("required_lanes")) if lane in AUTOMATED_LANES
    ]
    per_lane = case.get("lane_test_node_ids")
    for lane in automated:
        structured = [
            entry
            for entry in entries
            if entry.get("lane") == lane and entry.get("format") in STRUCTURED_FORMATS
        ]
        components = [entry for entry in structured if entry.get("format") == "unittest-json"]
        if not components:
            continue
        if len(structured) != 1:
            errors.append(
                f"case {case_id}: component lane {lane!r} requires exactly one structured report"
            )
            continue
        if isinstance(per_lane, dict):
            nodes = _string_list(per_lane.get(lane))
        elif per_lane is None and len(automated) == 1:
            nodes = _string_list(case.get("test_node_ids"))
        else:
            nodes = []
        if not nodes:
            errors.append(
                f"case {case_id}: component lane {lane!r} requires exact lane-bound test_node_ids"
            )
            continue
        entry = components[0]
        prefix = entry.get("project_prefix")
        if not _is_non_empty_str(prefix):
            errors.append(f"case {case_id}: component evidence requires project_prefix")
            continue
        raw_path = entry.get("path")
        if not _is_non_empty_str(raw_path):
            continue
        path = _resolved_evidence_path(raw_path, evidence_root)
        if path is None or not path.is_file():
            continue  # The ordinary evidence-path validation reports this.
        try:
            result_adapters.match_unittest_report(path, nodes, project_prefix=prefix)
        except result_adapters.ResultAdapterError as exc:
            errors.append(f"case {case_id}: component lane {lane!r}: {exc}")
    return errors


def _entry_scope_errors(
    case_id: str, entry: dict[str, Any], *, release: bool, lane: Any
) -> list[str]:
    errors: list[str] = []
    if not _is_non_empty_str(lane):
        errors.append(f"case {case_id}: evidence entry requires a lane")
    elif lane not in SUPPORTED_LANES:
        errors.append(f"case {case_id}: evidence lane {lane!r} is not supported")
    evidence_format = entry.get("format", "text")
    if evidence_format not in EVIDENCE_FORMATS:
        errors.append(
            f"case {case_id}: evidence format {evidence_format!r} must be one of "
            f"{', '.join(EVIDENCE_FORMATS)}"
        )
    if lane == MANUAL_LANE and evidence_format in STRUCTURED_FORMATS:
        errors.append(f"case {case_id}: manual lane evidence cannot be a junit run")
    exit_code = entry.get("exit_code")
    if exit_code is not None and (isinstance(exit_code, bool) or not isinstance(exit_code, int)):
        errors.append(f"case {case_id}: evidence exit_code must be an integer when present")
    if evidence_format in STRUCTURED_FORMATS:
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            errors.append(
                f"case {case_id}: junit evidence requires the run exit_code (0 for acceptance)"
            )
        elif exit_code != 0:
            errors.append(
                f"case {case_id}: junit evidence run exit_code must be 0, got {exit_code}"
            )
    entry_candidate = entry.get("candidate_id")
    if release and not _is_non_empty_str(entry_candidate):
        errors.append(f"case {case_id}: evidence entry requires a candidate_id")
    return errors


def _manual_entry_errors(case_id: str, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not _is_non_empty_str(entry.get("reviewer")):
        errors.append(f"case {case_id}: manual evidence requires a reviewer")
    observed = entry.get("observed_success")
    if observed is not True:
        errors.append(
            f"case {case_id}: manual evidence must record observed_success: true, got {observed!r}"
        )
    if _parse_timestamp(entry.get("timestamp")) is None:
        errors.append(f"case {case_id}: manual evidence requires a timezone-aware ISO timestamp")
    if not _string_list(entry.get("steps")):
        errors.append(f"case {case_id}: manual evidence requires non-empty steps")
    return errors


def _entry_errors(
    case_id: str,
    entry: dict[str, Any],
    candidate_id: str | None,
    candidate_digests: set[str],
    evidence_root: Path | None,
    *,
    release: bool,
) -> list[str]:
    lane = entry.get("lane")
    errors = _entry_scope_errors(case_id, entry, release=release, lane=lane)
    entry_candidate = entry.get("candidate_id")
    if _is_non_empty_str(entry_candidate) and entry_candidate != candidate_id:
        errors.append(
            f"case {case_id}: evidence candidate_id {entry_candidate!r} does not match "
            f"matrix candidate {candidate_id!r}"
        )
    artifact_digest = entry.get("artifact_digest")
    if artifact_digest is not None:
        normalized_artifact = _normalize_sha256(artifact_digest)
        if normalized_artifact is None:
            errors.append(f"case {case_id}: evidence artifact_digest must be a sha256 digest")
        elif normalized_artifact not in candidate_digests:
            errors.append(
                f"case {case_id}: evidence artifact_digest is not a declared candidate digest"
            )
    raw_path = entry.get("path")
    if not _is_non_empty_str(raw_path):
        errors.append(f"case {case_id}: evidence entry requires a path")
        return errors
    declared_digest = _normalize_sha256(entry.get("sha256"))
    if declared_digest is None and release:
        errors.append(f"case {case_id}: evidence sha256 must be a 64-character hex digest")
    if evidence_root is None:
        return errors
    if Path(raw_path).is_absolute():
        errors.append(
            f"case {case_id}: evidence path {raw_path!r} must be relative to evidence_root"
        )
        return errors
    root = evidence_root.resolve()
    path = (root / raw_path).resolve()
    if not path.is_relative_to(root):
        errors.append(f"case {case_id}: evidence path {raw_path!r} escapes evidence_root")
        return errors
    if not path.is_file():
        errors.append(
            f"case {case_id}: evidence file {raw_path!r} is missing or is not a regular file"
        )
        return errors
    if path.stat().st_size == 0:
        errors.append(f"case {case_id}: evidence file {raw_path!r} is empty")
        return errors
    if declared_digest is not None and _file_sha256(path) != declared_digest:
        errors.append(
            f"case {case_id}: evidence checksum mismatch for {raw_path!r} "
            f"(declared {declared_digest}, actual {_file_sha256(path)})"
        )
    return errors


# --------------------------------------------------------------------------------------
# Candidate, surfaces, dispositions
# --------------------------------------------------------------------------------------


def _candidate_digests(candidate: dict[str, Any]) -> set[str]:
    digests: set[str] = set()
    artifact_digests = candidate.get("artifact_digests")
    if isinstance(artifact_digests, dict):
        for value in artifact_digests.values():
            if isinstance(value, dict):
                for nested in value.values():
                    normalized = _normalize_sha256(nested)
                    if normalized is not None:
                        digests.add(normalized)
            else:
                normalized = _normalize_sha256(value)
                if normalized is not None:
                    digests.add(normalized)
    return digests


def _validate_candidate(
    matrix: dict[str, Any], errors: list[str], *, release: bool
) -> tuple[str | None, set[str], str | None]:
    candidate = matrix.get("candidate")
    if not isinstance(candidate, dict):
        errors.append("candidate must be a mapping with candidate_id and broker_commit")
        return None, set(), None
    candidate_id = candidate.get("candidate_id")
    if not _is_non_empty_str(candidate_id):
        errors.append("candidate.candidate_id must be a non-empty string")
        candidate_id = None
    broker_commit = candidate.get("broker_commit")
    commit_value = broker_commit if isinstance(broker_commit, str) else None
    if release:
        if not _GIT_SHA_RE.match(commit_value or ""):
            errors.append("candidate.broker_commit must be a 40-character git commit SHA")
    elif broker_commit is not None and commit_value is None:
        errors.append("candidate.broker_commit must be a 40-character git commit SHA when present")
    artifact_digests = candidate.get("artifact_digests")
    if artifact_digests is not None:
        if not isinstance(artifact_digests, dict):
            errors.append("candidate.artifact_digests must be a mapping when present")
        else:
            for name, value in artifact_digests.items():
                if isinstance(value, dict):
                    for nested_name, nested in value.items():
                        if _normalize_sha256(nested) is None:
                            errors.append(
                                f"candidate artifact digest {name}.{nested_name!r} "
                                "must be a sha256 digest"
                            )
                elif _normalize_sha256(value) is None:
                    errors.append(f"candidate artifact digest {name!r} must be a sha256 digest")
    return candidate_id, _candidate_digests(candidate), commit_value


def _validate_surfaces(matrix: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    surfaces = matrix.get("surfaces")
    if not isinstance(surfaces, list):
        errors.append("surfaces must be a list")
        return {}
    declared: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for index, surface in enumerate(surfaces):
        if not isinstance(surface, dict):
            errors.append(f"surfaces[{index}] must be a mapping")
            continue
        surface_id = surface.get("surface_id")
        if not _is_non_empty_str(surface_id):
            errors.append(f"surfaces[{index}].surface_id must be a non-empty string")
            continue
        if surface_id in seen:
            errors.append(f"duplicate surface_id {surface_id!r}")
            continue
        seen.add(surface_id)
        if not _is_non_empty_str(surface.get("surface_kind")):
            errors.append(f"surface {surface_id}: surface_kind must be a non-empty string")
        sources = surface.get("declared_source")
        if (
            not isinstance(sources, list)
            or not sources
            or not all(_is_source_ref(item) for item in sources)
        ):
            errors.append(
                f"surface {surface_id}: declared_source must be a non-empty list of "
                "file:line source references"
            )
        declared[surface_id] = surface
    return declared


def _disposition_errors(
    matrix: dict[str, Any],
    declared: dict[str, dict[str, Any]],
    errors: list[str],
    *,
    release: bool,
) -> set[str]:
    if not release:
        return set()
    dispositions = matrix.get("dispositions")
    if dispositions is None:
        return set()
    if not isinstance(dispositions, list):
        errors.append("dispositions must be a list when present")
        return set()
    exempt: set[str] = set()
    allowed = set(declared) | {"*"}
    for index, item in enumerate(dispositions):
        if not isinstance(item, dict):
            errors.append(f"dispositions[{index}] must be a mapping")
            continue
        scope = item.get("scope")
        if not _is_non_empty_str(scope) or scope not in allowed:
            errors.append(
                f"dispositions[{index}].scope must be the surface_id of a declared surface or '*'"
            )
            continue
        if not _is_non_empty_str(item.get("reason")):
            errors.append(f"dispositions[{index}].reason must be a non-empty string")
        if not _is_source_ref(item.get("source")):
            errors.append(f"dispositions[{index}].source must be a file:line source reference")
        approved_by = item.get("approved_by")
        if approved_by is not None:
            if not _is_non_empty_str(approved_by):
                errors.append(
                    f"dispositions[{index}].approved_by must be a non-empty string when present"
                )
            if not _is_non_empty_str(item.get("approval_receipt")):
                errors.append(
                    f"dispositions[{index}]: an approved disposition requires an "
                    "approval_receipt reference"
                )
        exempt.add(scope)
    return exempt


# --------------------------------------------------------------------------------------
# Case validation
# --------------------------------------------------------------------------------------


def _claim_binding_errors(
    case_id: str, case: dict[str, Any], required_lanes: list[str]
) -> list[str]:
    errors: list[str] = []
    node_ids = _string_list(case.get("test_node_ids"))
    manual_steps = _string_list(case.get("manual_steps"))
    automated = [lane for lane in required_lanes if lane in AUTOMATED_LANES]
    per_lane = case.get("lane_test_node_ids")
    per_lane_mapping = per_lane if isinstance(per_lane, dict) else None
    if len(automated) > 1:
        if per_lane_mapping is None:
            errors.append(
                f"case {case_id}: pass requires lane_test_node_ids when multiple automated "
                f"lanes are required ({', '.join(automated)}); global test_node_ids cannot "
                "prove separate lanes"
            )
        else:
            for lane in automated:
                nodes = per_lane_mapping.get(lane)
                if not (
                    isinstance(nodes, list)
                    and nodes
                    and all(_is_non_empty_str(node) for node in nodes)
                ):
                    errors.append(
                        f"case {case_id}: pass requires lane_test_node_ids[{lane!r}] for "
                        "automated lane"
                    )
    elif automated and per_lane_mapping is None and not node_ids:
        errors.append(
            f"case {case_id}: pass requires test_node_ids for automated lane(s) "
            f"{', '.join(automated)}"
        )
    elif automated and per_lane_mapping is not None:
        nodes = per_lane_mapping.get(automated[0])
        if not (
            isinstance(nodes, list) and nodes and all(_is_non_empty_str(node) for node in nodes)
        ):
            errors.append(
                f"case {case_id}: pass requires lane_test_node_ids[{automated[0]!r}] for "
                "automated lane"
            )
    if MANUAL_LANE in required_lanes and not manual_steps:
        errors.append(f"case {case_id}: pass requires manual_steps for the manual lane")
    if node_ids and not automated:
        errors.append(f"case {case_id}: test_node_ids require an automated lane in required_lanes")
    if per_lane_mapping is not None and not automated:
        errors.append(
            f"case {case_id}: lane_test_node_ids require automated lanes in required_lanes"
        )
    elif per_lane_mapping is not None:
        for lane in per_lane_mapping:
            if lane not in automated:
                errors.append(
                    f"case {case_id}: lane_test_node_ids has lane {lane!r} that is not a "
                    "required automated lane"
                )
    return errors


def _oracle_errors(case_id: str, case: dict[str, Any]) -> list[str]:
    oracle = case.get("oracle")
    if not isinstance(oracle, dict):
        return [f"case {case_id}: pass requires an oracle mapping with expected and actual"]
    errors: list[str] = []
    if not _is_non_empty_str(oracle.get("expected")):
        errors.append(f"case {case_id}: pass requires oracle.expected")
    if not _is_non_empty_str(oracle.get("actual")):
        errors.append(f"case {case_id}: pass requires oracle.actual")
    observed = oracle.get("observed_success")
    if not isinstance(observed, bool):
        errors.append(
            f"case {case_id}: pass requires oracle.observed_success to be a boolean "
            f"(got {observed!r}); prose is never a verdict"
        )
    elif not observed:
        errors.append(
            f"case {case_id}: oracle.observed_success is false; a recorded negative result "
            "cannot be claimed as a pass"
        )
    return errors


def _lane_evidence_errors(
    case_id: str,
    case: dict[str, Any],
    candidate_id: str | None,
    entries: list[dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    for lane in _string_list(case.get("required_lanes")):
        lane_entries = [entry for entry in entries if entry.get("lane") == lane]
        candidate_matched = [
            entry for entry in lane_entries if entry.get("candidate_id") == candidate_id
        ]
        if not candidate_matched:
            errors.append(
                f"case {case_id}: pass requires candidate-matching evidence for lane {lane!r}"
            )
            continue
        if lane in AUTOMATED_LANES and not any(
            entry.get("format") in STRUCTURED_FORMATS for entry in candidate_matched
        ):
            errors.append(
                f"case {case_id}: automated lane {lane!r} requires a junit evidence entry; "
                "arbitrary text cannot prove a test result"
            )
    return errors


def _exception_errors(
    case_id: str, case: dict[str, Any], surface_id: Any, *, release: bool
) -> list[str]:
    exception = case.get("exception")
    status = case.get("status")
    if exception is None:
        if status == "approved_exception":
            return [f"case {case_id}: approved_exception requires an exception record"]
        return []
    if not isinstance(exception, dict):
        return [f"case {case_id}: exception must be a mapping"]
    if status != "approved_exception":
        return [f"case {case_id}: exception is only valid when status is 'approved_exception'"]
    errors: list[str] = []
    for field in ("owner", "reason", "risk"):
        if not _is_non_empty_str(exception.get(field)):
            errors.append(f"case {case_id}: exception.{field} must be a non-empty string")
    allowed = {case_id, "*"}
    if _is_non_empty_str(surface_id):
        allowed.add(surface_id)
    scenario_id = case.get("scenario_id")
    if _is_non_empty_str(scenario_id):
        allowed.add(scenario_id)
    scope = exception.get("scope")
    if isinstance(scope, str):
        if not scope.strip():
            errors.append(f"case {case_id}: exception.scope must not be empty")
        elif scope != "*" and scope not in allowed:
            errors.append(
                f"case {case_id}: exception.scope must name the case, surface, or scenario "
                f"(got {scope!r})"
            )
    elif isinstance(scope, list):
        if not scope or not all(_is_non_empty_str(item) for item in scope):
            errors.append(f"case {case_id}: exception.scope must be a non-empty list of strings")
        elif any(item != "*" and item not in allowed for item in scope):
            errors.append(
                f"case {case_id}: exception.scope entries must name the case, surface, or scenario"
            )
    else:
        errors.append(f"case {case_id}: exception.scope must be a string or a list of strings")
    if exception.get("approved") is not True:
        errors.append(f"case {case_id}: exception.approved must be true")
    expires = _parse_expiry(exception.get("expires"))
    if expires is None:
        errors.append(f"case {case_id}: exception.expires must be an ISO date or datetime")
    elif expires < date.today():
        errors.append(f"case {case_id}: exception expired on {expires.isoformat()}")
    controls = exception.get("compensating_controls")
    if controls is not None and not _is_non_empty_str(controls):
        errors.append(f"case {case_id}: exception.compensating_controls must be a non-empty string")
    if release:
        receipt = exception.get("approval_receipt")
        if not _is_non_empty_str(receipt):
            errors.append(f"case {case_id}: exception requires an approval_receipt reference")
        if not _is_non_empty_str(controls):
            errors.append(f"case {case_id}: approved exception requires compensating_controls")
        resolved = exception.get("resolved_commit")
        if not isinstance(resolved, str) or not _GIT_SHA_RE.match(resolved):
            errors.append(
                f"case {case_id}: exception.resolved_commit must be a 40-character git "
                "commit SHA so the exception cannot be inherited by a different candidate"
            )
    return errors


def _required_status_errors(case_id: str, case: dict[str, Any]) -> list[str]:
    if case.get("applicability") != "required":
        return []
    if case.get("status") in RESOLVED_STATUSES:
        return []
    return [
        f"case {case_id}: required case status {case.get('status')!r} is release-blocking "
        "(only pass or an unexpired approved_exception resolves a required case)"
    ]


def _resolve_exception_commit(
    case_id: str, case: dict[str, Any], broker_commit: str | None
) -> list[str]:
    if case.get("status") != "approved_exception":
        return []
    exception = case.get("exception")
    if not isinstance(exception, dict):
        return []
    resolved = exception.get("resolved_commit")
    if not isinstance(resolved, str) or not _GIT_SHA_RE.match(resolved):
        return []
    if resolved != broker_commit:
        return [
            f"case {case_id}: exception is stale: resolved_commit {resolved!r} does not match "
            f"candidate.broker_commit {broker_commit!r}; re-approve for this candidate"
        ]
    return []


def _validate_case(
    case: dict[str, Any],
    index: int,
    *,
    declared_surfaces: dict[str, dict[str, Any]],
    case_ids: set[str],
    candidate_id: str | None,
    candidate_digests: set[str],
    broker_commit: str | None,
    evidence_root: Path | None,
    release: bool,
    errors: list[str],
) -> None:
    label = f"cases[{index}]"
    raw_case_id = case.get("case_id")
    case_id: str = label
    if not _is_non_empty_str(raw_case_id):
        errors.append(f"{label}.case_id must be a non-empty string")
    else:
        case_id = raw_case_id
        if case_id in case_ids:
            errors.append(f"duplicate case_id {case_id!r}")
        else:
            case_ids.add(case_id)
    surface_id = case.get("surface_id")
    if not _is_non_empty_str(surface_id):
        errors.append(f"case {case_id}: surface_id must be a non-empty string")
    elif surface_id not in declared_surfaces:
        errors.append(f"case {case_id}: surface_id {surface_id!r} is not a declared surface")
    scenario_id = case.get("scenario_id")
    if not _is_non_empty_str(scenario_id):
        errors.append(f"case {case_id}: scenario_id must be a non-empty string")
    journey_id = case.get("journey_id")
    if journey_id is not None and not _is_non_empty_str(journey_id):
        errors.append(f"case {case_id}: journey_id must be null or a non-empty string")
    applicability = case.get("applicability")
    if applicability not in SUPPORTED_APPLICABILITIES:
        errors.append(
            f"case {case_id}: applicability {applicability!r} is not one of "
            f"{', '.join(SUPPORTED_APPLICABILITIES)}"
        )
    required_lanes = case.get("required_lanes")
    if not isinstance(required_lanes, list):
        errors.append(f"case {case_id}: required_lanes must be a list")
        required_lanes = []
    else:
        for lane in required_lanes:
            if not _is_non_empty_str(lane) or lane not in SUPPORTED_LANES:
                errors.append(f"case {case_id}: required lane {lane!r} is not supported")
        if len({lane for lane in required_lanes if isinstance(lane, str)}) != len(required_lanes):
            errors.append(f"case {case_id}: required_lanes contains duplicates")
    for field in ("test_node_ids", "manual_steps"):
        value = case.get(field)
        if value is not None and not (
            isinstance(value, list) and all(_is_non_empty_str(item) for item in value)
        ):
            errors.append(f"case {case_id}: {field} must be a list of non-empty strings")
    lane_node_ids = case.get("lane_test_node_ids")
    if lane_node_ids is not None and not isinstance(lane_node_ids, dict):
        errors.append(f"case {case_id}: lane_test_node_ids must be a mapping when present")
    elif isinstance(lane_node_ids, dict):
        for lane, nodes in lane_node_ids.items():
            if lane not in AUTOMATED_LANES:
                errors.append(
                    f"case {case_id}: lane_test_node_ids has unsupported automated lane {lane!r}"
                )
            if not (isinstance(nodes, list) and all(_is_non_empty_str(node) for node in nodes)):
                errors.append(
                    f"case {case_id}: lane_test_node_ids[{lane!r}] must be a list of "
                    "non-empty strings"
                )
    oracle = case.get("oracle")
    if oracle is not None and not isinstance(oracle, dict):
        errors.append(f"case {case_id}: oracle must be a mapping when present")
    status = case.get("status")
    if status not in SUPPORTED_STATUSES:
        if _is_non_empty_str(status) and status.lower() in {
            "skip",
            "skipped",
            "xfail",
            "deselected",
        }:
            errors.append(
                f"case {case_id}: status {status!r} is not a valid result; "
                "skip/xfail/deselection never pass and must be recorded as fail/not_run"
            )
        else:
            errors.append(
                f"case {case_id}: status {status!r} is not one of {', '.join(SUPPORTED_STATUSES)}"
            )
    lane_results = case.get("lane_results")
    if lane_results is not None and not isinstance(lane_results, dict):
        errors.append(f"case {case_id}: lane_results must be a mapping when present")
    elif isinstance(lane_results, dict):
        declared_lanes = {lane for lane in required_lanes if isinstance(lane, str)}
        for lane, result in lane_results.items():
            if lane not in SUPPORTED_LANES:
                errors.append(f"case {case_id}: lane_results has unsupported lane {lane!r}")
                continue
            if lane not in declared_lanes:
                errors.append(
                    f"case {case_id}: lane_results[{lane!r}] is not in required_lanes; "
                    "record optional lane attempts outside the claimed pass"
                )
            if result not in SUPPORTED_STATUSES:
                errors.append(
                    f"case {case_id}: lane_results[{lane!r}] {result!r} is not a supported status"
                )
    evidence = case.get("evidence")
    entries: list[dict[str, Any]] = []
    if evidence is not None and not isinstance(evidence, list):
        errors.append(f"case {case_id}: evidence must be a list when present")
    elif isinstance(evidence, list):
        if evidence and release and evidence_root is None:
            errors.append(f"case {case_id}: evidence_root is required to validate release evidence")
        for entry in evidence:
            if not isinstance(entry, dict):
                errors.append(f"case {case_id}: evidence entries must be mappings")
                continue
            entries.append(entry)
            errors.extend(
                _entry_errors(
                    case_id,
                    entry,
                    candidate_id,
                    candidate_digests,
                    evidence_root,
                    release=release,
                )
            )
    if status == "pass":
        if isinstance(lane_results, dict):
            for lane, result in lane_results.items():
                if result != "pass":
                    errors.append(
                        f"case {case_id}: contradictory result: top-level 'pass' with "
                        f"lane_results[{lane!r}] = {result!r}"
                    )
        errors.extend(_oracle_errors(case_id, case))
        errors.extend(
            _claim_binding_errors(
                case_id, case, [lane for lane in required_lanes if isinstance(lane, str)]
            )
        )
        if release:
            errors.extend(_lane_evidence_errors(case_id, case, candidate_id, entries))
        for entry in entries:
            if entry.get("lane") == MANUAL_LANE:
                errors.extend(_manual_entry_errors(case_id, entry))
        errors.extend(_junit_errors(case_id, case, entries, evidence_root, release=release))
        errors.extend(
            _component_result_errors(case_id, case, entries, evidence_root, release=release)
        )
    errors.extend(_exception_errors(case_id, case, surface_id, release=release))
    if release:
        errors.extend(_required_status_errors(case_id, case))
        errors.extend(_resolve_exception_commit(case_id, case, broker_commit))


def _inventory_errors(
    inventory: Any,
    declared_surfaces: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    if isinstance(inventory, dict):
        # Preserve discovery diagnostics rather than projecting away everything
        # except rows. A complete-looking subset cannot establish closure.
        if "schema_version" in inventory:
            if inventory.get("schema_version") != "release-acceptance-discovery.v1":
                errors.append("discovery report has an unsupported schema_version")
            if inventory.get("status") != "complete":
                errors.append("discovery report status must be complete for release validation")
            if not isinstance(inventory.get("issues"), list):
                errors.append("discovery report issues must be a list")
        if inventory.get("issues"):
            errors.append("discovery report contains unresolved issues")
        if inventory.get("status") not in (None, "complete"):
            errors.append("discovery report remains unresolved")
        inventory = inventory.get("surfaces")
    if not isinstance(inventory, list):
        return errors + ["inventory must be a list for release validation"]
    discovered: set[str] = set()
    if not inventory:
        errors.append(
            "discovery inventory is empty; release validation requires discovered surfaces"
        )
    for index, entry in enumerate(inventory):
        if not isinstance(entry, dict):
            errors.append(f"inventory[{index}] must be a mapping")
            continue
        surface_id = entry.get("surface_id")
        if not _is_non_empty_str(surface_id):
            errors.append(f"inventory[{index}].surface_id must be a non-empty string")
            continue
        if surface_id in discovered:
            errors.append(f"inventory contains duplicate surface_id {surface_id!r}")
            continue
        discovered.add(surface_id)
    for surface_id in sorted(discovered - set(declared_surfaces)):
        errors.append(
            f"inventory surface {surface_id!r} is discovered but not declared "
            "in matrix surfaces (unmapped discovered surface)"
        )
    for surface_id in sorted(set(declared_surfaces) - discovered):
        errors.append(
            f"declared surface {surface_id!r} is not present in the discovery inventory "
            "(removed or stale declaration)"
        )
    return errors


def _surface_coverage_errors(
    matrix: dict[str, Any],
    declared: dict[str, dict[str, Any]],
    exempt: set[str],
    errors: list[str],
) -> None:
    cases = matrix.get("cases")
    case_list: list[Any] = cases if isinstance(cases, list) else []
    if not case_list:
        errors.append("release requires at least one case; an empty case set cannot be accepted")
    covered: set[str] = set()
    for case in case_list:
        if not isinstance(case, dict):
            continue
        surface_id = case.get("surface_id")
        if (
            _is_non_empty_str(surface_id)
            and surface_id in declared
            and case.get("applicability") == "required"
        ):
            covered.add(surface_id)
    if "*" in exempt:
        return
    for surface_id in sorted(set(declared) - covered - exempt):
        errors.append(
            f"surface {surface_id!r}: release requires at least one case for this surface; "
            "a required case or source-grounded scoped disposition must resolve coverage"
        )


def validate(
    matrix: dict[str, Any],
    inventory: list[dict[str, Any]] | Any,
    *,
    release: bool = False,
    evidence_root: Path | None = None,
) -> list[str]:
    """Validate an acceptance matrix and return deterministic error messages.

    Draft mode (``release=False``) accepts incomplete ``not_run`` rows. Release mode
    additionally enforces discovery closure, nonempty surfaces/cases, a required case
    or validated disposition for each discovered surface, required-case
    resolution, structured ``junit`` or reviewer-observed pass provenance, and evidence
    bytes under ``evidence_root``.
    """

    if not isinstance(matrix, dict):
        return ["matrix must be a mapping"]
    errors: list[str] = []
    if matrix.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION!r}, got {matrix.get('schema_version')!r}"
        )
    candidate_id, candidate_digests, broker_commit = _validate_candidate(
        matrix, errors, release=release
    )
    declared_surfaces = _validate_surfaces(matrix, errors)
    exempt = _disposition_errors(matrix, declared_surfaces, errors, release=release)
    cases = matrix.get("cases")
    if not isinstance(cases, list):
        errors.append("cases must be a list")
        cases = []
    case_ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f"cases[{index}] must be a mapping")
            continue
        _validate_case(
            case,
            index,
            declared_surfaces=declared_surfaces,
            case_ids=case_ids,
            candidate_id=candidate_id,
            candidate_digests=candidate_digests,
            broker_commit=broker_commit,
            evidence_root=evidence_root if isinstance(evidence_root, Path) else None,
            release=release,
            errors=errors,
        )
    if release:
        if not declared_surfaces:
            errors.append("release requires at least one declared surface")
        errors.extend(_inventory_errors(inventory, declared_surfaces))
        _surface_coverage_errors(matrix, declared_surfaces, exempt, errors)
    return errors


def summarize(matrix: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic counts for a matrix without performing validation."""

    cases = matrix.get("cases")
    case_list: list[Any] = cases if isinstance(cases, list) else []
    surfaces = matrix.get("surfaces")
    surface_list: list[Any] = surfaces if isinstance(surfaces, list) else []
    status_counts = dict.fromkeys(SUPPORTED_STATUSES, 0)
    lane_counts = dict.fromkeys(SUPPORTED_LANES, 0)
    required_total = 0
    required_passing = 0
    exceptions_accepted = 0
    for case in case_list:
        if not isinstance(case, dict):
            continue
        status = case.get("status")
        if status in status_counts:
            status_counts[status] += 1
        if case.get("applicability") == "required":
            required_total += 1
            if status == "pass":
                required_passing += 1
            elif status == "approved_exception":
                exceptions_accepted += 1
        lane_results = case.get("lane_results")
        if isinstance(lane_results, dict):
            for lane, result in lane_results.items():
                if lane in lane_counts and result in {"pass", "fail", "blocked"}:
                    lane_counts[lane] += 1
    blockers = sum(status_counts[status] for status in BLOCKING_STATUSES)
    return {
        "schema_version": matrix.get("schema_version"),
        "candidate_id": (
            matrix.get("candidate", {}).get("candidate_id")
            if isinstance(matrix.get("candidate"), dict)
            else None
        ),
        "surface_total": len(surface_list),
        "case_total": len(case_list),
        "required_total": required_total,
        "required_passing": required_passing,
        "exceptions_accepted": exceptions_accepted,
        "blocking_total": blockers,
        "status_counts": status_counts,
        "lane_attempt_counts": lane_counts,
    }


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "-"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _overall(summary: dict[str, Any]) -> str:
    if summary["surface_total"] == 0 or summary["case_total"] == 0:
        return "not ready (empty dataset: no release readiness can be claimed)"
    if summary["blocking_total"] > 0:
        return "not ready"
    if summary["required_total"] == 0:
        return "not ready (no required cases recorded)"
    if summary["exceptions_accepted"] > 0:
        return "not ready for an unconditional claim; accepted exceptions remain (see counts)"
    if summary["required_passing"] < summary["required_total"]:
        return "not ready (required cases unresolved)"
    return (
        "all required rows recorded as pass; this is a status claim from unchecked JSON only, "
        "and evidence validation is still required"
    )


def render(matrix: dict[str, Any]) -> str:
    """Render a deterministic Markdown matrix that never implies full product proof.

    Rendering never calls :func:`validate` and never checks evidence bytes, so positive
    wording can only report claimed statuses; it never asserts that the candidate is ready.
    """

    summary = summarize(matrix)
    raw_candidate = matrix.get("candidate")
    candidate: dict[str, Any] = raw_candidate if isinstance(raw_candidate, dict) else {}
    candidate_id = summary["candidate_id"] or "unidentified"
    lines: list[str] = []
    lines.append("# Release acceptance matrix")
    lines.append("")
    lines.append(
        "> NOT RELEASE PROOF: this matrix records candidate-scoped acceptance evidence only. "
        "It does not prove the full product, is not a publication approval, and any blocker "
        "listed below means the candidate is not ready."
    )
    lines.append("")
    lines.append(f"- Overall: {_overall(summary)}")
    lines.append(f"- Candidate: `{candidate_id}`")
    lines.append(f"- Broker commit: `{_cell(candidate.get('broker_commit'))}`")
    lines.append(f"- Schema version: `{_cell(summary['schema_version'])}`")
    lines.append("")
    lines.append("## Summary counts")
    lines.append("")
    lines.append(f"- Declared surfaces: {summary['surface_total']}")
    lines.append(f"- Cases: {summary['case_total']}")
    lines.append(
        f"- Required cases: {summary['required_total']} "
        f"({summary['required_passing']} passing, "
        f"{summary['exceptions_accepted']} accepted exceptions retained in the denominator)"
    )
    lines.append(f"- Blocking rows (fail/blocked/not_run/stale): {summary['blocking_total']}")
    lines.append("")
    lines.append("### Status counts")
    lines.append("")
    lines.append("| Status | Count |")
    lines.append("| --- | --- |")
    for status in SUPPORTED_STATUSES:
        lines.append(f"| {status} | {summary['status_counts'][status]} |")
    lines.append("")
    lines.append("## Cases")
    lines.append("")
    lines.append(
        "| Case | Surface | Scenario | Applicability | Required lanes | Status | "
        "Oracle (expected vs actual) | Evidence | Exceptions |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    cases = matrix.get("cases")
    case_list: list[Any] = cases if isinstance(cases, list) else []
    for case in case_list:
        if not isinstance(case, dict):
            continue
        raw_oracle = case.get("oracle")
        oracle: dict[str, Any] = raw_oracle if isinstance(raw_oracle, dict) else {}
        raw_evidence = case.get("evidence")
        evidence: list[Any] = raw_evidence if isinstance(raw_evidence, list) else []
        evidence_cells = [
            f"{entry.get('format', 'text')}:{entry.get('path')}"
            for entry in evidence
            if isinstance(entry, dict) and entry.get("path")
        ]
        exception = case.get("exception")
        exception_cell = (
            "expires " + str(exception.get("expires")) if isinstance(exception, dict) else "-"
        )
        observed = oracle.get("observed_success")
        oracle_cell = f"{_cell(oracle.get('expected'))} vs {_cell(oracle.get('actual'))}"
        if observed is not True:
            oracle_cell += f" [observed_success={observed!r}]"
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(case.get("case_id")),
                    _cell(case.get("surface_id")),
                    _cell(case.get("scenario_id")),
                    _cell(case.get("applicability")),
                    _cell(case.get("required_lanes")),
                    _cell(case.get("status")),
                    oracle_cell,
                    _cell(evidence_cells),
                    _cell(exception_cell),
                ]
            )
            + " |"
        )
    blockers: list[str] = []
    for case in case_list:
        if not isinstance(case, dict):
            continue
        case_status = case.get("status")
        if case_status in BLOCKING_STATUSES:
            blockers.append(f"- `{case.get('case_id')}` ({case.get('surface_id')}): {case_status}")
    lines.append("")
    lines.append("## Blockers")
    lines.append("")
    if blockers:
        lines.append("The candidate is not ready. Open blocking rows:")
        lines.append("")
        lines.extend(sorted(blockers))
    else:
        lines.append(
            "No fail/blocked/not_run/stale rows recorded. This still does not prove the full "
            "product; verify declarations, lanes, and cleanup separately."
        )
    lines.append("")
    return "\n".join(lines) + "\n"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="evidence",
        description="Validate a release-acceptance matrix and render its Markdown report.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate", help="Validate a matrix against a schema")
    validate_parser.add_argument("matrix", type=Path)
    validate_parser.add_argument(
        "--inventory",
        type=Path,
        help="Discovery inventory JSON used for bidirectional closure in release mode.",
    )
    validate_parser.add_argument(
        "--release",
        action="store_true",
        help="Apply release-mode gates instead of draft validation.",
    )
    validate_parser.add_argument(
        "--evidence-root",
        type=Path,
        help="Root directory for evidence path and checksum checks.",
    )
    report_parser = subparsers.add_parser("report", help="Render a matrix to Markdown")
    report_parser.add_argument("matrix", type=Path)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    try:
        if args.command == "report":
            sys.stdout.write(render(_load_json(args.matrix)))
            return 0
        matrix = _load_json(args.matrix)
        inventory: Any = []
        if args.inventory is not None:
            inventory = _load_json(args.inventory)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"acceptance-error: cannot read input: {exc}", file=sys.stderr)
        return 1
    errors = validate(
        matrix,
        inventory,
        release=bool(args.release),
        evidence_root=args.evidence_root,
    )
    for error in errors:
        print(f"acceptance-error: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
