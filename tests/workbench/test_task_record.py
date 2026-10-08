"""Task record store tests, translated from packages/pi-workbench/tests/task-record.test.ts."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.task_record import TaskRecordStore, owner_alive
from tests.hang_guard import HANG_GUARD_SECS

pytestmark = pytest.mark.parity

PROFILE: dict[str, str] = {
    "name": "fixture",
    "provider": "fixture",
    "modelId": "model",
    "api": "openai-completions",
    "endpoint": "http://127.0.0.1",
    "resourceGroup": "fixture",
    "credentialClass": "keyless",
}


def make(store: TaskRecordStore, root: Path, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "role": "scout",
        "profile": PROFILE,
        "workspace": str(root),
        "base_revision": "base",
        "parent_session_id": "parent",
        "task": "inspect",
    }
    fields.update(overrides)
    return store.create_sync(**fields)


def rewrite(path: Path, raw: dict[str, Any]) -> None:
    path.write_text(json.dumps(raw))


def test_task_records_preserve_versioned_lineage_and_full_result_artifacts(tmp_path: Path) -> None:
    """Source: task-record.test.ts 'task records preserve versioned lineage and full result artifacts'."""
    store = TaskRecordStore(str(tmp_path))
    first = make(store, tmp_path, base_revision="base-one", task="inspect alpha")
    second = make(store, tmp_path, base_revision="base-two", task="inspect beta")
    store.update(first["taskId"], {"executionState": "running", "childId": "child-one"})
    store.update(second["taskId"], {"executionState": "running", "childId": "child-two"})
    report = "SCOUT_ALPHA\n" + "full child output " * 1000
    store.finish(first["taskId"], execution_state="succeeded", result=report)
    store.finish(
        second["taskId"], execution_state="succeeded", result="SCOUT_BETA\nseparate result"
    )
    assert len(store.list()) == 2
    saved = store.get(first["taskId"])
    assert saved is not None
    assert saved["schemaVersion"] == 1
    assert saved["runId"] == first["runId"]
    assert saved["attemptId"] == first["attemptId"]
    assert saved["parentSessionId"] == "parent"
    assert saved["baseRevision"] == "base-one"
    assert saved["childId"] == "child-one"
    assert saved["executionState"] == "succeeded"
    assert saved["envelope"]["validationProfile"] == "read-only-observation"
    assert saved["envelope"]["workspacePolicy"] == "parent-workspace-read-only"
    assert saved["envelope"]["contextPolicy"] == "isolated-without-parent-transcript"
    full = Path(saved["artifacts"]["result"]["path"]).read_text()
    assert full == report
    assert len(saved["summary"]) < len(full)
    assert "truncated; complete result is in the artifact" in saved["summary"]


def test_a_terminal_task_record_refuses_late_artifacts_before_writing_a_file(
    tmp_path: Path,
) -> None:
    store = TaskRecordStore(str(tmp_path))
    record = make(store, tmp_path)
    store.finish(record["taskId"], execution_state="succeeded", result="done")
    before = sorted(path.name for path in store.directory.iterdir())

    with pytest.raises(ValueError, match="succeeded"):
        store.artifact(record["taskId"], "summary", "late")

    assert sorted(path.name for path in store.directory.iterdir()) == before
    assert not any(
        "late" in path.read_text() for path in store.directory.glob(f"{record['taskId']}.*.txt")
    )


def test_normalized_results_retain_changed_files_validation_blockers_risks_and_usage(
    tmp_path: Path,
) -> None:
    """Source: 'normalized results retain changed files, validation, blockers, risks, and usage provenance'."""
    store = TaskRecordStore(str(tmp_path))
    record = make(store, tmp_path, role="worker", base_revision="revision-one", task="change alpha")
    validation = tmp_path / "validation.json"
    patch = tmp_path / "handoff.patch"
    validation.write_text("validation evidence\n")
    patch.write_text("patch evidence\n")
    patch_sha = hashlib.sha256(b"patch evidence\n").hexdigest()
    finished = store.finish(
        record["taskId"],
        execution_state="succeeded",
        acceptance_state="checks-passed",
        changed_files=["src/alpha.ts", "tests/alpha.test.ts"],
        verification=[{"checkId": "unit", "exitCode": 0, "evidencePath": str(validation)}],
        blockers=[],
        risks=["fixture-only validation"],
        usage={
            "source": "provider-reported",
            "inputTokens": 12,
            "outputTokens": 4,
            "totalTokens": 16,
        },
        handoff={"handoffId": "task-handoff-1", "patchPath": str(patch), "patchSha256": patch_sha},
        result="completed",
    )
    evidence = {
        "taskId": record["taskId"],
        "runId": record["runId"],
        "attemptId": record["attemptId"],
        "workspace": str(tmp_path),
        "revision": "revision-one",
    }
    assert finished["changedFiles"] == ["src/alpha.ts", "tests/alpha.test.ts"]
    assert finished["blockers"] == []
    assert finished["risks"] == ["fixture-only validation"]
    assert finished["usage"]["source"] == "provider-reported"
    assert finished["usage"]["totalTokens"] == 16
    assert finished["usage"]["provenance"] == evidence
    assert finished["verification"][0]["evidence"] == evidence
    assert finished["handoff"]["evidence"] == evidence
    reloaded = store.get(record["taskId"])
    assert reloaded is not None
    assert reloaded["usage"]["provenance"] == evidence
    assert reloaded["verification"][0]["evidence"] == evidence


def test_task_result_evidence_must_be_owned_existing_regular_files(tmp_path: Path) -> None:
    """Source: 'task result evidence must be owned existing regular files'."""
    root = tmp_path / "root"
    root.mkdir()
    store = TaskRecordStore(str(root))
    missing = make(store, root, base_revision="revision-one", task="missing evidence")
    with pytest.raises(ValueError, match="evidence path is unavailable"):
        store.finish(
            missing["taskId"],
            execution_state="succeeded",
            verification=[
                {"checkId": "missing", "exitCode": 0, "evidencePath": str(root / "missing.json")}
            ],
        )
    outside_path = tmp_path / "unowned-evidence.json"
    outside_path.write_text("outside\n")
    outside = make(store, root, base_revision="revision-one", task="outside evidence")
    with pytest.raises(ValueError, match="escapes task workspace"):
        store.finish(
            outside["taskId"],
            execution_state="succeeded",
            verification=[{"checkId": "outside", "exitCode": 0, "evidencePath": str(outside_path)}],
        )


def test_terminal_task_records_ignore_late_updates_and_finishes_in_serialized_order(
    tmp_path: Path,
) -> None:
    """Source: 'terminal task records ignore late updates and finishes in serialized order'."""
    store = TaskRecordStore(str(tmp_path))
    record = make(store, tmp_path, role="worker", base_revision="revision-one", task="deadline")
    interrupted = store.finish(
        record["taskId"],
        execution_state="interrupted",
        acceptance_state="rejected",
        error="deadline exceeded",
    )
    results: dict[str, dict[str, Any]] = {}

    def late_update() -> None:
        results["update"] = store.update(record["taskId"], {"childId": "late-child"})

    def late_finish() -> None:
        results["finish"] = store.finish(
            record["taskId"], execution_state="succeeded", result="late result"
        )

    threads = [threading.Thread(target=late_update), threading.Thread(target=late_finish)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert interrupted["executionState"] == "interrupted"
    assert interrupted["error"] == "deadline exceeded"
    assert results["update"]["executionState"] == "interrupted"
    assert results["finish"]["executionState"] == "interrupted"
    saved = store.get(record["taskId"])
    assert saved is not None
    assert saved["executionState"] == "interrupted"
    assert saved["acceptanceState"] == "rejected"
    assert saved["error"] == "deadline exceeded"
    assert saved["childId"] == "late-child"
    assert saved["artifacts"] == {}


def test_task_record_reads_reject_malformed_version_identity_and_state_contracts(
    tmp_path: Path,
) -> None:
    """Source: 'task record reads reject malformed version, identity, and state contracts'."""
    store = TaskRecordStore(str(tmp_path))
    (tmp_path / "task-records").mkdir()
    (tmp_path / "task-records" / "task-invalid.json").write_text(
        json.dumps({"schemaVersion": 999, "taskId": "different-task", "executionState": "accepted"})
    )
    with pytest.raises(ValueError, match="schemaVersion"):
        store.get("task-invalid")
    with pytest.raises(ValueError, match="schemaVersion"):
        store.list()


def test_task_record_reads_reject_malformed_consumed_fields(tmp_path: Path) -> None:
    """Source: 'task record reads reject malformed consumed fields instead of sorting or casting them'."""
    store = TaskRecordStore(str(tmp_path))
    record = make(store, tmp_path)
    path = tmp_path / "task-records" / f"{record['taskId']}.json"
    raw = json.loads(path.read_text())

    def expect_get(pattern: str, method: str = "get") -> None:
        rewrite(path, raw)
        with pytest.raises(ValueError, match=pattern):
            if method == "get":
                store.get(record["taskId"])
            else:
                store.list()

    for bad in ("not-a-timestamp", "2026-02-30T00:00:00.000Z", "2026-01-01T24:00:00.000Z"):
        raw["updatedAt"] = bad
        expect_get("updatedAt")
    raw["updatedAt"] = record["updatedAt"]
    for bad in ("not-a-timestamp", "1"):
        raw["completedAt"] = bad
        expect_get("completedAt")
    del raw["completedAt"]
    raw["artifacts"] = {
        "result": {
            "path": "x",
            "sha256": "bad",
            "bytes": -1,
            "complete": False,
            "view": 4,
            "truncated": "no",
        }
    }
    expect_get(r"artifacts\.result", "list")
    raw["artifacts"] = {}
    raw["envelope"]["task"] = "forged task"
    expect_get("envelope task")
    raw["envelope"]["task"] = raw["task"]
    raw["envelope"]["profile"] = {**PROFILE, "provider": "forged"}
    expect_get("envelope profile")
    raw["envelope"]["profile"] = PROFILE
    raw["instructionProvenance"] = {
        "source": "resource-loader",
        "complete": True,
        "files": [{"path": "AGENTS.md", "sha256": "bad", "bytes": 1, "estimated": False}],
    }
    expect_get("instructionProvenance")


def test_task_record_artifacts_must_stay_under_their_task_owned_directory(tmp_path: Path) -> None:
    """Source: 'task record artifacts must stay under their task-owned record directory'."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    store = TaskRecordStore(str(root))
    record = make(store, root)
    path = root / "task-records" / f"{record['taskId']}.json"
    raw = json.loads(path.read_text())
    artifact_path = outside / f"{record['taskId']}.result-0123456789abcdef.txt"
    artifact_path.write_text("outside\n")
    raw["artifacts"] = {
        "result": {
            "path": str(artifact_path),
            "sha256": "0" * 64,
            "bytes": 8,
            "complete": True,
            "view": "outside",
            "truncated": False,
        }
    }
    rewrite(path, raw)
    with pytest.raises(ValueError, match=r"artifacts\.result\.path.*(escapes|owned)"):
        store.get(record["taskId"])


def test_task_record_artifacts_reject_symlink_escapes(tmp_path: Path) -> None:
    """Source: 'task record artifacts reject symlink escapes'."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    store = TaskRecordStore(str(root))
    record = make(store, root)
    path = root / "task-records" / f"{record['taskId']}.json"
    raw = json.loads(path.read_text())
    target = outside / "outside.txt"
    target.write_text("outside\n")
    artifact_path = root / "task-records" / f"{record['taskId']}.result-0123456789abcdef.txt"
    artifact_path.symlink_to(target)
    raw["artifacts"] = {
        "result": {
            "path": str(artifact_path),
            "sha256": "0" * 64,
            "bytes": 8,
            "complete": True,
            "view": "outside",
            "truncated": False,
        }
    }
    rewrite(path, raw)
    with pytest.raises(ValueError, match=r"artifacts\.result\.path.*(symlink|regular)"):
        store.list()


def test_task_record_artifacts_reject_content_tampering_and_truncation_on_reload(
    tmp_path: Path,
) -> None:
    """Source: 'task record artifacts reject content tampering and truncation on reload'."""
    store = TaskRecordStore(str(tmp_path))
    record = make(store, tmp_path, task="integrity")
    finished = store.finish(
        record["taskId"], execution_state="succeeded", result="durable result content"
    )
    result_path = Path(finished["artifacts"]["result"]["path"])
    summary_path = Path(finished["artifacts"]["summary"]["path"])
    content = result_path.read_bytes()
    result_path.write_bytes(b"x" * len(content))
    with pytest.raises(ValueError, match=r"artifacts\.result\.sha256.*hash"):
        store.get(record["taskId"])
    result_path.write_bytes(content)
    summary = summary_path.read_bytes()
    summary_path.write_bytes(summary[:-1])
    with pytest.raises(ValueError, match=r"artifacts\.summary\.bytes.*length"):
        store.list()


def test_task_record_updates_reject_envelope_identity_drift(tmp_path: Path) -> None:
    """Source: 'task record updates reject envelope identity drift without replacing the valid record'."""
    store = TaskRecordStore(str(tmp_path))
    record = make(store, tmp_path, role="worker", task="original")
    taskid = record["taskId"]
    with pytest.raises(ValueError, match="envelope task"):
        store.update(taskid, {"task": "updated"})
    with pytest.raises(ValueError, match="envelope"):
        store.update(taskid, {"role": "reviewer"})
    with pytest.raises(ValueError, match="envelope profile"):
        store.update(taskid, {"profile": {**PROFILE, "provider": "other"}})
    with pytest.raises(ValueError, match="envelope scope"):
        store.update(taskid, {"acceptance": "changed"})
    with pytest.raises(ValueError, match="workspace is immutable"):
        store.update(taskid, {"workspace": "/elsewhere/other"})
    saved = store.get(taskid)
    assert saved is not None
    assert (saved["task"], saved["role"], saved["profile"]) == ("original", "worker", PROFILE)


def test_stale_recovery_preserves_ownership_when_process_start_metadata_is_unavailable(
    tmp_path: Path,
) -> None:
    """Source: 'stale recovery preserves ownership when process start metadata is unavailable'."""
    store = TaskRecordStore(str(tmp_path))
    record = make(store, tmp_path, role="worker", task="keep ownership")
    path = tmp_path / "task-records" / f"{record['taskId']}.json"
    raw = json.loads(path.read_text())
    del raw["ownerStartTime"]
    rewrite(path, raw)
    recovered = store.recover_stale(record["taskId"])
    assert recovered is not None
    assert recovered["executionState"] == "running"
    assert recovered["acceptanceState"] == "unchecked"


def test_owner_identity_remains_uncertain_when_a_live_process_start_read_fails() -> None:
    """Source: 'owner identity remains uncertain when a live process start read fails'."""
    assert owner_alive(os.getpid(), "known-start", lambda: None) is True
    assert owner_alive(os.getpid(), "known-start", lambda: "different-start") is False


def test_task_record_creation_validates_the_generated_record_before_writing_it(
    tmp_path: Path,
) -> None:
    """Source: 'task record creation validates the generated record before writing it'."""
    store = TaskRecordStore(str(tmp_path))
    with pytest.raises(ValueError, match="task"):
        make(store, tmp_path, task="")
    with pytest.raises(ValueError, match="task"):
        store.create(
            role="worker",
            profile=PROFILE,
            workspace=str(tmp_path),
            base_revision="base",
            parent_session_id="parent",
            task="",
        )
    assert store.list() == []


CRASH_SCRIPT = textwrap.dedent(
    """
    import json, sys, time
    from pitwall.workbench.task_record import TaskRecordStore

    root, marker = sys.argv[1], sys.argv[2]
    store = TaskRecordStore(root)
    profile = {"name": "crash-fixture", "provider": "fixture", "modelId": "model", "api": "openai-completions",
               "endpoint": "http://127.0.0.1", "resourceGroup": "fixture", "credentialClass": "keyless"}
    published = []
    for role in ("scout", "worker", "reviewer"):
        record = store.create_sync(role=role, profile=profile, workspace=root, base_revision="crash-base",
                                   parent_session_id="parent-crash", task=role + " task")
        store.update(record["taskId"], {"childId": "child-" + role, "executionState": "running"})
        published.append({k: record[k] for k in ("taskId", "runId", "attemptId", "role")})
    with open(marker, "w") as handle:
        json.dump(published, handle)
    time.sleep(60)
    """
)


def test_durable_task_records_for_every_role_remain_inspectable_after_the_owner_is_killed(
    tmp_path: Path,
) -> None:
    """Source: 'durable task records for every role remain inspectable after the owning process is killed'."""
    root = tmp_path / "root"
    root.mkdir()
    marker = tmp_path / "ready.json"
    child = subprocess.Popen(
        [sys.executable, "-c", CRASH_SCRIPT, str(root), str(marker)], stderr=subprocess.PIPE
    )
    try:
        deadline = time.monotonic() + HANG_GUARD_SECS
        while not marker.exists():
            assert time.monotonic() < deadline, "crash fixture did not publish durable records"
            assert child.poll() is None, child.stderr.read() if child.stderr else b""
            time.sleep(0.02)
    finally:
        child.send_signal(signal.SIGKILL)
        child.wait()
        if child.stderr:
            child.stderr.close()
    published = json.loads(marker.read_text())
    store = TaskRecordStore(str(root))
    assert len(published) == 3
    for identity in published:
        saved = store.get(identity["taskId"])
        assert saved is not None
        assert saved["runId"] == identity["runId"]
        assert saved["attemptId"] == identity["attemptId"]
        assert saved["role"] == identity["role"]
        assert saved["parentSessionId"] == "parent-crash"
        assert saved["childId"] == f"child-{identity['role']}"
        assert saved["workspace"] == str(root)
        assert saved["baseRevision"] == "crash-base"
        assert saved["executionState"] == "running"
        assert saved["acceptanceState"] == "unchecked"
    assert len(store.list()) == 3
