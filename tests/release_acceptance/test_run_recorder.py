"""Hermetic subprocess and provenance checks for the release run recorder."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from tools.release_acceptance import candidate, run_recorder


def _git(root: Path, *args: str, commit: bool = False) -> None:
    command = ["git", "-C", str(root)]
    if commit:
        command += ["-c", "user.name=Recorder Test", "-c", "user.email=recorder@example.invalid"]
    subprocess.run(command + list(args), check=True, capture_output=True)


@pytest.fixture
def source_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "uv.lock").write_text("lock\n")
    (root / "tracked.txt").write_text("before\n")
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


#: Timeout for tests whose recorded program must finish its setup before the timeout fires:
#: long enough for interpreter start-up on a loaded machine, far below the programs' 30 s sleep.
_SETUP_SAFE_TIMEOUT_S = 3.0


def _run(
    source_repo: Path,
    output_root: Path,
    code: str,
    *,
    timeout_s: float = 5,
    secret_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    return run_recorder.record_run(
        [sys.executable, "-c", code],
        cwd=source_repo,
        candidate_root=source_repo,
        output_root=output_root,
        timeout_s=timeout_s,
        terminate_grace_s=0.2,
        env={"RECORDER_FIXTURE_ENV": "fixture-value"},
        env_allowlist=("RECORDER_FIXTURE_ENV",),
        secret_values=secret_values,
    )


def _read_record(record: dict[str, Any]) -> dict[str, Any]:
    return json.loads(Path(record["run_record"]).read_text())


def test_success_retains_outputs_hashes_metadata_and_no_acceptance(
    source_repo: Path, tmp_path: Path
) -> None:
    output_root = tmp_path / "evidence"
    record = _run(
        source_repo,
        output_root,
        "import os; print('stdout-value'); print('stderr-value', file=__import__('sys').stderr); print(os.environ['RECORDER_FIXTURE_ENV'])",
    )
    saved = _read_record(record)
    assert record["outcome"] == "completed"
    assert saved["process"]["exit_code"] == 0
    assert saved["acceptance_status"] == "not_evaluated"
    assert saved["shell"] is False
    assert saved["argv"][:2] == [sys.executable, "-c"]
    assert saved["environment"] == {"allowlisted_names": ["RECORDER_FIXTURE_ENV"]}
    stdout = Path(saved["output"]["stdout"]).read_bytes()
    stderr = Path(saved["output"]["stderr"]).read_bytes()
    assert b"stdout-value" in stdout and b"fixture-value" in stdout
    assert b"stderr-value" in stderr
    assert saved["output"]["stdout_sha256"] == hashlib.sha256(stdout).hexdigest()
    assert saved["output"]["stderr_sha256"] == hashlib.sha256(stderr).hexdigest()
    assert saved["candidate"]["status"] == "stable"
    assert saved["candidate"]["source_drift"] is False
    assert Path(saved["candidate"]["before"]).exists()
    assert Path(saved["candidate"]["after"]).exists()
    assert (Path(record["run_dir"]).stat().st_mode & 0o077) == 0


def test_result_path_retains_report_and_initial_request(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_root = tmp_path / "evidence"
    report = output_root / "junit.xml"
    initial_records: list[dict[str, Any]] = []
    original_write_json = run_recorder._write_json

    def capture_initial(path: Path, value: Any, secrets_to_redact: tuple[bytes, ...]) -> None:
        if path.name == "run.json" and value.get("outcome") == "running":
            initial_records.append(json.loads(json.dumps(value)))
        original_write_json(path, value, secrets_to_redact)

    monkeypatch.setattr(run_recorder, "_write_json", capture_initial)
    record = run_recorder.record_run(
        [
            sys.executable,
            "-c",
            f"from pathlib import Path; Path({str(report)!r}).write_text('<testsuite/>')",
        ],
        cwd=source_repo,
        candidate_root=source_repo,
        output_root=output_root,
        timeout_s=5,
        result_paths={"junit": report},
    )
    saved = _read_record(record)
    assert initial_records[0]["result_paths"]["junit"]["path"] == str(report)
    result = saved["result_paths"]["junit"]
    assert result["path"] == str(report)
    assert result["status"] == "retained"
    assert result["size"] == len(b"<testsuite/>")
    assert result["sha256"] == hashlib.sha256(b"<testsuite/>").hexdigest()


def test_result_path_must_be_fresh_and_inside_output_root(
    source_repo: Path, tmp_path: Path
) -> None:
    output_root = tmp_path / "evidence"
    existing = output_root / "existing.xml"
    existing.parent.mkdir()
    existing.write_text("old")
    with pytest.raises(run_recorder.RunRecorderError, match="already exists"):
        run_recorder.record_run(
            [sys.executable, "-c", "raise SystemExit('must not run')"],
            cwd=source_repo,
            candidate_root=source_repo,
            output_root=output_root,
            timeout_s=5,
            result_paths={"existing": existing},
        )
    for label, path in (
        ("source", source_repo / "report.xml"),
        ("outside", tmp_path / "outside.xml"),
        ("relative", Path("report.xml")),
    ):
        with pytest.raises(run_recorder.RunRecorderError):
            run_recorder.record_run(
                [sys.executable, "-c", "pass"],
                cwd=source_repo,
                candidate_root=source_repo,
                output_root=output_root,
                timeout_s=5,
                result_paths={label: path},
            )


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("pass", "missing"),
        ("from pathlib import Path; Path({path}).mkdir()", "nonregular"),
    ],
)
def test_unresolved_result_paths_are_retained_without_reading(
    source_repo: Path, tmp_path: Path, code: str, reason: str
) -> None:
    output_root = tmp_path / "evidence"
    report = output_root / "junit.xml"
    child_code = code.format(path=repr(str(report))) if "{path}" in code else code
    record = run_recorder.record_run(
        [sys.executable, "-c", child_code],
        cwd=source_repo,
        candidate_root=source_repo,
        output_root=output_root,
        timeout_s=5,
        result_paths={"junit": report},
    )
    result = _read_record(record)["result_paths"]["junit"]
    assert result["status"] == "unresolved"
    assert result["reason"] == reason


def test_escaped_result_symlink_is_unresolved(source_repo: Path, tmp_path: Path) -> None:
    output_root = tmp_path / "evidence"
    report = output_root / "junit.xml"
    outside = tmp_path / "outside.xml"
    outside.write_text("outside")
    code = f"from pathlib import Path; Path({str(report)!r}).symlink_to({str(outside)!r})"
    record = run_recorder.record_run(
        [sys.executable, "-c", code],
        cwd=source_repo,
        candidate_root=source_repo,
        output_root=output_root,
        timeout_s=5,
        result_paths={"junit": report},
    )
    result = _read_record(record)["result_paths"]["junit"]
    assert result["status"] == "unresolved"
    assert result["reason"] == "escaped"


def test_failure_retains_nonzero_evidence(source_repo: Path, tmp_path: Path) -> None:
    record = _run(
        source_repo, tmp_path / "evidence", "print('failure-output'); raise SystemExit(7)"
    )
    saved = _read_record(record)
    assert record["outcome"] == "failed"
    assert saved["process"]["exit_code"] == 7
    assert b"failure-output" in Path(saved["output"]["stdout"]).read_bytes()
    assert Path(saved["run_record"]).exists()


def test_spawn_error_still_finalizes_receipt_and_logs(source_repo: Path, tmp_path: Path) -> None:
    record = run_recorder.record_run(
        [str(tmp_path / "missing-command")],
        cwd=source_repo,
        candidate_root=source_repo,
        output_root=tmp_path / "evidence",
        timeout_s=1,
    )
    saved = _read_record(record)
    assert record["outcome"] == "spawn_error"
    assert saved["spawn_error"]
    assert saved["ended_at"]
    assert Path(saved["run_record"]).exists()
    assert Path(saved["output"]["stdout"]).exists()
    assert Path(saved["output"]["stderr"]).exists()


def test_timeout_terminates_owned_process_group_and_retains_evidence(
    source_repo: Path, tmp_path: Path
) -> None:
    # The timeout must fire after the program has printed; 0.1 s raced interpreter start-up
    # on a loaded machine. The program still sleeps far past the timeout.
    record = _run(
        source_repo,
        tmp_path / "evidence",
        "import time; print('before-timeout', flush=True); time.sleep(30)",
        timeout_s=_SETUP_SAFE_TIMEOUT_S,
    )
    saved = _read_record(record)
    assert record["outcome"] == "timeout"
    assert saved["process"]["timed_out"] is True
    assert saved["process"]["termination"]["term_sent"] is True
    assert saved["process"]["exit_code"] is not None
    assert saved["output"]["complete"] is True
    assert saved["output"]["loss_reason"] is None
    assert b"before-timeout" in Path(saved["output"]["stdout"]).read_bytes()
    with pytest.raises(ProcessLookupError):
        os.kill(saved["process"]["pid"], 0)


def test_timeout_closes_pipes_when_descendant_escapes_owned_group(
    source_repo: Path, tmp_path: Path
) -> None:
    pid_file = tmp_path / "escaped.pid"
    ready_file = tmp_path / "escaped.ready"
    # The escaped descendant must exist before the timeout fires, or there is nothing left to
    # clean up and the test measures its own start-up race (it failed that way under load with
    # a 0.1 s timeout). The parent waits for the child's setsid() before it prints.
    code = f"""
import pathlib, subprocess, sys, time
ready = pathlib.Path({str(ready_file)!r})
child = subprocess.Popen(
    [
        sys.executable,
        "-c",
        "import os, pathlib, sys, time; os.setsid(); pathlib.Path(sys.argv[1]).write_text('ready'); time.sleep(30)",
        str(ready),
    ],
    stdout=sys.stdout,
    stderr=sys.stderr,
)
pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))
while not ready.exists():
    time.sleep(0.01)
print("parent-before-timeout", flush=True)
time.sleep(30)
"""
    try:
        started = time.monotonic()
        record = _run(source_repo, tmp_path / "evidence", code, timeout_s=_SETUP_SAFE_TIMEOUT_S)
        elapsed = time.monotonic() - started
        saved = _read_record(record)
        assert ready_file.exists(), "the descendant had not escaped before the timeout fired"
        # Cleanup must not wait out the escaped descendant's 30 s sleep.
        assert elapsed < _SETUP_SAFE_TIMEOUT_S + 2.0
        assert record["outcome"] == "timeout"
        assert saved["process"]["termination"]["cleanup_unresolved"] is True
        assert saved["process"]["termination"]["pipes_closed"] is True
        assert saved["output"]["complete"] is False
        assert saved["output"]["loss_reason"]
        assert b"parent-before-timeout" in Path(saved["output"]["stdout"]).read_bytes()
    finally:
        if pid_file.exists():
            escaped_pid = int(pid_file.read_text())
            with suppress(ProcessLookupError):
                os.kill(escaped_pid, signal.SIGKILL)


def test_keyboard_interrupt_finalizes_receipt_then_preserves_interrupt(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupt_while_collecting(*_args: Any, **_kwargs: Any) -> tuple[bytes, bytes, bool]:
        raise KeyboardInterrupt

    monkeypatch.setattr(run_recorder, "_communicate", interrupt_while_collecting)
    output_root = tmp_path / "evidence"
    with pytest.raises(KeyboardInterrupt):
        run_recorder.record_run(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=source_repo,
            candidate_root=source_repo,
            output_root=output_root,
            timeout_s=5,
            terminate_grace_s=0.1,
        )
    run_dirs = list(output_root.glob("run-*"))
    assert len(run_dirs) == 1
    saved = json.loads((run_dirs[0] / "run.json").read_text())
    assert saved["outcome"] == "interrupted"
    assert saved["ended_at"]
    assert saved["interrupted"] is True
    assert saved["process"]["termination"]["term_sent"] is True
    assert saved["process"]["termination"]["pipes_closed"] is True
    assert saved["output"]["complete"] is False
    assert saved["output"]["loss_reason"]
    with pytest.raises(ProcessLookupError):
        os.kill(saved["process"]["pid"], 0)


def _run_sigint_wrapper(
    source_repo: Path, output_root: Path, *, signals: int = 1
) -> subprocess.CompletedProcess[bytes]:
    """Run record_run in a child interpreter whose recorded child SIGINTs it."""
    child_code = (
        "import os, signal, sys, time; "
        "print('interrupt-stdout', flush=True); "
        "print('interrupt-stderr', file=sys.stderr, flush=True); "
        f"[(os.kill(os.getppid(), signal.SIGINT), time.sleep(0.05)) for _ in range({signals})]; "
        "time.sleep(30)"
    )
    candidate_record = {
        "candidate_id": "sha256:test-candidate",
        "git": {"commit": "abc123", "error": None},
        "git_status": {"clean": True},
        "manifest": [],
        "issues": [],
    }
    wrapper = (
        "from pathlib import Path; "
        "import sys; "
        "from tools.release_acceptance import run_recorder; "
        f"run_recorder.candidate.build_candidate=lambda *a, **k: {candidate_record!r}\n"
        "try:\n"
        f"    run_recorder.record_run([{sys.executable!r}, '-c', {child_code!r}], "
        f"cwd=Path({str(source_repo)!r}), candidate_root=Path({str(source_repo)!r}), "
        f"output_root=Path({str(output_root)!r}), timeout_s=5, terminate_grace_s=0.2)\n"
        "except KeyboardInterrupt:\n"
        "    sys.exit(130)\n"
    )
    return subprocess.run(
        [sys.executable, "-c", wrapper],
        cwd=source_repo,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[2])
            + os.pathsep
            + os.environ.get("PYTHONPATH", ""),
        },
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_real_sigint_retain_flushed_output_before_re_raise(
    source_repo: Path, tmp_path: Path
) -> None:
    output_root = tmp_path / "sigint-evidence"
    completed = _run_sigint_wrapper(source_repo, output_root)
    assert completed.returncode != 0
    run_dirs = list(output_root.glob("run-*"))
    assert len(run_dirs) == 1
    saved = json.loads((run_dirs[0] / "run.json").read_text())
    assert saved["outcome"] == "interrupted"
    assert saved["interrupted"] is True
    assert saved["process"]["termination"]["term_sent"] is True
    assert saved["process"]["termination"]["pipes_closed"] is True
    assert saved["output"]["complete"] is True
    assert saved["output"]["loss_reason"] is None
    assert b"interrupt-stdout" in Path(saved["output"]["stdout"]).read_bytes()
    assert b"interrupt-stderr" in Path(saved["output"]["stderr"]).read_bytes()


def test_sigint_never_loses_flushed_output_across_repeats(
    source_repo: Path, tmp_path: Path
) -> None:
    # The race needs many attempts to show; 30 is enough to fail the old code.
    for attempt in range(30):
        output_root = tmp_path / f"sigint-{attempt}"
        completed = _run_sigint_wrapper(source_repo, output_root)
        assert completed.returncode != 0
        [run_dir] = list(output_root.glob("run-*"))
        saved = json.loads((run_dir / "run.json").read_text())
        assert saved["outcome"] == "interrupted"
        assert saved["output"]["complete"] is True
        assert b"interrupt-stdout" in Path(saved["output"]["stdout"]).read_bytes(), attempt


def test_second_sigint_during_collection_still_writes_a_receipt(
    source_repo: Path, tmp_path: Path
) -> None:
    output_root = tmp_path / "double-sigint"
    completed = _run_sigint_wrapper(source_repo, output_root, signals=2)
    assert completed.returncode != 0
    [run_dir] = list(output_root.glob("run-*"))
    saved = json.loads((run_dir / "run.json").read_text())
    assert saved["outcome"] == "interrupted"
    assert saved["interrupted"] is True
    assert "Traceback" not in completed.stderr.decode()


def test_run_directories_are_unique_and_never_overwritten(
    source_repo: Path, tmp_path: Path
) -> None:
    output_root = tmp_path / "evidence"
    first = _run(source_repo, output_root, "print('first')")
    first_stdout = Path(_read_record(first)["output"]["stdout"])
    first_stdout.write_bytes(b"preserve-me")
    second = _run(source_repo, output_root, "print('second')")
    assert first["run_dir"] != second["run_dir"]
    assert first_stdout.read_bytes() == b"preserve-me"
    assert b"second" in Path(_read_record(second)["output"]["stdout"]).read_bytes()
    assert len(list(output_root.glob("run-*"))) == 2


def test_source_drift_marks_run_stale_without_changing_process_outcome(
    source_repo: Path, tmp_path: Path
) -> None:
    code = "from pathlib import Path; Path('tracked.txt').write_text('after\\n'); print('edited')"
    record = _run(source_repo, tmp_path / "evidence", code)
    saved = _read_record(record)
    assert record["outcome"] == "completed"
    assert saved["process"]["exit_code"] == 0
    assert saved["candidate"]["status"] == "stale"
    assert saved["candidate"]["source_drift"] is True
    assert (
        json.loads(Path(saved["candidate"]["before"]).read_text())["candidate_id"]
        != json.loads(Path(saved["candidate"]["after"]).read_text())["candidate_id"]
    )


def test_declared_secret_is_redacted_from_outputs_and_metadata(
    source_repo: Path, tmp_path: Path
) -> None:
    secret = "known-recorder-secret"
    code = "import os, sys; print(os.environ['RECORDER_SECRET']); print(os.environ['RECORDER_SECRET'], file=sys.stderr)"
    record = run_recorder.record_run(
        [sys.executable, "-c", code],
        cwd=source_repo,
        candidate_root=source_repo,
        output_root=tmp_path / "evidence",
        timeout_s=5,
        env={"RECORDER_SECRET": secret},
        env_allowlist=("RECORDER_SECRET",),
        secret_values=(secret,),
    )
    saved = _read_record(record)
    run_text = Path(record["run_record"]).read_text()
    stdout = Path(saved["output"]["stdout"]).read_text()
    stderr = Path(saved["output"]["stderr"]).read_text()
    assert secret not in run_text
    assert secret not in stdout and secret not in stderr
    assert stdout.strip() == run_recorder.REDACTION_MARKER
    assert stderr.strip() == run_recorder.REDACTION_MARKER
    assert saved["redaction"]["known_values_supplied"] == 1


def test_declared_secret_is_rejected_from_argv_before_spawn(
    source_repo: Path, tmp_path: Path
) -> None:
    with pytest.raises(run_recorder.RunRecorderError, match="not accepted in argv"):
        run_recorder.record_run(
            [sys.executable, "-c", "print('known-recorder-secret')"],
            cwd=source_repo,
            output_root=tmp_path / "evidence",
            timeout_s=1,
            secret_values=("known-recorder-secret",),
        )
    assert not (tmp_path / "evidence").exists()


def test_unknown_candidate_identity_is_not_reported_as_stable(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        run_recorder.candidate,
        "build_candidate",
        lambda *_args, **_kwargs: {
            "candidate_id": "sha256:unchanged-but-unavailable",
            "git": {"commit": None, "error": "git unavailable"},
            "git_status": {"clean": None},
            "issues": ["git metadata error"],
        },
    )
    record = _run(source_repo, tmp_path / "evidence", "print('ran')")
    saved = _read_record(record)
    assert saved["candidate"]["status"] == "unknown"
    assert saved["candidate"]["source_drift"] is None


def test_unproven_gitlink_identity_is_not_reported_as_stable(
    source_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        run_recorder.candidate,
        "build_candidate",
        lambda *_args, **_kwargs: {
            "candidate_id": "sha256:gitlink-not-proven",
            "git": {"commit": "abc123", "error": None},
            "git_status": {"clean": True},
            "manifest": [
                {"path": "vendor/component", "issue": "gitlink component source not read"}
            ],
            "issues": [],
        },
    )
    record = _run(source_repo, tmp_path / "evidence", "print('ran')")
    saved = _read_record(record)
    assert saved["candidate"]["status"] == "unknown"
    assert saved["candidate"]["source_drift"] is None


def test_rejects_shell_command_strings_and_internal_output_root(
    source_repo: Path, tmp_path: Path
) -> None:
    with pytest.raises(run_recorder.RunRecorderError, match="explicit sequence"):
        run_recorder.record_run(
            "echo unsafe", cwd=source_repo, output_root=tmp_path / "evidence", timeout_s=1
        )
    with pytest.raises(run_recorder.RunRecorderError, match="outside cwd"):
        run_recorder.record_run(
            [sys.executable, "-c", "pass"],
            cwd=source_repo,
            output_root=source_repo / "runs",
            timeout_s=1,
        )
    for timeout in (0, float("nan"), float("inf")):
        with pytest.raises(run_recorder.RunRecorderError):
            run_recorder.record_run(
                [sys.executable, "-c", "pass"],
                cwd=source_repo,
                output_root=tmp_path / f"evidence-{timeout}",
                timeout_s=timeout,
            )
