"""Adversarial tests for the release-acceptance matrix validator and renderer.

These tests prove that a false pass cannot be recorded. Release mode rejects: empty
surfaces/cases/inventory, a declared surface with no cases and no scoped disposition, a pass
citing a test node that a JUnit run never collected, a collected failure/error/skipped result,
a non-zero or missing run exit code, prose text substituting for a JUnit result, oracle
``observed_success`` that is false or non-boolean, unbound pass claims, cross-lane
substitution (one lane's JUnit run proving another lane, or multiple automated lanes sharing a
single global ``test_node_ids`` list), suffix-only JUnit classname matching, missing or corrupt
evidence, candidate mismatch, path traversal, symlink escape, unresolved required statuses,
expired or unreceipted exceptions, and unmapped/removed surfaces. Draft ``not_run`` rows
validate. The Markdown report is deterministic and never claims release readiness from an empty
dataset.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.release_acceptance import evidence

CANDIDATE_ID = "rc-20260920-1"
COMMIT = "a" * 40
OTHER_COMMIT = "b" * 40
WHEEL_DIGEST = "sha256:" + "1" * 64
NODE_ID = "tests/api/test_health.py::test_health"
INTEGRATION_NODE_ID = "tests/integration/test_health.py::test_health"
JUNIT_PATH = "runs/run-1/junit.xml"
INTEGRATION_JUNIT_PATH = "runs/run-1/integration-junit.xml"


def _write_bytes(root: Path, relative: str, content: bytes) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def _write_evidence(root: Path, relative: str, content: bytes = b"HTTP 200 OK\n") -> str:
    return _write_bytes(root, relative, content)


HERMETIC_CLASSNAME = "tests.api.test_health"
INTEGRATION_CLASSNAME = "tests.integration.test_health"


def _junit_xml(
    cases: list[tuple[str, str, str | None]] | None = None,
) -> bytes:
    rows = cases or [(HERMETIC_CLASSNAME, "test_health", None)]
    body = "".join(
        f'<testcase classname="{classname}" name="{name}" time="0.01">'
        + (f'<{tag} message="recorded">FAILED test_a\n</{tag}>' if tag else "")
        + "</testcase>"
        for classname, name, tag in rows
    )
    return f'<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite>{body}</testsuite></testsuites>'.encode()


def _write_junit(
    root: Path,
    cases: list[tuple[str, str, str | None]] | None = None,
    relative: str = JUNIT_PATH,
) -> str:
    return _write_bytes(root, relative, _junit_xml(cases))


def _junit_entry(sha: str, **overrides: object) -> dict:
    entry: dict = {
        "lane": "hermetic",
        "format": "junit",
        "path": JUNIT_PATH,
        "sha256": sha,
        "candidate_id": CANDIDATE_ID,
        "exit_code": 0,
    }
    entry.update(overrides)
    return entry


def _integration_junit_entry(sha: str, **overrides: object) -> dict:
    entry = _junit_entry(
        sha,
        lane="integration",
        path=INTEGRATION_JUNIT_PATH,
    )
    entry.update(overrides)
    return entry


def _manual_entry(sha: str, **overrides: object) -> dict:
    entry: dict = {
        "lane": "manual",
        "format": "text",
        "path": "runs/run-1/health-manual.txt",
        "sha256": sha,
        "candidate_id": CANDIDATE_ID,
        "reviewer": "release-owner",
        "observed_success": True,
        "timestamp": "2026-09-20T12:00:00Z",
        "steps": ["open /health and confirm HTTP 200"],
    }
    entry.update(overrides)
    return entry


def _health_oracle(**overrides: object) -> dict:
    oracle: dict = {"expected": "HTTP 200", "actual": "HTTP 200", "observed_success": True}
    oracle.update(overrides)
    return oracle


def _pass_case(**overrides: object) -> dict:
    case: dict = {
        "case_id": "REST-HEALTH-01",
        "surface_id": "rest:GET:/health",
        "scenario_id": "linux-wheel-loopback-noauth",
        "journey_id": None,
        "applicability": "required",
        "required_lanes": ["hermetic"],
        "test_node_ids": [NODE_ID],
        "manual_steps": [],
        "oracle": _health_oracle(),
        "status": "pass",
        "lane_results": {"hermetic": "pass"},
        "evidence": [_junit_entry("0" * 64)],
        "exception": None,
    }
    case.update(overrides)
    return case


def _surface(surface_id: str = "rest:GET:/health", **overrides: object) -> dict:
    surface: dict = {
        "surface_id": surface_id,
        "surface_kind": "rest",
        "declared_source": ["docs/support-matrix.md:12"],
    }
    surface.update(overrides)
    return surface


def _matrix(case: dict | None = None, **overrides: object) -> dict:
    matrix: dict = {
        "schema_version": evidence.SCHEMA_VERSION,
        "candidate": {
            "candidate_id": CANDIDATE_ID,
            "broker_commit": COMMIT,
            "artifact_digests": {"wheel": WHEEL_DIGEST},
        },
        "surfaces": [_surface()],
        "cases": [case or _pass_case()],
    }
    matrix.update(overrides)
    return matrix


def _release_case(tmp_path: Path, **overrides: object) -> tuple[dict, list[dict], Path]:
    digest = _write_junit(tmp_path)
    case = _pass_case()
    case["evidence"] = [_junit_entry(digest)]
    case.update(overrides)
    return _matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path


def _valid_release_matrix(tmp_path: Path) -> tuple[dict, list[dict], Path]:
    return _release_case(tmp_path)


def _run_release(matrix: dict, inventory: list[dict], root: Path | None) -> list[str]:
    return evidence.validate(matrix, inventory, release=True, evidence_root=root)


# ---------------------------------------------------------------------------------
# Accepted green paths
# ---------------------------------------------------------------------------------


def test_release_accepts_complete_candidate_matched_junit_evidence(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    assert _run_release(matrix, inventory, root) == []


def test_release_accepts_manual_lane_with_reviewer_observation(tmp_path: Path) -> None:
    digest = _write_evidence(tmp_path, "runs/run-1/health-manual.txt")
    case = _pass_case(
        required_lanes=["manual"],
        test_node_ids=[],
        manual_steps=["open /health and confirm HTTP 200"],
        lane_results={"manual": "pass"},
        evidence=[_manual_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert errors == []


def test_draft_allows_incomplete_not_run_rows(tmp_path: Path) -> None:
    case = {
        "case_id": "REST-HEALTH-01",
        "surface_id": "rest:GET:/health",
        "scenario_id": "linux-wheel-loopback-noauth",
        "applicability": "required",
        "required_lanes": ["manual"],
        "status": "not_run",
        "lane_results": {"manual": "not_run"},
    }
    assert evidence.validate(_matrix(case), [], release=False) == []


def test_draft_pass_requires_oracle_but_not_evidence(tmp_path: Path) -> None:
    case = _pass_case()
    case["evidence"] = []
    errors = evidence.validate(_matrix(case), [], release=False)
    assert errors == []

    case["oracle"] = {"expected": "HTTP 200"}
    errors = evidence.validate(_matrix(case), [], release=False)
    assert any("requires oracle.actual" in error for error in errors)


# ---------------------------------------------------------------------------------
# Demonstrated false passes
# ---------------------------------------------------------------------------------


def test_release_rejects_empty_surfaces_cases_and_inventory(tmp_path: Path) -> None:
    matrix = _matrix(cases=[], surfaces=[])
    errors = _run_release(matrix, [], tmp_path)
    assert any("at least one declared surface" in error for error in errors)
    assert any("at least one case" in error for error in errors)
    assert any("discovery inventory is empty" in error for error in errors)


def test_release_rejects_declared_surface_without_cases(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["surfaces"].append(_surface("rest:GET:/debug"))
    inventory.append({"surface_id": "rest:GET:/debug"})
    errors = _run_release(matrix, inventory, root)
    assert any("release requires at least one case for this surface" in error for error in errors)


def test_release_rejects_missing_test_node(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path, [("tests.api.test_health", "test_other", None)])
    case = _pass_case(test_node_ids=["missing.py::missing"], evidence=[_junit_entry(digest)])
    matrix = _matrix(case)
    errors = _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("was not collected" in error for error in errors)
    assert any("missing.py::missing" in error for error in errors)


def test_release_rejects_collected_failure_result(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path, [("tests.api.test_health", "test_health", "failure")])
    case = _pass_case(evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("result is 'failure'" in error for error in errors)


def test_release_rejects_collected_error_and_skipped_results(tmp_path: Path) -> None:
    for tag in ("error", "skipped"):
        digest = _write_junit(tmp_path, [("tests.api.test_health", "test_health", tag)])
        case = _pass_case(evidence=[_junit_entry(digest)])
        errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
        assert any(f"result is {tag!r}" in error for error in errors), tag


def test_release_rejects_nonzero_and_missing_exit_code(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["evidence"][0]["exit_code"] = 1
    errors = _run_release(matrix, inventory, root)
    assert any("exit_code must be 0" in error for error in errors)

    matrix, inventory, root = _valid_release_matrix(tmp_path)
    del matrix["cases"][0]["evidence"][0]["exit_code"]
    errors = _run_release(matrix, inventory, root)
    assert any("requires the run exit_code" in error for error in errors)


def test_release_rejects_text_evidence_for_automated_lane(tmp_path: Path) -> None:
    digest = _write_evidence(tmp_path, "runs/run-1/health.txt", b"FAILED test_a\n")
    case = _pass_case(evidence=[_junit_entry(digest, format="text", path="runs/run-1/health.txt")])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("requires a junit evidence entry" in error for error in errors)


def test_release_rejects_oracle_observed_failure(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(
        oracle=_health_oracle(observed_success=False),
        evidence=[_junit_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("observed_success is false" in error for error in errors)


def test_release_rejects_oracle_without_boolean_observed_success(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    for oracle in (
        {"expected": "HTTP 200", "actual": "HTTP 200"},
        _health_oracle(observed_success="yes"),
    ):
        case = _pass_case(oracle=oracle, evidence=[_junit_entry(digest)])
        errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
        assert any("observed_success to be a boolean" in error for error in errors), oracle


def test_release_accepts_negative_case_prose_with_observed_success(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(
        oracle=_health_oracle(
            expected="payload is invalid and no failure row is emitted",
            actual="invalid payload rejected; no failure recorded",
        ),
        evidence=[_junit_entry(digest)],
    )
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []
    assert "observed_success" not in evidence.render(matrix)


def test_release_rejects_junit_without_testcase_elements(tmp_path: Path) -> None:
    digest = _write_bytes(tmp_path, JUNIT_PATH, b"<testsuites></testsuites>")
    case = _pass_case(evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("contains no testcase elements" in error for error in errors)


def test_release_rejects_unparseable_junit(tmp_path: Path) -> None:
    digest = _write_bytes(tmp_path, JUNIT_PATH, b"<testsuites")
    case = _pass_case(evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("cannot parse junit XML" in error for error in errors)


def test_release_binds_node_across_multiple_junit_entries(tmp_path: Path) -> None:
    other = "runs/run-2/junit.xml"
    first = _write_junit(tmp_path, [("tests.api.test_other", "test_other", None)])
    second = _write_junit(tmp_path, relative=other)
    case = _pass_case(
        evidence=[_junit_entry(first), _junit_entry(second, path=other)],
    )
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []

    case["test_node_ids"] = ["tests/api/absent.py::test_absent"]
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("was not collected" in error for error in errors)


def test_integration_lane_pass_requires_its_own_junit_run(tmp_path: Path) -> None:
    hermetic_sha = _write_junit(tmp_path)
    case = _pass_case(
        required_lanes=["hermetic", "integration"],
        test_node_ids=[],
        lane_test_node_ids={
            "hermetic": [NODE_ID],
            "integration": [INTEGRATION_NODE_ID],
        },
        lane_results={"hermetic": "pass", "integration": "pass"},
        evidence=[_junit_entry(hermetic_sha)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("candidate-matching evidence for lane 'integration'" in error for error in errors)


def test_release_rejects_cross_lane_node_substitution(tmp_path: Path) -> None:
    hermetic_sha = _write_junit(tmp_path)
    integration_sha = _write_junit(
        tmp_path,
        [(INTEGRATION_CLASSNAME, "test_other", None)],
        relative=INTEGRATION_JUNIT_PATH,
    )
    case = _pass_case(
        required_lanes=["hermetic", "integration"],
        test_node_ids=[],
        lane_test_node_ids={
            "hermetic": [NODE_ID],
            "integration": [NODE_ID],
        },
        lane_results={"hermetic": "pass", "integration": "pass"},
        evidence=[
            _junit_entry(hermetic_sha),
            _integration_junit_entry(integration_sha),
        ],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("lane 'integration'" in error and "was not collected" in error for error in errors)


def test_multi_automated_lane_pass_requires_lane_node_mapping(tmp_path: Path) -> None:
    hermetic_sha = _write_junit(tmp_path)
    integration_sha = _write_junit(
        tmp_path,
        [(INTEGRATION_CLASSNAME, "test_health", None)],
        relative=INTEGRATION_JUNIT_PATH,
    )
    case = _pass_case(
        required_lanes=["hermetic", "integration"],
        test_node_ids=[NODE_ID],
        lane_results={"hermetic": "pass", "integration": "pass"},
        evidence=[
            _junit_entry(hermetic_sha),
            _integration_junit_entry(integration_sha),
        ],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any(
        "requires lane_test_node_ids when multiple automated lanes" in error for error in errors
    )


def test_global_node_ids_cannot_prove_two_automated_lanes(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path, [(HERMETIC_CLASSNAME, "test_health", None)])
    case = _pass_case(
        required_lanes=["hermetic", "integration"],
        test_node_ids=[NODE_ID],
        lane_test_node_ids={
            "hermetic": [NODE_ID],
            "integration": [INTEGRATION_NODE_ID],
        },
        lane_results={"hermetic": "pass", "integration": "pass"},
        evidence=[_junit_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("lane 'integration'" in error and "was not collected" in error for error in errors)


def test_release_accepts_explicit_lane_node_mapping(tmp_path: Path) -> None:
    hermetic_sha = _write_junit(tmp_path)
    integration_sha = _write_junit(
        tmp_path,
        [(INTEGRATION_CLASSNAME, "test_health", None)],
        relative=INTEGRATION_JUNIT_PATH,
    )
    case = _pass_case(
        required_lanes=["hermetic", "integration"],
        test_node_ids=[],
        lane_test_node_ids={
            "hermetic": [NODE_ID],
            "integration": [INTEGRATION_NODE_ID],
        },
        lane_results={"hermetic": "pass", "integration": "pass"},
        evidence=[
            _junit_entry(hermetic_sha),
            _integration_junit_entry(integration_sha),
        ],
    )
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []


def test_lane_node_mapping_validates_each_lane_separately(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path, [(HERMETIC_CLASSNAME, "test_health", "failure")])
    case = _pass_case(
        required_lanes=["hermetic", "integration"],
        test_node_ids=[],
        lane_test_node_ids={
            "hermetic": [NODE_ID],
            "integration": [INTEGRATION_NODE_ID],
        },
        lane_results={"hermetic": "pass", "integration": "pass"},
        evidence=[_junit_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("lane 'hermetic'" in error and "result is 'failure'" in error for error in errors)
    assert any("lane 'integration'" in error and "was not collected" in error for error in errors)


def test_lane_node_mapping_rejects_empty_or_missing_lane_entries(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(
        required_lanes=["hermetic", "integration"],
        test_node_ids=[],
        lane_test_node_ids={"hermetic": [NODE_ID], "integration": []},
        lane_results={"hermetic": "pass", "integration": "pass"},
        evidence=[_junit_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("requires lane_test_node_ids['integration']" in error for error in errors)


def test_single_automated_lane_may_use_global_node_ids(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(evidence=[_junit_entry(digest)])
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []


def test_single_lane_supplied_map_ignores_conflicting_global_node(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(
        test_node_ids=["tests/api/absent.py::test_absent"],
        lane_test_node_ids={"hermetic": [NODE_ID]},
        evidence=[_junit_entry(digest)],
    )
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []


def test_single_lane_map_only_valid_case_is_accepted(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(
        test_node_ids=[],
        lane_test_node_ids={"hermetic": [NODE_ID]},
        evidence=[_junit_entry(digest)],
    )
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []


def test_single_lane_supplied_map_with_absent_node_is_rejected(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(
        test_node_ids=[NODE_ID],
        lane_test_node_ids={"hermetic": ["tests/api/absent.py::test_absent"]},
        evidence=[_junit_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("tests/api/absent.py::test_absent" in error for error in errors)


def test_single_lane_supplied_map_with_empty_lane_list_is_rejected(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(
        test_node_ids=[NODE_ID],
        lane_test_node_ids={"hermetic": []},
        evidence=[_junit_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("lane_test_node_ids['hermetic']" in error for error in errors)


def test_junit_classname_must_match_module_not_bare_suffix(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path, [("test_health", "test_health", None)])
    case = _pass_case(evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("was not collected" in error and NODE_ID in error for error in errors)

    digest = _write_junit(tmp_path, [("api.test_health", "test_health", None)])
    case = _pass_case(evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("was not collected" in error for error in errors)

    digest = _write_junit(tmp_path, [("test_health.py", "test_health", None)])
    case = _pass_case(evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("was not collected" in error for error in errors)


def test_junit_classname_accepts_exact_module_path(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path, [(HERMETIC_CLASSNAME, "test_health", None)])
    case = _pass_case(evidence=[_junit_entry(digest)])
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []


def test_module_only_classname_cannot_borrow_a_class_method_pass(tmp_path: Path) -> None:
    node = "tests/api/test_health.py::TestHealth::test_health"
    digest = _write_junit(tmp_path, [(HERMETIC_CLASSNAME, "test_health", None)])
    case = _pass_case(test_node_ids=[node], evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("was not collected" in error and node in error for error in errors)


def test_class_method_node_requires_exact_class_classname(tmp_path: Path) -> None:
    node = "tests/api/test_health.py::TestHealth::test_health"
    digest = _write_junit(tmp_path, [(f"{HERMETIC_CLASSNAME}.TestHealth", "test_health", None)])
    case = _pass_case(test_node_ids=[node], evidence=[_junit_entry(digest)])
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []


def test_class_classname_cannot_match_a_module_level_node(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path, [(f"{HERMETIC_CLASSNAME}.TestHealth", "test_health", None)])
    case = _pass_case(test_node_ids=[NODE_ID], evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("was not collected" in error and NODE_ID in error for error in errors)


def test_nested_class_node_requires_full_qualifier_chain(tmp_path: Path) -> None:
    node = "tests/api/test_health.py::Outer::Inner::test_deep"
    digest = _write_junit(tmp_path, [(f"{HERMETIC_CLASSNAME}.Outer.Inner", "test_deep", None)])
    case = _pass_case(test_node_ids=[node], evidence=[_junit_entry(digest)])
    matrix = _matrix(case)
    assert _run_release(matrix, [{"surface_id": "rest:GET:/health"}], tmp_path) == []

    partial = _write_junit(tmp_path, [(f"{HERMETIC_CLASSNAME}.Outer", "test_deep", None)])
    case = _pass_case(test_node_ids=[node], evidence=[_junit_entry(partial)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("was not collected" in error and node in error for error in errors)


def test_release_rejects_unbound_pass_claim(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["test_node_ids"] = []
    errors = _run_release(matrix, inventory, root)
    assert any("requires test_node_ids for automated lane" in error for error in errors)


def test_release_rejects_pass_without_lane_evidence(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["evidence"] = []
    errors = _run_release(matrix, inventory, root)
    assert any(
        "requires candidate-matching evidence for lane 'hermetic'" in error for error in errors
    )


def test_release_rejects_contradictory_lane_results_on_pass(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["lane_results"] = {"hermetic": "not_run"}
    errors = _run_release(matrix, inventory, root)
    assert any("contradictory result" in error for error in errors)


# ---------------------------------------------------------------------------------
# Manual evidence structure
# ---------------------------------------------------------------------------------


def _manual_case(tmp_path: Path, **entry_overrides: object) -> tuple[dict, list[dict], Path]:
    digest = _write_evidence(tmp_path, "runs/run-1/health-manual.txt")
    entry = _manual_entry(digest, **entry_overrides)
    case = _pass_case(
        required_lanes=["manual"],
        test_node_ids=[],
        manual_steps=["open /health and confirm HTTP 200"],
        lane_results={"manual": "pass"},
        evidence=[entry],
    )
    return _matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path


def test_manual_evidence_requires_structured_observation(tmp_path: Path) -> None:
    checks = {
        "reviewer": {},
        "observed_success": {"observed_success": False},
        "timestamp": {"timestamp": "2026-09-20T12:00:00"},
        "steps": {"steps": []},
    }
    for field, override in checks.items():
        if field == "reviewer":
            override = {"reviewer": ""}
        matrix, inventory, root = _manual_case(tmp_path, **override)
        errors = _run_release(matrix, inventory, root)
        assert any(field in error for error in errors), (field, errors)


def test_manual_evidence_rejects_junit_format(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    matrix, inventory, root = _manual_case(tmp_path, format="junit", path=JUNIT_PATH, sha256=digest)
    errors = _run_release(matrix, inventory, root)
    assert any("manual lane evidence cannot be a junit run" in error for error in errors)


# ---------------------------------------------------------------------------------
# Evidence bytes, candidate identity, and path safety
# ---------------------------------------------------------------------------------


def test_missing_evidence_file_is_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["evidence"][0]["path"] = "runs/run-1/absent.xml"
    errors = _run_release(matrix, inventory, root)
    assert any("missing or is not a regular file" in error for error in errors)


def test_empty_evidence_file_is_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    digest = _write_bytes(root, JUNIT_PATH, b"")
    matrix["cases"][0]["evidence"][0]["sha256"] = digest
    errors = _run_release(matrix, inventory, root)
    assert any("is empty" in error for error in errors)


def test_checksum_mismatch_is_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["evidence"][0]["sha256"] = "f" * 64
    errors = _run_release(matrix, inventory, root)
    assert any("checksum mismatch" in error for error in errors)


def test_candidate_id_mismatch_is_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["evidence"][0]["candidate_id"] = "rc-old"
    errors = _run_release(matrix, inventory, root)
    assert any("does not match matrix candidate" in error for error in errors)
    assert any("candidate-matching evidence" in error for error in errors)


def test_evidence_artifact_digest_must_match_candidate(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["evidence"][0]["artifact_digest"] = "sha256:" + "9" * 64
    errors = _run_release(matrix, inventory, root)
    assert any("not a declared candidate digest" in error for error in errors)


def test_path_traversal_and_absolute_paths_are_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_bytes(b"secret\n")
    case = matrix["cases"][0]

    case["evidence"][0]["path"] = "../outside-secret.txt"
    errors = _run_release(matrix, inventory, root)
    assert any("escapes evidence_root" in error for error in errors)

    case["evidence"][0]["path"] = str(outside)
    errors = _run_release(matrix, inventory, root)
    assert any("must be relative to evidence_root" in error for error in errors)


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_bytes(b"secret\n")
    (root / "linked.xml").symlink_to(outside)
    matrix["cases"][0]["evidence"][0]["path"] = "linked.xml"
    errors = _run_release(matrix, inventory, root)
    assert any("escapes evidence_root" in error for error in errors)


def test_symlink_escape_is_rejected_before_junit_read(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    outside = tmp_path.parent / "outside-valid-junit.xml"
    outside.write_bytes(_junit_xml())
    (root / "linked.xml").symlink_to(outside)
    matrix["cases"][0]["evidence"][0]["path"] = "linked.xml"
    errors = _run_release(matrix, inventory, root)
    assert any("escapes evidence_root" in error for error in errors)
    assert not any("cannot parse junit XML" in error for error in errors)
    assert not any("contains no testcase elements" in error for error in errors)


def test_lane_node_mapping_rejects_non_automated_lane_entries(tmp_path: Path) -> None:
    digest = _write_junit(tmp_path)
    case = _pass_case(
        required_lanes=["hermetic"],
        lane_test_node_ids={"hermetic": [NODE_ID], "manual": [NODE_ID]},
        evidence=[_junit_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any(
        "lane_test_node_ids has unsupported automated lane 'manual'" in error for error in errors
    )

    case = _pass_case(
        required_lanes=["hermetic"],
        lane_test_node_ids={"hermetic": [NODE_ID], "integration": [INTEGRATION_NODE_ID]},
        evidence=[_junit_entry(digest)],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any(
        "lane_test_node_ids has lane 'integration' that is not a required automated lane" in error
        for error in errors
    )


def test_release_requires_evidence_root_when_evidence_present(tmp_path: Path) -> None:
    matrix, inventory, _ = _release_case(tmp_path)
    errors = evidence.validate(matrix, inventory, release=True, evidence_root=None)
    assert any("evidence_root is required" in error for error in errors)


def test_release_requires_git_commit_identity(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["candidate"]["broker_commit"] = "not-a-sha"
    errors = _run_release(matrix, inventory, root)
    assert any("40-character git commit SHA" in error for error in errors)


# ---------------------------------------------------------------------------------
# Surfaces, dispositions, and statuses
# ---------------------------------------------------------------------------------


def test_missing_required_lane_proof_is_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    case = matrix["cases"][0]
    case["required_lanes"] = ["hermetic", "manual"]
    case["manual_steps"] = ["open /health"]
    case["lane_results"] = {"hermetic": "pass", "manual": "not_run"}
    errors = _run_release(matrix, inventory, root)
    assert any("contradictory result" in error for error in errors)
    assert any("candidate-matching evidence for lane 'manual'" in error for error in errors)


def test_required_unresolved_statuses_are_rejected(tmp_path: Path) -> None:
    for status in ("fail", "blocked", "not_run", "stale"):
        matrix, inventory, root = _valid_release_matrix(tmp_path)
        case = matrix["cases"][0]
        case["status"] = status
        case["lane_results"] = {"hermetic": status}
        errors = _run_release(matrix, inventory, root)
        assert any("release-blocking" in error for error in errors), status


def test_skip_xfail_deselection_never_pass(tmp_path: Path) -> None:
    for marker in ("skip", "xfail", "deselected"):
        matrix, inventory, root = _valid_release_matrix(tmp_path)
        case = matrix["cases"][0]
        case["status"] = marker
        case["lane_results"] = {"hermetic": marker}
        errors = _run_release(matrix, inventory, root)
        assert any("not a valid result" in error for error in errors), marker


def test_unknown_status_and_lane_are_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["required_lanes"] = ["hermetic", "telepathy"]
    errors = _run_release(matrix, inventory, root)
    assert any("required lane 'telepathy' is not supported" in error for error in errors)

    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["lane_results"]["telepathy"] = "pass"
    errors = _run_release(matrix, inventory, root)
    assert any("unsupported lane 'telepathy'" in error for error in errors)


def test_declared_source_must_be_a_file_line_reference(tmp_path: Path) -> None:
    for source in (None, [], ["docs/support-matrix.md"], "docs/support-matrix.md:12"):
        matrix, inventory, root = _valid_release_matrix(tmp_path)
        if source is None:
            del matrix["surfaces"][0]["declared_source"]
        else:
            matrix["surfaces"][0]["declared_source"] = source
        errors = _run_release(matrix, inventory, root)
        assert any("declared_source" in error for error in errors), source


def test_scoped_disposition_requires_source_grounding(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["surfaces"].append(_surface("rest:GET:/debug"))
    inventory.append({"surface_id": "rest:GET:/debug"})
    matrix["dispositions"] = [{"scope": "rest:GET:/debug", "reason": "not shipped"}]
    errors = _run_release(matrix, inventory, root)
    assert any("source must be a file:line source reference" in error for error in errors)

    matrix["dispositions"] = [
        {
            "scope": "rest:GET:/debug",
            "reason": "not shipped",
            "source": "docs/support-matrix.md:40",
            "approved_by": "release-owner",
        }
    ]
    errors = _run_release(matrix, inventory, root)
    assert any("approval_receipt" in error for error in errors)

    matrix["dispositions"][0]["approval_receipt"] = "ops/receipts/2026-09-20-debug.md"
    assert _run_release(matrix, inventory, root) == []


def test_disposition_scope_must_be_declared_surface(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["dispositions"] = [
        {"scope": "rest:GET:/ghost", "reason": "x", "source": "docs/support-matrix.md:40"}
    ]
    errors = _run_release(matrix, inventory, root)
    assert any("scope must be the surface_id of a declared surface" in error for error in errors)


def test_unmapped_discovered_surface_is_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    inventory.append({"surface_id": "mcp:tool:ask_orchestrator"})
    errors = _run_release(matrix, inventory, root)
    assert any("unmapped discovered surface" in error for error in errors)


def test_removed_declared_surface_is_rejected(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    errors = _run_release(matrix, [], root)
    assert any("discovery inventory is empty" in error for error in errors)
    assert any("removed or stale declaration" in error for error in errors)


# ---------------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------------


def _exception(**overrides: object) -> dict:
    base: dict = {
        "owner": "release-owner",
        "scope": "REST-HEALTH-01",
        "reason": "provider gap accepted",
        "risk": "bounded",
        "expires": "2999-01-01",
        "resolved_commit": COMMIT,
        "approved": True,
        "approval_receipt": "ops/receipts/2026-09-20-gap.md",
        "compensating_controls": "manual re-check before publication",
    }
    base.update(overrides)
    return base


def _exception_matrix(tmp_path: Path, exception: dict | None) -> tuple[dict, list[dict], Path]:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    case = matrix["cases"][0]
    case["status"] = "approved_exception"
    case["lane_results"] = {"hermetic": "blocked"}
    case["exception"] = exception
    return matrix, inventory, root


def test_release_accepts_receipted_unexpired_exception(tmp_path: Path) -> None:
    matrix, inventory, root = _exception_matrix(tmp_path, _exception())
    assert _run_release(matrix, inventory, root) == []


def test_exception_requires_record_and_valid_status(tmp_path: Path) -> None:
    matrix, inventory, root = _exception_matrix(tmp_path, None)
    errors = _run_release(matrix, inventory, root)
    assert any("requires an exception record" in error for error in errors)

    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["exception"] = _exception()
    errors = _run_release(matrix, inventory, root)
    assert any("only valid when status is 'approved_exception'" in error for error in errors)


def test_exception_rejects_unapproved_expired_missing_risk_and_empty_scope(tmp_path: Path) -> None:
    for override, marker in (
        ({"approved": False}, "exception.approved must be true"),
        ({"expires": "2020-01-01"}, "exception expired"),
        ({"scope": ""}, "exception.scope must not be empty"),
        ({"scope": "OTHER-CASE-01"}, "exception.scope must name"),
    ):
        matrix, inventory, root = _exception_matrix(tmp_path, _exception(**override))
        errors = _run_release(matrix, inventory, root)
        assert any(marker in error for error in errors), (override, errors)

    matrix, inventory, root = _exception_matrix(tmp_path, _exception())
    del matrix["cases"][0]["exception"]["risk"]
    errors = _run_release(matrix, inventory, root)
    assert any("exception.risk" in error for error in errors)


def test_exception_requires_receipt_controls_and_matching_commit(tmp_path: Path) -> None:
    matrix, inventory, root = _exception_matrix(tmp_path, _exception(approval_receipt=None))
    errors = _run_release(matrix, inventory, root)
    assert any("approval_receipt" in error for error in errors)

    matrix, inventory, root = _exception_matrix(tmp_path, _exception(compensating_controls=None))
    errors = _run_release(matrix, inventory, root)
    assert any("compensating_controls" in error for error in errors)

    matrix, inventory, root = _exception_matrix(tmp_path, _exception(resolved_commit=OTHER_COMMIT))
    errors = _run_release(matrix, inventory, root)
    assert any("exception is stale" in error for error in errors)


def test_exception_counts_stay_in_denominator_and_out_of_numerator(tmp_path: Path) -> None:
    matrix, inventory, root = _exception_matrix(tmp_path, _exception(scope=["REST-HEALTH-01"]))
    assert _run_release(matrix, inventory, root) == []
    summary = evidence.summarize(matrix)
    assert summary["required_total"] == 1
    assert summary["required_passing"] == 0
    assert summary["exceptions_accepted"] == 1


# ---------------------------------------------------------------------------------
# Invalid JSON types must be reported, never raised
# ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "marker"),
    [
        (lambda m: m.update(candidate=[]), "candidate must be a mapping"),
        (lambda m: m.update(surfaces={}), "surfaces must be a list"),
        (lambda m: m.update(cases={}), "cases must be a list"),
        (
            lambda m: m["cases"][0].update(required_lanes="hermetic"),
            "required_lanes must be a list",
        ),
        (lambda m: m["cases"][0].update(status=["pass"]), "status"),
        (lambda m: m["cases"][0].update(oracle="ok"), "oracle must be a mapping"),
        (lambda m: m["cases"][0].update(evidence="bytes"), "evidence must be a list"),
        (lambda m: m["cases"][0].update(lane_results=[]), "lane_results must be a mapping"),
        (lambda m: m["cases"][0].update(test_node_ids="tests/x.py::test_x"), "test_node_ids"),
        (
            lambda m: m["cases"][0].update(lane_test_node_ids=["tests/x.py::test_x"]),
            "lane_test_node_ids must be a mapping",
        ),
        (
            lambda m: m["cases"][0].update(lane_test_node_ids={"hermetic": "tests/x.py::test_x"}),
            "lane_test_node_ids['hermetic']",
        ),
        (lambda m: m["cases"][0].update(exception=[]), "exception must be a mapping"),
        (lambda m: m.update(dispositions={}), "dispositions must be a list"),
    ],
)
def test_invalid_json_types_return_errors_not_typeerror(
    tmp_path: Path, mutate, marker: str
) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    mutate(matrix)
    errors = _run_release(matrix, inventory, root)
    assert any(marker in error for error in errors), (marker, errors)


def test_inventory_must_be_a_list_not_a_mapping(tmp_path: Path) -> None:
    matrix, _, root = _valid_release_matrix(tmp_path)
    errors = _run_release(matrix, {"surface_id": "rest:GET:/health"}, root)
    assert errors == ["inventory must be a list for release validation"]


def test_render_and_summarize_tolerate_invalid_types(tmp_path: Path) -> None:
    matrix = {
        "schema_version": evidence.SCHEMA_VERSION,
        "candidate": [],
        "surfaces": {},
        "cases": "nope",
    }
    rendered = evidence.render(matrix)
    assert "NOT RELEASE PROOF" in rendered
    assert "empty dataset" in rendered
    summary = evidence.summarize(matrix)
    assert summary["case_total"] == 0


# ---------------------------------------------------------------------------------
# Schema pinning, determinism, renderer, CLI, no execution
# ---------------------------------------------------------------------------------


def test_schema_version_is_pinned(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["schema_version"] = "acceptance-evidence.v2"
    errors = _run_release(matrix, inventory, root)
    assert any("schema_version must be" in error for error in errors)


def test_validate_never_executes_matrix_commands(tmp_path: Path) -> None:
    marker = tmp_path / "executed.txt"
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    case = matrix["cases"][0]
    case["reproduction"] = {"command": f"touch {marker}"}
    case["manual_steps"] = [f"touch {marker}"]
    assert _run_release(matrix, inventory, root) == []
    assert not marker.exists()


def test_render_is_deterministic_and_byte_identical_to_input(tmp_path: Path) -> None:
    matrix, _, _ = _valid_release_matrix(tmp_path)
    first = evidence.render(matrix)
    second = evidence.render(json.loads(json.dumps(matrix)))
    assert first == second
    assert first.endswith("\n")


def test_render_labels_not_ready_with_blockers(tmp_path: Path) -> None:
    matrix, _, _ = _valid_release_matrix(tmp_path)
    case = matrix["cases"][0]
    case["status"] = "not_run"
    case["lane_results"] = {"hermetic": "not_run"}
    rendered = evidence.render(matrix)
    assert "- Overall: not ready" in rendered
    assert "NOT RELEASE PROOF" in rendered
    assert "## Blockers" in rendered
    assert "`REST-HEALTH-01` (rest:GET:/health): not_run" in rendered
    assert "does not prove the full product" in rendered


def test_render_labels_empty_dataset_not_ready() -> None:
    rendered = evidence.render(_matrix(cases=[], surfaces=[]))
    assert "empty dataset" in rendered
    assert "all required rows recorded as pass" not in rendered


def test_render_never_says_ready_from_unchecked_pass_statuses(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    case = matrix["cases"][0]
    case["evidence"] = []
    matrix["cases"] = [case]
    errors = _run_release(matrix, inventory, root)
    assert any("candidate-matching evidence" in error for error in errors)
    rendered = evidence.render(matrix)
    assert "- Overall: all required rows recorded as pass" in rendered
    assert "evidence validation is still required" in rendered
    assert "ready for the recorded scope only" not in rendered
    assert "- Overall: ready" not in rendered

    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix["cases"][0]["evidence"][0]["sha256"] = "f" * 64
    errors = _run_release(matrix, inventory, root)
    assert any("checksum mismatch" in error for error in errors)
    rendered = evidence.render(matrix)
    assert "evidence validation is still required" in rendered
    assert "- Overall: ready" not in rendered


def test_render_never_claims_full_product_proof_without_blockers(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    assert _run_release(matrix, inventory, root) == []
    rendered = evidence.render(matrix)
    assert "verify declarations, lanes, and cleanup separately" in rendered
    assert "proof of the full product" not in rendered


def test_render_surfaces_exception_expiry_and_summary_counts(tmp_path: Path) -> None:
    matrix, _, _ = _exception_matrix(tmp_path, _exception())
    rendered = evidence.render(matrix)
    assert "accepted exceptions retained in the denominator" in rendered
    assert "expires 2999-01-01" in rendered
    assert "required cases unresolved" not in rendered


def test_cli_validate_exit_codes_and_report_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text(json.dumps({"surfaces": inventory}), encoding="utf-8")

    exit_code = evidence.main(
        [
            "validate",
            str(matrix_path),
            "--release",
            "--inventory",
            str(inventory_path),
            "--evidence-root",
            str(root),
        ]
    )
    assert exit_code == 0

    matrix["cases"][0]["evidence"][0]["path"] = "missing.xml"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    exit_code = evidence.main(
        ["validate", str(matrix_path), "--release", "--evidence-root", str(root)]
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "acceptance-error:" in captured.err

    assert evidence.main(["report", str(tmp_path / "matrix.json")]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("# Release acceptance matrix")


def test_cli_reports_unreadable_input_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert evidence.main(["validate", str(tmp_path / "absent.json"), "--release"]) == 1
    assert "cannot read input" in capsys.readouterr().err


def test_validator_has_no_execution_side_effect_via_import(tmp_path: Path) -> None:
    module_path = Path(evidence.__file__)
    assert module_path.name == "evidence.py"
    source = module_path.read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "os.system" not in source


@pytest.mark.parametrize("separate_files", [False, True])
def test_release_rejects_duplicate_collected_node(tmp_path: Path, separate_files: bool) -> None:
    rows = [(HERMETIC_CLASSNAME, "test_health", None)]
    first = _write_junit(tmp_path, rows if separate_files else rows * 2)
    entries = [_junit_entry(first)]
    if separate_files:
        second_path = "runs/run-2/junit.xml"
        second = _write_junit(tmp_path, rows, relative=second_path)
        entries.append(_junit_entry(second, path=second_path))
    case = _pass_case(evidence=entries)
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("collected 2 times" in error for error in errors)


@pytest.mark.parametrize(
    "xml",
    [
        '<unrelated><testcase classname="tests.api.test_health" name="test_health"/></unrelated>',
        '<testsuite tests="2"><testcase classname="tests.api.test_health" name="test_health"/></testsuite>',
        '<testsuite failures="1"><testcase classname="tests.api.test_health" name="test_health"/></testsuite>',
    ],
)
def test_release_rejects_invalid_junit_structure_or_counts(tmp_path: Path, xml: str) -> None:
    digest = _write_bytes(tmp_path, JUNIT_PATH, xml.encode())
    case = _pass_case(evidence=[_junit_entry(digest)])
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("junit" in error for error in errors)


def test_release_rejects_surface_with_only_optional_unrun_case(tmp_path: Path) -> None:
    case = _pass_case(applicability="optional", status="not_run", evidence=[], lane_results={})
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    assert any("required case" in error for error in errors)


def test_cli_preserves_unresolved_discovery_issues(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix))
    inventory_path = tmp_path / "discovery.json"
    inventory_path.write_text(
        json.dumps(
            {
                "schema_version": "release-acceptance-discovery.v1",
                "status": "unresolved",
                "surfaces": inventory,
                "issues": [{"code": "extractor-failed", "message": "domain unavailable"}],
            }
        )
    )
    assert (
        evidence.main(
            [
                "validate",
                str(matrix_path),
                "--release",
                "--inventory",
                str(inventory_path),
                "--evidence-root",
                str(root),
            ]
        )
        == 1
    )


@pytest.mark.parametrize(
    "status,issues",
    [
        ("complete", [{"code": "unmapped"}]),
        ("unresolved", []),
        ("complete", None),
    ],
)
def test_release_rejects_unresolved_discovery_report(
    tmp_path: Path,
    status: str,
    issues: object,
) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    report = {
        "schema_version": "release-acceptance-discovery.v1",
        "status": status,
        "issues": issues,
        "surfaces": inventory,
    }
    errors = evidence.validate(matrix, report, release=True, evidence_root=root)
    assert any("discovery report" in error for error in errors)


def test_release_accepts_complete_discovery_report(tmp_path: Path) -> None:
    matrix, inventory, root = _valid_release_matrix(tmp_path)
    report = {
        "schema_version": "release-acceptance-discovery.v1",
        "status": "complete",
        "issues": [],
        "surfaces": inventory,
    }
    assert evidence.validate(matrix, report, release=True, evidence_root=root) == []


@pytest.mark.parametrize(
    "attributes,child",
    [
        ('status="notrun"', ""),
        ('status="failed"', ""),
        ("", "<flakyFailure/>"),
        ("", "<rerunFailure/>"),
        ("", "<unknown-result/>"),
    ],
)
def test_release_rejects_nonpassing_or_unknown_junit_result(
    tmp_path: Path,
    attributes: str,
    child: str,
) -> None:
    xml = (
        '<testsuite><testcase classname="tests.api.test_health" '
        f'name="test_health" {attributes}>{child}</testcase></testsuite>'
    )
    digest = _write_bytes(tmp_path, JUNIT_PATH, xml.encode())
    case = _pass_case(evidence=[_junit_entry(digest)])
    assert _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)


def _unittest_case(tmp_path: Path) -> dict:
    from tools.release_acceptance.unittest_report import run_unittest_discovery

    component = tmp_path / "component"
    tests = component / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "__init__.py").write_text("")
    (tests / "test_probe.py").write_text(
        "import unittest\nclass Probe(unittest.TestCase):\n"
        "    def test_ok(self):\n        self.assertEqual(2 + 2, 4)\n"
    )
    relative = "runs/unittest.json"
    report = run_unittest_discovery(component, output_path=tmp_path / relative)
    entry = {
        "lane": "hermetic",
        "format": "unittest-json",
        "path": relative,
        "sha256": hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest(),
        "candidate_id": CANDIDATE_ID,
        "exit_code": report.exit_code,
        "project_prefix": "components/probe",
    }
    return _pass_case(
        test_node_ids=["components/probe/tests/test_probe.py::Probe::test_ok"],
        evidence=[entry],
    )


def test_release_binds_exact_unittest_component_result(tmp_path: Path) -> None:
    case = _unittest_case(tmp_path)
    assert _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path) == []


@pytest.mark.parametrize(
    "failure", ["wrong_component", "missing_node", "duplicate_report", "missing_prefix"]
)
def test_release_rejects_invalid_component_proof(tmp_path: Path, failure: str) -> None:
    case = _unittest_case(tmp_path)
    if failure == "wrong_component":
        case["evidence"][0]["project_prefix"] = "components/other"
    elif failure == "missing_node":
        case["test_node_ids"][0] += "_absent"
    elif failure == "duplicate_report":
        case["evidence"].append(dict(case["evidence"][0]))
    elif failure == "missing_prefix":
        del case["evidence"][0]["project_prefix"]
    assert _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)


def test_component_proof_does_not_read_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = _unittest_case(tmp_path)
    outside = tmp_path / "outside.json"
    root = tmp_path / "contained"
    root.mkdir()
    outside.write_text("untrusted canary")
    (root / "escape.json").symlink_to(outside)
    case["evidence"][0]["path"] = "escape.json"

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("component matcher read an escaped artifact")

    monkeypatch.setattr(evidence.result_adapters, "match_unittest_report", forbidden)
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], root)
    assert any("escapes evidence_root" in error for error in errors)


@pytest.mark.parametrize("skip", [False, True])
def test_release_binds_unittest_callback_report(tmp_path: Path, skip: bool) -> None:
    from tools.release_acceptance.unittest_report import run_unittest_discovery

    component = tmp_path / "component"
    tests = component / "tests"
    tests.mkdir(parents=True)
    (tests / "__init__.py").write_text("")
    decorator = "    @unittest.skip('fixture')\n" if skip else ""
    (tests / "test_probe.py").write_text(
        "import unittest\nclass Probe(unittest.TestCase):\n"
        + decorator
        + "    def test_ok(self):\n        self.assertEqual(2 + 2, 4)\n"
    )
    relative = "runs/unittest.json"
    report = run_unittest_discovery(component, output_path=tmp_path / relative)
    entry = {
        "lane": "hermetic",
        "format": "unittest-json",
        "path": relative,
        "sha256": hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest(),
        "candidate_id": CANDIDATE_ID,
        "exit_code": report.exit_code,
        "project_prefix": "packages/probe",
    }
    case = _pass_case(
        test_node_ids=["packages/probe/tests/test_probe.py::Probe::test_ok"],
        evidence=[entry],
    )
    errors = _run_release(_matrix(case), [{"surface_id": "rest:GET:/health"}], tmp_path)
    if skip:
        assert any("skipped" in error for error in errors)
    else:
        assert errors == []
