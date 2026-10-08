"""Focused read-only validation tests for recorded run provenance."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import candidate, run_recorder
from tools.release_acceptance.run_provenance import validate_run_record


def _git(root: Path, *args: str, commit: bool = False) -> None:
    command = ["git", "-C", str(root)]
    if commit:
        command += [
            "-c",
            "user.name=Provenance Test",
            "-c",
            "user.email=provenance@example.invalid",
        ]
    subprocess.run(command + list(args), check=True, capture_output=True)


@pytest.fixture
def source_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "uv.lock").write_text("lock\n", encoding="utf-8")
    (root / "tracked.txt").write_text("before\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "initial", commit=True)
    monkeypatch.setattr(
        candidate,
        "_tool_versions",
        lambda: {
            "python": "test",
            "platform": "test",
            "machine": "test",
            "node": None,
            "uv": None,
        },
    )
    return root


def _run(
    source_repo: Path,
    output_root: Path,
    code: str,
    *,
    timeout_s: float = 5,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = run_recorder.record_run(
        [sys.executable, "-c", code],
        cwd=source_repo,
        candidate_root=source_repo,
        output_root=output_root,
        timeout_s=timeout_s,
        terminate_grace_s=0.2,
    )
    saved = json.loads(Path(result["run_record"]).read_text(encoding="utf-8"))
    return result, saved


def _run_with_result(
    source_repo: Path, output_root: Path, report: Path, content: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    return _run_with_result_code(
        source_repo,
        output_root,
        report,
        f"from pathlib import Path; Path({str(report)!r}).write_text({content!r})",
    )


def _run_with_result_code(
    source_repo: Path, output_root: Path, report: Path, code: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = run_recorder.record_run(
        [sys.executable, "-c", code],
        cwd=source_repo,
        candidate_root=source_repo,
        output_root=output_root,
        timeout_s=5,
        result_paths={"junit": report},
    )
    saved = json.loads(Path(result["run_record"]).read_text(encoding="utf-8"))
    return result, saved


def _expected_id(saved: dict[str, Any]) -> str:
    before = json.loads(Path(saved["candidate"]["before"]).read_text(encoding="utf-8"))
    return str(before["candidate_id"])


def _write_receipt(saved: dict[str, Any]) -> None:
    Path(saved["run_record"]).write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")


def test_genuine_record_passes(source_repo: Path, tmp_path: Path) -> None:
    result, saved = _run(source_repo, tmp_path / "evidence", "print('provenance-pass')")
    assert (
        validate_run_record(Path(result["run_record"]), tmp_path / "evidence", _expected_id(saved))
        == []
    )


def test_expected_result_binding_passes(source_repo: Path, tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    report = evidence / "junit.xml"
    _, saved = _run_with_result(source_repo, evidence, report, "<testsuite/>")
    digest = hashlib.sha256(report.read_bytes()).hexdigest()
    assert (
        validate_run_record(
            Path(saved["run_record"]),
            evidence,
            _expected_id(saved),
            expected_result_path=report,
            expected_result_sha256=digest,
        )
        == []
    )


def test_expected_result_binding_rejects_different_report(
    source_repo: Path, tmp_path: Path
) -> None:
    evidence = tmp_path / "evidence"
    report = evidence / "junit.xml"
    other = evidence / "other.xml"
    _, saved = _run_with_result_code(
        source_repo,
        evidence,
        report,
        f"from pathlib import Path; Path({str(other)!r}).write_text('<other/>')",
    )
    errors = validate_run_record(
        Path(saved["run_record"]),
        evidence,
        _expected_id(saved),
        expected_result_path=report,
        expected_result_sha256=hashlib.sha256(b"<other/>").hexdigest(),
    )
    assert any("expected result" in error for error in errors)


def test_expected_result_binding_rejects_hash_tamper(source_repo: Path, tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    report = evidence / "junit.xml"
    _, saved = _run_with_result(source_repo, evidence, report, "<testsuite/>")
    original_digest = hashlib.sha256(report.read_bytes()).hexdigest()
    report.write_text("<tampered/>")
    errors = validate_run_record(
        Path(saved["run_record"]),
        evidence,
        _expected_id(saved),
        expected_result_path=report,
        expected_result_sha256=original_digest,
    )
    assert any("result" in error and "hash" in error for error in errors)


def test_expected_result_binding_rejects_missing_report(source_repo: Path, tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    report = evidence / "junit.xml"
    _, saved = _run_with_result_code(source_repo, evidence, report, "pass")
    errors = validate_run_record(
        Path(saved["run_record"]),
        evidence,
        _expected_id(saved),
        expected_result_path=report,
        expected_result_sha256=hashlib.sha256(b"<testsuite/>").hexdigest(),
    )
    assert any("expected result" in error for error in errors)


def test_expected_result_binding_rejects_escaped_symlink(source_repo: Path, tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    report = evidence / "junit.xml"
    outside = tmp_path / "outside.xml"
    outside.write_text("<outside/>")
    _, saved = _run_with_result_code(
        source_repo,
        evidence,
        report,
        f"from pathlib import Path; Path({str(report)!r}).symlink_to({str(outside)!r})",
    )
    errors = validate_run_record(
        Path(saved["run_record"]),
        evidence,
        _expected_id(saved),
        expected_result_path=report,
        expected_result_sha256=hashlib.sha256(b"<outside/>").hexdigest(),
    )
    assert any("escapes evidence_root" in error or "symlink" in error for error in errors)


def test_stable_deleted_tracked_file_has_valid_tombstone(source_repo: Path, tmp_path: Path) -> None:
    (source_repo / "tracked.txt").unlink()
    result, saved = _run(source_repo, tmp_path / "evidence", "print('deleted candidate')")
    assert (
        validate_run_record(Path(result["run_record"]), tmp_path / "evidence", _expected_id(saved))
        == []
    )


@pytest.mark.parametrize("mutation", ["float_exit", "missing_argv", "unrelated_run_dir"])
def test_invalid_reproduction_metadata_fails(
    source_repo: Path, tmp_path: Path, mutation: str
) -> None:
    result, saved = _run(source_repo, tmp_path / "evidence", "print('valid')")
    if mutation == "float_exit":
        saved["process"]["exit_code"] = 0.0
    elif mutation == "missing_argv":
        saved.pop("argv")
    else:
        saved["run_dir"] = str(tmp_path / "evidence" / "unrelated")
    _write_receipt(saved)
    assert validate_run_record(
        Path(result["run_record"]), tmp_path / "evidence", _expected_id(saved)
    )


def test_stale_candidate_fails(source_repo: Path, tmp_path: Path) -> None:
    _, saved = _run(
        source_repo,
        tmp_path / "evidence",
        "from pathlib import Path; Path('tracked.txt').write_text('after\\n')",
    )
    errors = validate_run_record(
        Path(saved["run_record"]), tmp_path / "evidence", _expected_id(saved)
    )
    assert any("stable" in error or "source_drift" in error for error in errors)


def test_missing_logs_fail(source_repo: Path, tmp_path: Path) -> None:
    _, saved = _run(source_repo, tmp_path / "evidence", "print('missing-log')")
    Path(saved["output"]["stdout"]).unlink()
    errors = validate_run_record(
        Path(saved["run_record"]), tmp_path / "evidence", _expected_id(saved)
    )
    assert any("stdout artifact" in error for error in errors)


def test_hash_mismatch_fails(source_repo: Path, tmp_path: Path) -> None:
    _, saved = _run(source_repo, tmp_path / "evidence", "print('hash-mismatch')")
    Path(saved["output"]["stdout"]).write_bytes(b"tampered\n")
    errors = validate_run_record(
        Path(saved["run_record"]), tmp_path / "evidence", _expected_id(saved)
    )
    assert "stdout hash does not match retained bytes" in errors


def test_tampered_candidate_snapshot_fails(source_repo: Path, tmp_path: Path) -> None:
    _, saved = _run(source_repo, tmp_path / "evidence", "print('candidate-tamper')")
    snapshot_path = Path(saved["candidate"]["before"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["record"]["candidate_id"] = "sha256:" + ("0" * 64)
    snapshot_path.write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    errors = validate_run_record(
        Path(saved["run_record"]), tmp_path / "evidence", _expected_id(saved)
    )
    assert any("candidate_id" in error for error in errors)


def test_escaped_symlink_fails_before_reading_outside_evidence(
    source_repo: Path, tmp_path: Path
) -> None:
    evidence = tmp_path / "evidence"
    _, saved = _run(source_repo, evidence, "print('symlink-check')")
    outside = tmp_path / "outside.log"
    outside.write_bytes(b"outside evidence\n")
    stdout = Path(saved["output"]["stdout"])
    stdout.unlink()
    stdout.symlink_to(outside)
    errors = validate_run_record(Path(saved["run_record"]), evidence, _expected_id(saved))
    assert "stdout artifact escapes evidence_root" in errors


def test_incomplete_receipt_fails(source_repo: Path, tmp_path: Path) -> None:
    _, saved = _run(source_repo, tmp_path / "evidence", "print('incomplete')")
    saved["output"]["complete"] = False
    _write_receipt(saved)
    errors = validate_run_record(
        Path(saved["run_record"]), tmp_path / "evidence", _expected_id(saved)
    )
    assert "retained output is incomplete" in errors


def test_timeout_receipt_fails(source_repo: Path, tmp_path: Path) -> None:
    _, saved = _run(
        source_repo, tmp_path / "evidence", "import time; time.sleep(30)", timeout_s=0.1
    )
    errors = validate_run_record(
        Path(saved["run_record"]), tmp_path / "evidence", _expected_id(saved)
    )
    assert any("completed" in error or "timed_out" in error for error in errors)


def test_receipt_outside_evidence_is_rejected(source_repo: Path, tmp_path: Path) -> None:
    result, saved = _run(source_repo, tmp_path / "evidence", "print('receipt-check')")
    receipt = Path(result["run_record"])
    outside = tmp_path / "outside-receipt.json"
    outside.write_text(receipt.read_text(encoding="utf-8"), encoding="utf-8")
    assert any(
        "run receipt escapes evidence_root" in error
        for error in validate_run_record(outside, tmp_path / "evidence", _expected_id(saved))
    )
