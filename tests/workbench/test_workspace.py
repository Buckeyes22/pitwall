"""Handoff workspace tests, translated from packages/pi-workbench/tests/workspace.test.ts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.workspace import (
    ApprovedCheck,
    capture_patch,
    create_handoff_workspace,
    integrate_handoff,
    load_handoff,
    record_review,
    recover_handoff,
    run_approved_checks,
    update_validation,
)
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.parity

PY = sys.executable
HANDOFFS = ".pi-workbench-handoffs"


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=x", "-c", "user.email=x@example.invalid", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def init_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for name, content in (files or {"base.txt": "base\n"}).items():
        (root / name).write_text(content)
        git(root, "add", name)
    git(root, "commit", "-qm", "base")
    return root


def py_check(script: str, **kwargs: Any) -> ApprovedCheck:
    return ApprovedCheck(command=PY, args=("-c", script), **kwargs)


def reviewed(root: Path, name: str, content: str) -> dict[str, Any]:
    """Create a handoff that changed one file, validated and reviewed."""
    workspace = create_handoff_workspace(str(root))
    (Path(workspace["workspace"]) / name).write_text(content)
    captured = capture_patch(workspace)
    validated = update_validation(captured.manifest, "passed")
    record_review(validated, "REVIEWED")
    return validated


def process_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        status = Path(f"/proc/{pid}/status").read_text()
    except ProcessLookupError, FileNotFoundError:
        return False
    return re.search(r"^State:\s+Z", status, re.MULTILINE) is None


def wait_for_file(path: Path, timeout: float = 5.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return path.read_text()
        except FileNotFoundError:
            time.sleep(0.01)
    raise AssertionError(f"fixture did not become ready: {path}")


def wait_until(condition: Callable[[], bool], timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.02)
    return condition()


def test_handoff_workspace_preserves_dirty_base_and_returns_an_uncommitted_patch(
    tmp_path: Path,
) -> None:
    """Source: workspace.test.ts 'handoff workspace preserves dirty base and returns an uncommitted patch'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    (Path(workspace["workspace"]) / "change.txt").write_text("writer output\n")
    captured = capture_patch(workspace)
    assert "change.txt" in captured.patch
    assert captured.changed_files == ["change.txt"]
    assert (
        load_handoff(str(root), captured.manifest["taskId"])["patchSha256"]
        == captured.manifest["patchSha256"]
    )
    assert git(Path(workspace["workspace"]), "rev-list", "--count", "HEAD").strip() == "1"


def test_rejects_a_tampered_durable_patch_handoff(tmp_path: Path) -> None:
    """Source: 'rejects a tampered durable patch handoff'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    (Path(workspace["workspace"]) / "change.txt").write_text("writer output\n")
    captured = capture_patch(workspace)
    Path(captured.patch_path).write_text("tampered\n")
    with pytest.raises(RuntimeError, match="patch hash mismatch"):
        load_handoff(str(root), captured.manifest["taskId"])


def test_refuses_a_dirty_base_instead_of_silently_omitting_user_changes(tmp_path: Path) -> None:
    """Source: 'refuses a dirty base instead of silently omitting user changes'."""
    root = init_repo(tmp_path)
    (root / "dirty.txt").write_text("preserve\n")
    with pytest.raises(RuntimeError, match="clean base"):
        create_handoff_workspace(str(root))


def test_detects_unstaged_drift(tmp_path: Path) -> None:
    """Source: 'detects unstaged drift and refuses forged handoff paths'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    change = Path(workspace["workspace"]) / "change.txt"
    change.write_text("one\n")
    captured = capture_patch(workspace)
    change.write_text("two\n")
    with pytest.raises(RuntimeError, match="changed since patch capture"):
        load_handoff(str(root), captured.manifest["taskId"])


def test_rejects_a_writer_commit_while_preserving_the_patch_and_final_revision(
    tmp_path: Path,
) -> None:
    """Source: 'rejects a writer commit while preserving the captured patch and final revision evidence'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    worktree = Path(workspace["workspace"])
    (worktree / "committed.txt").write_text("committed\n")
    git(worktree, "add", "committed.txt")
    git(worktree, "commit", "-qm", "unexpected")
    with pytest.raises(RuntimeError, match="unexpected writer commit"):
        capture_patch(workspace)
    manifest = json.loads((worktree.parent / ".pi-workbench-handoff.json").read_text())
    assert manifest["taskId"] == workspace["taskId"]
    assert manifest["revision"] == workspace["revision"]
    assert manifest["status"] == "failed"
    assert manifest["unexpectedCommit"] is True
    assert manifest["acceptance"] == "rejected"
    assert manifest["finalRevision"] != workspace["revision"]
    assert "committed.txt" in (worktree.parent / "handoff.patch").read_text()
    assert (
        load_handoff(str(root), workspace["taskId"])["finalRevision"] == manifest["finalRevision"]
    )


def test_atomic_writer_lock_rejects_overlap_and_invalid_recovery_owner(tmp_path: Path) -> None:
    """Source: 'atomic writer lock rejects overlap and invalid recovery owner'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    with pytest.raises(RuntimeError, match="active writer"):
        create_handoff_workspace(str(root))
    (root / HANDOFFS / ".writer-lock" / "owner").write_text("not-json")
    with pytest.raises(RuntimeError, match="unreadable"):
        recover_handoff(str(root), workspace["taskId"])


def test_terminal_capture_refuses_to_remove_a_lock_owned_by_another_task(tmp_path: Path) -> None:
    """Source: 'terminal capture refuses to remove a lock owned by another task'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    (Path(workspace["workspace"]) / "change.txt").write_text("writer output\n")
    owner = root / HANDOFFS / ".writer-lock" / "owner"
    owner.write_text(json.dumps({"pid": os.getpid(), "taskId": "task-other-000000000000"}))
    with pytest.raises(RuntimeError, match="does not match handoff"):
        capture_patch(workspace)
    assert "task-other-000000000000" in owner.read_text()


def test_approved_validation_records_failure_evidence(tmp_path: Path) -> None:
    """Source: 'approved validation records failure evidence'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    result = run_approved_checks(
        workspace, [py_check("import sys; sys.stderr.write('bad'); sys.exit(3)")]
    )
    assert result["passed"] is False
    assert result["results"][0]["code"] == 3
    assert "bad" in result["results"][0]["stderr"]


def test_approved_validation_refuses_a_non_private_artifact_destination(tmp_path: Path) -> None:
    """Source: 'approved validation refuses a non-private artifact destination before launching a check'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    directory = Path(workspace["workspace"]).parent / "artifacts"
    directory.mkdir()
    directory.chmod(0o755)
    marker = tmp_path / "launched"
    with pytest.raises(RuntimeError, match="private regular directory"):
        run_approved_checks(workspace, [py_check(f"open({str(marker)!r}, 'w')")])
    assert not marker.exists()


def test_approved_validation_streams_complete_large_output_and_preserves_the_tail(
    tmp_path: Path,
) -> None:
    """Source: 'approved validation streams complete large output and preserves the failure tail in its bounded view'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    script = (
        "import sys; sys.stdout.write('O' * 1000000); sys.stdout.flush(); "
        "sys.stderr.write('E' * 1000000 + 'TAIL_FAILURE'); sys.exit(7)"
    )
    result = run_approved_checks(workspace, [py_check(script)])
    check = result["results"][0]
    assert result["passed"] is False
    assert check["code"] == 7
    assert "TAIL_FAILURE" in check["stderr"]
    assert check["stdoutArtifact"]["truncated"] is True
    assert check["stderrArtifact"]["truncated"] is True
    full_stdout = Path(check["stdoutArtifact"]["path"]).read_bytes()
    full_stderr = Path(check["stderrArtifact"]["path"]).read_bytes()
    assert len(full_stdout) == 1000000
    assert set(full_stdout) == {ord("O")}
    assert len(full_stderr) == 1000012
    assert full_stderr.endswith(b"TAIL_FAILURE")
    assert check["stdoutArtifact"]["sha256"] == hashlib.sha256(full_stdout).hexdigest()
    assert check["stderrArtifact"]["sha256"] == hashlib.sha256(full_stderr).hexdigest()
    validation = json.loads((Path(workspace["workspace"]).parent / "validation.json").read_text())
    recorded = validation["results"][0]
    assert recorded["code"] == 7
    assert recorded["stderrArtifact"]["path"] == check["stderrArtifact"]["path"]
    assert recorded["stderrArtifact"]["sha256"] == check["stderrArtifact"]["sha256"]
    assert recorded["stderrArtifact"]["truncated"] is True


def test_approved_validation_preserves_short_multibyte_output_and_fresh_artifact_directory(
    tmp_path: Path,
) -> None:
    """Source: 'approved validation preserves short multibyte output and creates a fresh artifact directory on repeat'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    expected = "\U0001f600X\U0001f600" * 500
    script = f"import os; [os.write(1, bytes([b])) for b in {expected!r}.encode('utf-8')]"
    first = run_approved_checks(workspace, [py_check(script)])
    second = run_approved_checks(workspace, [py_check(script)])
    assert first["passed"] is True
    assert second["passed"] is True
    assert first["results"][0]["stdout"] == expected
    assert first["results"][0]["stdoutArtifact"]["truncated"] is False
    assert second["results"][0]["stdout"] == expected
    assert (
        second["results"][0]["stdoutArtifact"]["path"]
        != first["results"][0]["stdoutArtifact"]["path"]
    )
    assert Path(first["results"][0]["stdoutArtifact"]["path"]).read_text() == expected
    assert Path(second["results"][0]["stdoutArtifact"]["path"]).read_text() == expected


def test_approved_validation_records_a_passing_content_check_with_the_unchanged_patch_hash(
    tmp_path: Path,
) -> None:
    """Source: 'approved validation records a passing content check with the unchanged patch hash'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    (Path(workspace["workspace"]) / "change.txt").write_text("expected\n")
    captured = capture_patch(workspace, "active")
    script = "import sys; sys.exit(0 if open('change.txt').read() == 'expected\\n' else 1)"
    result = run_approved_checks(captured.manifest, [py_check(script)])
    assert result["passed"] is True
    assert result["results"][0]["code"] == 0
    evidence = json.loads(
        (Path(captured.manifest["workspace"]).parent / "validation.json").read_text()
    )
    patch = Path(captured.patch_path).read_bytes()
    assert evidence["taskId"] == captured.manifest["taskId"]
    assert evidence["revision"] == captured.manifest["revision"]
    assert evidence["patchSha256"] == hashlib.sha256(patch).hexdigest()
    assert any(entry["command"] == PY and entry["code"] == 0 for entry in evidence["results"])
    assert (
        load_handoff(str(root), captured.manifest["taskId"])["patchSha256"]
        == evidence["patchSha256"]
    )


def test_approved_checks_get_scrubbed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ledger E-10: approved checks run with restricted_tool_environment, not the full parent environment."""
    monkeypatch.setenv("PITWALL_FIXTURE_API_KEY", "fixture-value")  # pragma: allowlist secret
    monkeypatch.setenv("PITWALL_FIXTURE_TOKEN", "fixture-value")  # pragma: allowlist secret
    monkeypatch.setenv("PITWALL_FIXTURE_PLAIN", "kept")
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    script = "import json, os; print(json.dumps(sorted(os.environ)))"
    result = run_approved_checks(workspace, [py_check(script)])
    names = json.loads(result["results"][0]["stdout"])
    assert "PITWALL_FIXTURE_API_KEY" not in names
    assert "PITWALL_FIXTURE_TOKEN" not in names
    assert "PITWALL_FIXTURE_PLAIN" in names
    assert "PATH" in names


def test_explicit_handoff_integration_serializes_checks_base_identity_and_does_not_commit(
    tmp_path: Path,
) -> None:
    """Source: 'explicit handoff integration serializes, checks exact base identity, and reruns approved checks without committing'."""
    root = init_repo(tmp_path)
    validated = reviewed(root, "worker.txt", "INTEGRATED\n")
    script = "import sys; sys.exit(0 if open('worker.txt').read() == 'INTEGRATED\\n' else 1)"
    integrated = integrate_handoff(str(root), validated["taskId"], [py_check(script)])
    assert integrated["status"] == "applied"
    assert integrated["checks"]["passed"] is True
    assert "post-apply-" in integrated["checks"]["validationPath"]
    assert (root / "worker.txt").read_text() == "INTEGRATED\n"
    assert git(root, "rev-list", "--count", "HEAD").strip() == "1"
    assert "?? worker.txt" in git(root, "status", "--porcelain", "--", "worker.txt")
    record = json.loads(Path(integrated["recordPath"]).read_text())
    assert record["status"] == "applied"
    assert record["taskId"] == validated["taskId"]
    assert record["patchSha256"] == validated["patchSha256"]


def test_failed_post_apply_checks_retain_the_uncommitted_patch_and_failure_evidence(
    tmp_path: Path,
) -> None:
    """Source: 'failed post-apply checks retain the uncommitted patch and durable failure evidence'."""
    root = init_repo(tmp_path)
    validated = reviewed(root, "worker.txt", "RETAIN\n")
    integrated = integrate_handoff(
        str(root),
        validated["taskId"],
        [py_check("import sys; sys.stderr.write('post-apply failure'); sys.exit(9)")],
    )
    assert integrated["status"] == "failed"
    assert "retained" in integrated["error"]
    assert integrated["checks"]["passed"] is False
    assert integrated["errorArtifact"]["complete"] is True
    assert (root / "worker.txt").read_text() == "RETAIN\n"
    assert (
        "post-apply approved checks failed" in Path(integrated["errorArtifact"]["path"]).read_text()
    )


def test_a_passing_post_apply_check_that_mutates_the_target_cannot_claim_acceptance(
    tmp_path: Path,
) -> None:
    """Source: 'a passing post-apply check that mutates the target cannot claim the reviewed patch was accepted'."""
    root = init_repo(tmp_path)
    validated = reviewed(root, "worker.txt", "REVIEWED\n")
    integrated = integrate_handoff(
        str(root),
        validated["taskId"],
        [py_check("open('worker.txt', 'w').write('UNREVIEWED\\n')")],
    )
    assert integrated["status"] == "failed"
    assert integrated["checks"]["passed"] is True
    assert integrated["postApplyPatchMatches"] is False
    assert "changed the target beyond" in integrated["error"]
    assert (root / "worker.txt").read_text() == "UNREVIEWED\n"
    assert git(root, "diff", "--cached") == ""


def test_a_post_apply_check_that_commits_is_rejected_as_an_unexpected_target_revision(
    tmp_path: Path,
) -> None:
    """Source: 'a post-apply check that commits is rejected as an unexpected target revision'."""
    root = init_repo(tmp_path)
    validated = reviewed(root, "worker.txt", "COMMIT\n")
    script = (
        "import subprocess; "
        "subprocess.run(['git', 'add', 'worker.txt'], check=True); "
        "subprocess.run(['git', '-c', 'user.name=check', '-c', 'user.email=check@example.invalid', "
        "'commit', '-qm', 'postcheck'], check=True)"
    )
    integrated = integrate_handoff(str(root), validated["taskId"], [py_check(script)])
    assert integrated["status"] == "failed"
    assert "target revision changed" in integrated["error"]
    assert git(root, "rev-list", "--count", "HEAD").strip() == "2"


def test_integration_refuses_dirty_or_advanced_targets_and_preserves_unrelated_work(
    tmp_path: Path,
) -> None:
    """Source: 'integration refuses dirty or advanced targets and preserves unrelated target work'."""
    root = init_repo(tmp_path)
    validated = reviewed(root, "worker.txt", "PATCH\n")
    (root / "unrelated.txt").write_text("KEEP\n")
    checks = [py_check("pass")]
    dirty = integrate_handoff(str(root), validated["taskId"], checks)
    assert dirty["status"] == "conflict"
    assert "dirty" in dirty["error"]
    assert (root / "unrelated.txt").read_text() == "KEEP\n"
    assert not (root / "worker.txt").exists()
    git(root, "add", "unrelated.txt")
    git(root, "commit", "-qm", "unrelated")
    advanced = integrate_handoff(str(root), validated["taskId"], checks)
    assert advanced["status"] == "conflict"
    assert "target base changed" in advanced["error"]
    assert not (root / "worker.txt").exists()


def test_overlapping_handoff_integration_rejects_the_target_and_preserves_both_sides(
    tmp_path: Path,
) -> None:
    """Source: 'overlapping handoff integration rejects the target and preserves both sides' files'."""
    root = init_repo(tmp_path)
    validated = reviewed(root, "shared.txt", "HANDOFF\n")
    (root / "shared.txt").write_text("TARGET\n")
    (root / "unrelated.txt").write_text("KEEP\n")
    result = integrate_handoff(str(root), validated["taskId"], [py_check("pass")])
    assert result["status"] == "conflict"
    assert "dirty" in result["error"]
    assert (root / "shared.txt").read_text() == "TARGET\n"
    assert (root / "unrelated.txt").read_text() == "KEEP\n"
    assert "shared.txt" in git(root, "status", "--porcelain", "--", "shared.txt", "unrelated.txt")


def test_sequential_owned_handoffs_with_overlapping_patches_preserve_first_result(
    tmp_path: Path,
) -> None:
    """Source: 'sequential owned handoffs with overlapping patches preserve the applied first result and second worktree'."""
    root = init_repo(tmp_path, {"shared.txt": "BASE\n"})
    first = create_handoff_workspace(str(root))
    (Path(first["workspace"]) / "shared.txt").write_text("FIRST\n")
    first_validated = update_validation(capture_patch(first).manifest, "passed")
    record_review(first_validated, "FIRST REVIEW")
    second = create_handoff_workspace(str(root))
    (Path(second["workspace"]) / "shared.txt").write_text("SECOND\n")
    second_captured = capture_patch(second)
    second_validated = update_validation(second_captured.manifest, "passed")
    record_review(second_validated, "SECOND REVIEW")
    checks = [py_check("pass")]
    assert integrate_handoff(str(root), first_validated["taskId"], checks)["status"] == "applied"
    assert (root / "shared.txt").read_text() == "FIRST\n"
    second_result = integrate_handoff(str(root), second_validated["taskId"], checks)
    assert second_result["status"] == "conflict"
    assert "dirty" in second_result["error"]
    assert (root / "shared.txt").read_text() == "FIRST\n"
    assert (Path(second["workspace"]) / "shared.txt").read_text() == "SECOND\n"
    assert "SECOND" in Path(second_captured.patch_path).read_text()


def test_two_concurrent_integrations_cannot_bypass_the_target_lease(tmp_path: Path) -> None:
    """Source: 'two concurrent integrations cannot bypass the target lease'."""
    root = init_repo(tmp_path)
    validated = reviewed(root, "worker.txt", "LOCKED\n")
    slow = [py_check("import time; time.sleep(1.5)")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(integrate_handoff, str(root), validated["taskId"], slow),
            pool.submit(integrate_handoff, str(root), validated["taskId"], slow),
        ]
        outcomes: list[Any] = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except RuntimeError as error:
                outcomes.append(error)
    successes = [item for item in outcomes if isinstance(item, dict)]
    failures = [item for item in outcomes if isinstance(item, RuntimeError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert "another handoff integration" in str(failures[0])
    assert (root / "worker.txt").read_text() == "LOCKED\n"


def group_fixture(pid_path: Path, delayed_path: Path) -> str:
    """A shell check that leaves a same-group descendant which would write a file late."""
    return f"sleep 1.6 && echo LATE > '{delayed_path}' & echo $! > '{pid_path}'; wait"


def test_approved_checks_kill_owned_descendants_on_timeout_and_abort(tmp_path: Path) -> None:
    """Source: 'approved checks kill owned descendants on timeout and abort while retaining failure logs'.

    Ledger E-09: the fixture pid file is awaited before any liveness assertion.
    """
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    (Path(workspace["workspace"]) / "change.txt").write_text("expected\n")
    captured = capture_patch(workspace, "active")

    timeout_pid = tmp_path / "timeout.pid"
    timeout_delayed = tmp_path / "timeout-delayed.txt"
    started = time.monotonic()
    result = run_approved_checks(
        captured.manifest,
        [ApprovedCheck("sh", ("-c", group_fixture(timeout_pid, timeout_delayed)), timeout_ms=800)],
    )
    assert result["passed"] is False
    assert result["results"][0]["code"] == 124
    assert "timed out" in result["results"][0]["stderr"]
    assert Path(result["results"][0]["stderrArtifact"]["path"]).read_text() == ""
    timeout_child = int(wait_for_file(timeout_pid).strip())
    assert wait_until(lambda: not process_running(timeout_child))
    time.sleep(max(0.0, 2.0 - (time.monotonic() - started)))
    assert not timeout_delayed.exists()

    abort_pid = tmp_path / "abort.pid"
    abort_delayed = tmp_path / "abort-delayed.txt"
    abort = threading.Event()
    outcome: dict[str, Any] = {}
    started = time.monotonic()

    def run() -> None:
        outcome["result"] = run_approved_checks(
            captured.manifest,
            [
                ApprovedCheck(
                    "sh", ("-c", group_fixture(abort_pid, abort_delayed)), timeout_ms=30_000
                )
            ],
            abort,
        )

    thread = threading.Thread(target=run)
    thread.start()
    wait_for_file(abort_pid)
    abort.set()
    thread.join(timeout=HANG_GUARD_SECS)
    assert not thread.is_alive()
    abort_result = outcome["result"]
    assert abort_result["passed"] is False
    assert abort_result["results"][0]["code"] == 130
    assert "aborted" in abort_result["results"][0]["stderr"]
    assert Path(abort_result["results"][0]["stderrArtifact"]["path"]).read_text() == ""
    abort_child = int(abort_pid.read_text().strip())
    assert wait_until(lambda: not process_running(abort_child))
    time.sleep(max(0.0, 2.0 - (time.monotonic() - started)))
    assert not abort_delayed.exists()


def test_record_review_stores_the_complete_report_and_exposes_only_a_bounded_view(
    tmp_path: Path,
) -> None:
    """Source: 'recordReview stores the complete report and exposes only a bounded view'."""
    root = init_repo(tmp_path)
    workspace = create_handoff_workspace(str(root))
    (Path(workspace["workspace"]) / "change.txt").write_text("review\n")
    captured = capture_patch(workspace)
    report = "R" * 9000 + "TAIL_REVIEW"
    record = record_review(captured.manifest, report)
    assert record["reportArtifact"]["truncated"] is True
    assert "TAIL_REVIEW" in record["report"]
    assert Path(record["reportArtifact"]["path"]).read_text() == report
    assert record["reportArtifact"]["sha256"] == hashlib.sha256(report.encode()).hexdigest()
    saved = json.loads((Path(workspace["workspace"]).parent / "review.json").read_text())
    assert saved["reportArtifact"]["path"] == record["reportArtifact"]["path"]
    assert saved["reportArtifact"]["sha256"] == record["reportArtifact"]["sha256"]
    assert saved["reportArtifact"]["truncated"] is True


def spawn_writer(root: Path, body: str) -> dict[str, Any]:
    """Run a writer host in a child process, read its metadata line, then SIGKILL it."""
    script = (
        textwrap.dedent(
            """
        import json, sys, time
        from pathlib import Path
        from pitwall.workbench.workspace import capture_patch, create_handoff_workspace

        workspace = create_handoff_workspace(sys.argv[1])
        """
        )
        + textwrap.dedent(body)
        + "\ntime.sleep(60)\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(root)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        line = child.stdout.readline()
        assert line, f"crash writer exited before metadata: {child.poll()}"
        metadata: dict[str, Any] = json.loads(line)
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait()
        if child.stdout:
            child.stdout.close()
        if child.stderr:
            child.stderr.close()
    return metadata


def test_recovers_a_captured_handoff_after_its_writer_host_dies_and_keeps_it_reviewable(
    tmp_path: Path,
) -> None:
    """Source: 'recovers a captured handoff after its writer host dies and keeps it reviewable'."""
    root = init_repo(tmp_path)
    metadata = spawn_writer(
        root,
        """
        (Path(workspace["workspace"]) / "crashed.txt").write_text("preserve after crash\\n")
        captured = capture_patch(workspace, "active")
        print(json.dumps({"taskId": captured.manifest["taskId"], "patchSha256": captured.manifest["patchSha256"]}), flush=True)
        """,
    )
    recovered = recover_handoff(str(root), metadata["taskId"])
    assert recovered["taskId"] == metadata["taskId"]
    assert recovered["status"] == "failed"
    assert recovered["patchSha256"] == metadata["patchSha256"]
    assert recovered["baseRef"] == "HEAD"
    assert (Path(recovered["workspace"]) / "crashed.txt").read_text() == "preserve after crash\n"
    assert "crashed.txt" in Path(recovered["patchPath"]).read_text()
    assert load_handoff(str(root), metadata["taskId"])["patchSha256"] == metadata["patchSha256"]
    record_review(recovered, "RECOVERED_REVIEW")
    review = json.loads((Path(recovered["workspace"]).parent / "review.json").read_text())
    assert review["taskId"] == metadata["taskId"]
    assert review["patchSha256"] == metadata["patchSha256"]
    assert review["report"] == "RECOVERED_REVIEW"
    assert metadata["taskId"] in (root / HANDOFFS / ".writer-lock" / "owner").read_text()
    with pytest.raises(RuntimeError, match="active writer"):
        create_handoff_workspace(str(root))


def test_marks_a_writer_failed_when_the_host_dies_before_patch_capture(tmp_path: Path) -> None:
    """Source: 'marks a writer failed when the host dies before patch capture and preserves dirty files'."""
    root = init_repo(tmp_path)
    metadata = spawn_writer(
        root,
        """
        worktree = Path(workspace["workspace"])
        (worktree / "base.txt").write_text("tracked mutation before capture\\n")
        (worktree / "before-capture.txt").write_text("preserve before capture\\n")
        print(json.dumps({"taskId": workspace["taskId"]}), flush=True)
        """,
    )
    recovered = recover_handoff(str(root), metadata["taskId"])
    assert recovered["status"] == "failed"
    assert recovered["validation"] == "not-run"
    assert recovered["review"] == "pending"
    assert recovered["acceptance"] == "pending"
    assert "patchPath" not in recovered
    assert "patchSha256" not in recovered
    handoff_root = root / HANDOFFS
    directory = next(
        p for p in handoff_root.iterdir() if p.name.startswith(f"{metadata['taskId']}-")
    )
    assert (root / "base.txt").read_text() == "base\n"
    assert (directory / "worktree" / "base.txt").read_text() == "tracked mutation before capture\n"
    assert (
        directory / "worktree" / "before-capture.txt"
    ).read_text() == "preserve before capture\n"
    with pytest.raises(RuntimeError, match="active writer handoff already exists"):
        create_handoff_workspace(str(root))
