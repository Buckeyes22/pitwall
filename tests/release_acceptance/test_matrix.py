"""Focused tests for draft acceptance matrix assembly and gap preservation."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import matrix

CANDIDATE = {
    "schema_version": "release-acceptance-candidate.v1",
    "candidate_id": "candidate-fixture-1",
    "git": {"commit": "a" * 40, "merge_base": "b" * 40},
    "clean": True,
    "release_ready": False,
    "manifest": [{"path": "src/app.py", "sha256": "1" * 64}],
    "dependencies": [],
    "artifacts": [],
    "tools": {"python": "3.14.7"},
    "issues": [],
}


def _surface(surface_id: str, source: str = "src/app.py:1", kind: str = "rest") -> dict[str, Any]:
    return {
        "surface_id": surface_id,
        "kind": kind,
        "surface_kind": kind,
        "operation": "probe",
        "source": source,
        "declared_source": [source],
        "metadata": {},
        "origin": {"domain": "fixture", "schema_version": "fixture.v1", "surface_id": surface_id},
    }


def _discovery(
    *surfaces: dict[str, Any], issues: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "release-acceptance-discovery.v1",
        "status": "unresolved" if issues else "complete",
        "domains": [],
        "surfaces": list(surfaces),
        "issues": issues or [],
        "deferred_scope": issues or [],
        "source_review": {"status": "linked_not_approved", "auto_approved": False},
    }


def _test_index(
    *nodes: dict[str, Any], templates: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "schema_version": "release-acceptance-test-index.v1",
        "nodes": list(nodes),
        "templates": templates or [],
        "issues": [],
    }


def _node(node_id: str, path: str, framework: str = "pytest") -> dict[str, Any]:
    return {
        "node_id": node_id,
        "path": path,
        "line": 1,
        "framework": framework,
        "skip_indicated": False,
        "expected_failure": False,
    }


def _collection_report(root: Path, source: Path, *items: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "release-acceptance-pytest-collection.v1",
        "candidate_root": str(root.resolve()),
        "pytest_root": str(root.resolve()),
        "pytest_version": "9.1.1",
        "collect_only": True,
        "exitstatus": 0,
        "status": "complete",
        "items": [
            {
                "collected_id": item["collected_id"],
                "nodeid": item["collected_id"],
                "framework": "pytest",
                "source_file": "tests/test_parameterized.py",
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "line": 1,
                "function": "test_parameterized",
                "parameterized": True,
                **item,
            }
            for item in items
        ],
        "collection_failures": [],
        "collection_skips": [],
        "issues": [],
    }


def _binding(
    surface_id: str, source: str, source_hash: str, node_id: str, **extra: Any
) -> dict[str, Any]:
    return {
        "surface_id": surface_id,
        "framework": "pytest",
        "source": source,
        "test_source_sha256": source_hash,
        "reviewed_oracle": "expected fixture behavior",
        "proof_lane": "hermetic",
        "limits": "fixture only",
        "reviewer": "fixture reviewer",
        "review_state": "source_and_collection_reviewed_execution_evidence_pending",
        "status": "not_run",
        "test_node_id": node_id,
        **extra,
    }


def _write_inputs(root: Path, declarations: dict[str, Any], bindings: dict[str, Any]) -> None:
    (root / "release_acceptance").mkdir(parents=True, exist_ok=True)
    (root / "release_acceptance/declarations.json").write_text(
        json.dumps(declarations), encoding="utf-8"
    )
    (root / "release_acceptance/reviewed-bindings.json").write_text(
        json.dumps(bindings), encoding="utf-8"
    )


def _assemble(
    tmp_path: Path,
    *,
    surfaces: list[dict[str, Any]],
    bindings: list[dict[str, Any]] | None = None,
    test_report: dict[str, Any] | None = None,
    issues: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    _write_inputs(
        root,
        {"schema_version": "declarations.v1", "declarations": []},
        {"schema_version": "reviewed-test-bindings.v1", "bindings": bindings or []},
    )
    output = tmp_path / "draft-output"
    return matrix.assemble(
        root,
        output_dir=output,
        candidate_record=CANDIDATE,
        discovery_report=_discovery(*surfaces, issues=issues),
        test_index_report=test_report or _test_index(),
    )


def test_lost_rows_and_duplicate_ids_are_retained_as_gaps(tmp_path: Path) -> None:
    result = _assemble(
        tmp_path,
        surfaces=[
            _surface("rest:GET:/same", source="a.py:1"),
            _surface("rest:GET:/same", source="b.py:1"),
        ],
    )
    matrix_data = result["matrix"]
    gap_kinds = {gap["kind"] for gap in result["gap_ledger"]["gaps"]}

    assert len(matrix_data["surfaces"]) == 2
    assert len(matrix_data["cases"]) == 2
    assert "duplicate-discovery-surface" in gap_kinds
    assert all(case["status"] == "not_run" for case in matrix_data["cases"])
    assert not any(case["status"] == "pass" for case in matrix_data["cases"])


def test_stale_binding_is_gap_and_not_a_case(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "tests/test_fixture.py"
    source.parent.mkdir()
    source.write_text("def test_fixture():\n    pass\n", encoding="utf-8")
    old_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_text("def test_fixture():\n    changed = True\n", encoding="utf-8")
    binding = _binding(
        "rest:GET:/fixture",
        "tests/test_fixture.py:1",
        old_hash,
        "tests/test_fixture.py::test_fixture",
    )
    _write_inputs(
        root, {"schema_version": "declarations.v1", "declarations": []}, {"bindings": [binding]}
    )
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "out",
        candidate_record=CANDIDATE,
        discovery_report=_discovery(_surface("rest:GET:/fixture", "src/app.py:1")),
        test_index_report=_test_index(
            _node("tests/test_fixture.py::test_fixture", "tests/test_fixture.py")
        ),
    )

    assert not any(
        case["draft"]["kind"] == "source_reviewed_binding" for case in result["matrix"]["cases"]
    )
    assert any(gap["kind"] == "stale-binding" for gap in result["gap_ledger"]["gaps"])
    assert all(case["status"] == "not_run" for case in result["matrix"]["cases"])


def test_incomplete_review_metadata_is_gap_and_not_a_source_review_case(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "tests/test_fixture.py"
    source.parent.mkdir()
    source.write_text("def test_fixture():\n    pass\n", encoding="utf-8")
    binding = _binding(
        "rest:GET:/fixture",
        "tests/test_fixture.py:1",
        hashlib.sha256(source.read_bytes()).hexdigest(),
        "tests/test_fixture.py::test_fixture",
    )
    binding.pop("reviewed_oracle")
    binding.pop("reviewer")
    binding.pop("review_state")
    _write_inputs(root, {"declarations": []}, {"bindings": [binding]})
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "out",
        candidate_record=CANDIDATE,
        discovery_report=_discovery(_surface("rest:GET:/fixture", "src/app.py:1")),
        test_index_report=_test_index(
            _node("tests/test_fixture.py::test_fixture", "tests/test_fixture.py")
        ),
    )

    assert any(gap["kind"] == "incomplete-binding-review" for gap in result["gap_ledger"]["gaps"])
    assert not any(
        case["draft"]["kind"] == "source_reviewed_binding" for case in result["matrix"]["cases"]
    )


def test_missing_parameterized_node_requires_collection_proof(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "tests/test_parameterized.py"
    source.parent.mkdir()
    source.write_text("def test_parameterized(value):\n    pass\n", encoding="utf-8")
    node_id = "tests/test_parameterized.py::test_parameterized"
    binding = _binding(
        "rest:GET:/fixture",
        "tests/test_parameterized.py:1",
        hashlib.sha256(source.read_bytes()).hexdigest(),
        node_id,
    )
    _write_inputs(root, {"declarations": []}, {"bindings": [binding]})
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "out",
        candidate_record=CANDIDATE,
        discovery_report=_discovery(_surface("rest:GET:/fixture", "src/app.py:1")),
        test_index_report=_test_index(
            templates=[
                {
                    "template_id": "template:tests/test_parameterized.py:1:parameterized",
                    "path": "tests/test_parameterized.py",
                    "unresolved_reason": "parameterized",
                }
            ]
        ),
    )

    assert any(
        gap["kind"] == "parameterized-node-needs-collection-proof"
        for gap in result["gap_ledger"]["gaps"]
    )
    assert not any(
        case["draft"]["kind"] == "source_reviewed_binding" for case in result["matrix"]["cases"]
    )


def test_discovery_issues_and_declaration_hints_are_preserved_without_approval(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_inputs(
        root,
        {
            "declarations": [
                {
                    "declaration_id": "decl-fixture",
                    "source": "docs/support-matrix.md:1",
                    "status": "supported",
                    "scope": {"patterns": ["rest:GET:/fixture"], "selector_status": "provisional"},
                    "required_proof_lanes": ["hermetic"],
                }
            ],
        },
        {"bindings": []},
    )
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "out",
        candidate_record=CANDIDATE,
        discovery_report=_discovery(
            _surface("rest:GET:/fixture"),
            issues=[
                {
                    "code": "unresolved-fixture",
                    "status": "unresolved",
                    "reason": "needs review",
                    "surface_ids": ["rest:GET:/fixture"],
                }
            ],
        ),
        test_index_report=_test_index(),
    )
    surface = result["matrix"]["surfaces"][0]

    assert surface["declaration_hints"][0]["declaration_id"] == "decl-fixture"
    assert surface["declaration_hints"][0]["auto_approved"] is False
    assert result["matrix"]["discovery_issues"][0]["code"] == "unresolved-fixture"
    assert any(gap["kind"] == "discovery-issue" for gap in result["gap_ledger"]["gaps"])
    assert all(case["status"] == "not_run" for case in result["matrix"]["cases"])
    assert all(case["applicability"] == "required" for case in result["matrix"]["cases"])


def test_outputs_are_deterministic_external_and_share_candidate_id(tmp_path: Path) -> None:
    surfaces = [_surface("rest:GET:/fixture")]
    first = _assemble(tmp_path / "one", surfaces=surfaces)
    second = _assemble(tmp_path / "two", surfaces=surfaces)
    first_dir = Path(first["output_dir"])
    second_dir = Path(second["output_dir"])

    assert first["candidate_id"] == second["candidate_id"] == CANDIDATE["candidate_id"]
    assert json.loads(Path(first["files"]["candidate_snapshot"]).read_text()) == CANDIDATE
    assert not first_dir.is_relative_to((tmp_path / "one/repo").resolve())
    assert first_dir != second_dir
    assert stat.S_IMODE(first_dir.stat().st_mode) == 0o700
    assert (
        (first_dir / "acceptance-matrix.md")
        .read_text(encoding="utf-8")
        .startswith("# Release acceptance matrix")
    )
    assert "NOT RELEASE PROOF" in (first_dir / "acceptance-matrix.md").read_text(encoding="utf-8")
    assert (first_dir / "acceptance-evidence.v1.json").read_bytes() == (
        second_dir / "acceptance-evidence.v1.json"
    ).read_bytes()
    assert (first_dir / "acceptance.v1.jsonl").read_bytes() == (
        second_dir / "acceptance.v1.jsonl"
    ).read_bytes()
    for path in first_dir.iterdir():
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert not path.is_symlink()
            payload = path.read_text(encoding="utf-8")
            if path.suffix == ".jsonl":
                assert all(
                    json.loads(line)["candidate_id"] == CANDIDATE["candidate_id"]
                    for line in payload.splitlines()
                )
            elif path.suffix == ".json":
                assert CANDIDATE["candidate_id"] in payload


def test_output_inside_candidate_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(matrix.MatrixAssemblyError, match="outside the candidate root"):
        matrix.assemble(
            root,
            output_dir=root / "release_acceptance/generated",
            candidate_record=CANDIDATE,
            discovery_report=_discovery(),
            test_index_report=_test_index(),
        )


def test_existing_output_is_never_overwritten(tmp_path: Path) -> None:
    first = _assemble(tmp_path, surfaces=[_surface("rest:GET:/fixture")])
    output = Path(first["output_dir"])
    sentinel = output / "acceptance-evidence.v1.json"
    sentinel.write_text("retained original")
    with pytest.raises(matrix.MatrixAssemblyError):
        matrix.assemble(
            tmp_path / "repo",
            output_dir=output,
            candidate_record=CANDIDATE,
            discovery_report=_discovery(),
            test_index_report=_test_index(),
        )
    assert sentinel.read_text() == "retained original"


def test_output_file_symlink_cannot_modify_candidate(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_inputs(root, {"declarations": []}, {"bindings": []})
    sentinel = root / "keep.json"
    sentinel.write_text("candidate canary")
    output = tmp_path / "output"
    output.mkdir()
    (output / "candidate.json").symlink_to(sentinel)
    with pytest.raises(matrix.MatrixAssemblyError):
        matrix.assemble(
            root,
            output_dir=output,
            candidate_record=CANDIDATE,
            discovery_report=_discovery(),
            test_index_report=_test_index(),
        )
    assert sentinel.read_text() == "candidate canary"


def test_candidate_clean_state_comes_from_git_status(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    _write_inputs(root, {"declarations": []}, {"bindings": []})
    candidate = {
        **CANDIDATE,
        "clean": True,
        "git_status": {"clean": False, "paths": ["worktree.txt"]},
    }
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "out",
        candidate_record=candidate,
        discovery_report=_discovery(_surface("rest:GET:/fixture")),
        test_index_report=_test_index(),
    )

    assert result["matrix"]["candidate"]["clean"] is False


def test_collection_receipt_promotes_one_exact_parameterized_node_to_not_run(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "tests/test_parameterized.py"
    source.parent.mkdir()
    source.write_text("def test_parameterized(value):\n    pass\n", encoding="utf-8")
    concrete = "tests/test_parameterized.py::test_parameterized[one]"
    binding = _binding(
        "rest:GET:/fixture",
        "tests/test_parameterized.py:1",
        hashlib.sha256(source.read_bytes()).hexdigest(),
        concrete,
    )
    _write_inputs(root, {"declarations": []}, {"bindings": [binding]})
    report = _collection_report(root, source, {"collected_id": concrete})
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "out",
        candidate_record=CANDIDATE,
        discovery_report=_discovery(_surface("rest:GET:/fixture", "src/app.py:1")),
        test_index_report=_test_index(
            _node("tests/test_parameterized.py::test_parameterized", "tests/test_parameterized.py"),
            templates=[
                {
                    "template_id": "template:tests/test_parameterized.py::test_parameterized:parametrize",
                    "path": "tests/test_parameterized.py",
                    "unresolved_reason": "parameterized",
                }
            ],
        ),
        collection_report=report,
    )

    cases = [
        case
        for case in result["matrix"]["cases"]
        if case["draft"]["kind"] == "source_reviewed_binding"
    ]
    assert len(cases) == 1
    assert cases[0]["status"] == "not_run"
    assert cases[0]["test_node_ids"] == [concrete]
    assert cases[0]["draft"]["runtime_collection_verified"] is True
    assert cases[0]["draft"]["test_source_validation"]["parameterized_template_used"] is True
    assert not any(
        gap["kind"] == "invalid-collection-report" for gap in result["gap_ledger"]["gaps"]
    )


def test_collection_receipt_stale_or_ambiguous_never_promotes_parameterized_binding(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "tests/test_parameterized.py"
    source.parent.mkdir()
    source.write_text("def test_parameterized(value):\n    pass\n", encoding="utf-8")
    base = "tests/test_parameterized.py::test_parameterized"
    binding = _binding(
        "rest:GET:/fixture",
        "tests/test_parameterized.py:1",
        hashlib.sha256(source.read_bytes()).hexdigest(),
        base,
    )
    _write_inputs(root, {"declarations": []}, {"bindings": [binding]})
    test_report = _test_index(
        _node(base, "tests/test_parameterized.py"),
        templates=[
            {
                "template_id": "template:tests/test_parameterized.py::test_parameterized:parametrize",
                "path": "tests/test_parameterized.py",
                "unresolved_reason": "parameterized",
            }
        ],
    )
    ambiguous = _collection_report(
        root, source, {"collected_id": base + "[one]"}, {"collected_id": base + "[two]"}
    )
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "ambiguous",
        candidate_record=CANDIDATE,
        discovery_report=_discovery(_surface("rest:GET:/fixture", "src/app.py:1")),
        test_index_report=test_report,
        collection_report=ambiguous,
    )
    assert not any(
        case["draft"]["kind"] == "source_reviewed_binding" for case in result["matrix"]["cases"]
    )
    assert any(gap["kind"] == "collection-node-not-unique" for gap in result["gap_ledger"]["gaps"])

    stale = _collection_report(root, source, {"collected_id": base + "[one]"})
    stale["items"][0]["source_sha256"] = "0" * 64
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "stale",
        candidate_record=CANDIDATE,
        discovery_report=_discovery(_surface("rest:GET:/fixture", "src/app.py:1")),
        test_index_report=test_report,
        collection_report=stale,
    )
    assert not any(
        case["draft"]["kind"] == "source_reviewed_binding" for case in result["matrix"]["cases"]
    )
    assert any(gap["kind"] == "invalid-collection-report" for gap in result["gap_ledger"]["gaps"])


def test_collection_receipt_path_escape_is_a_gap_and_does_not_read_outside_source(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "tests/test_parameterized.py"
    source.parent.mkdir()
    source.write_text("def test_parameterized(value):\n    pass\n", encoding="utf-8")
    binding = _binding(
        "rest:GET:/fixture",
        "tests/test_parameterized.py:1",
        hashlib.sha256(source.read_bytes()).hexdigest(),
        "tests/test_parameterized.py::test_parameterized[one]",
    )
    _write_inputs(root, {"declarations": []}, {"bindings": [binding]})
    report = _collection_report(
        root, source, {"collected_id": binding["test_node_id"], "source_file": "../outside.py"}
    )
    result = matrix.assemble(
        root,
        output_dir=tmp_path / "out",
        candidate_record=CANDIDATE,
        discovery_report=_discovery(_surface("rest:GET:/fixture", "src/app.py:1")),
        test_index_report=_test_index(
            _node("tests/test_parameterized.py::test_parameterized", "tests/test_parameterized.py"),
            templates=[
                {
                    "template_id": "template:tests/test_parameterized.py::test_parameterized:parametrize",
                    "path": "tests/test_parameterized.py",
                    "unresolved_reason": "parameterized",
                }
            ],
        ),
        collection_report=report,
    )
    assert not any(
        case["draft"]["kind"] == "source_reviewed_binding" for case in result["matrix"]["cases"]
    )
    assert any(gap["kind"] == "invalid-collection-report" for gap in result["gap_ledger"]["gaps"])
