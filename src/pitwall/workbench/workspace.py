"""Handoff workspaces: an isolated git worktree for a writer, a captured patch, and gated integration.

A writer never touches the base checkout. Its work is captured as an uncommitted patch with a
recorded hash; integration applies that exact patch to the exact clean base revision, runs approved
checks with a scrubbed environment, and never commits. Manifests and results are plain JSON objects
(camelCase keys) so the on-disk records match the TypeScript workbench.
"""

from __future__ import annotations

import codecs
import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from pitwall.workbench.restricted import restricted_tool_environment

HANDOFF_DIR = ".pi-workbench-handoffs"
MANIFEST_NAME = ".pi-workbench-handoff.json"
_PATCH_EXCLUDES = (".", f":(exclude){MANIFEST_NAME}", ":(exclude)handoff.patch")
OUTPUT_HEAD_LIMIT = 2_000
OUTPUT_TAIL_LIMIT = 2_000
DEFAULT_CHECK_TIMEOUT_MS = 120_000
_TASK_ID = re.compile(r"task-[0-9]+-[a-f0-9]{12}")

Manifest = dict[str, Any]


class HandoffError(RuntimeError):
    """A handoff precondition failed or the recorded evidence does not match."""


@dataclass(frozen=True)
class ApprovedCheck:
    """One approved command, run in the handoff workspace or the integration target."""

    command: str
    args: tuple[str, ...] = ()
    timeout_ms: int = DEFAULT_CHECK_TIMEOUT_MS


@dataclass(frozen=True)
class CapturedPatch:
    patch: str
    manifest: Manifest
    patch_path: str
    changed_files: list[str]


def _run_git(cwd: str | Path, args: Sequence[str], env: Mapping[str, str] | None = None) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env={**os.environ, **env} if env else None,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise HandoffError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def _git(cwd: str | Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    return _run_git(cwd, args, env).decode("utf-8", "replace").strip()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_new(path: Path, data: bytes | str) -> None:
    """Create a private file, failing if it already exists."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)


def _write_private(path: Path, data: bytes | str) -> None:
    """Create or replace a private file."""
    payload = data.encode("utf-8") if isinstance(data, str) else data
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(payload)


def _json_text(value: object) -> str:
    return f"{json.dumps(value, indent=2, ensure_ascii=False)}\n"


def _handoff_root(manifest: Mapping[str, Any]) -> Path:
    return Path(manifest["workspace"]).parent


def _write_manifest(manifest: Mapping[str, Any]) -> None:
    _write_private(_handoff_root(manifest) / MANIFEST_NAME, _json_text(manifest))


def _read_owner(lock: Path) -> dict[str, Any]:
    owner = json.loads((lock / "owner").read_text(encoding="utf-8"))
    if not isinstance(owner, dict):
        raise ValueError("owner is not an object")
    return owner


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _release_writer_lock(base: str, task_id: str) -> None:
    lock = Path(base) / HANDOFF_DIR / ".writer-lock"
    try:
        owner = _read_owner(lock)
    except (OSError, ValueError) as error:
        raise HandoffError(
            f"writer lock owner is unreadable; refusing to remove lock: {error}"
        ) from None
    if owner.get("taskId") != task_id:
        raise HandoffError("writer lock owner does not match handoff; refusing to remove lock")
    _remove_tree(lock)


def _writer_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    if sys.platform != "linux":
        return True
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except FileNotFoundError:
        return False
    return re.search(r"^State:\s+Z", status, re.MULTILINE) is None


def _status_dirty(base: str) -> str:
    return _git(
        base,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
        f":(exclude){HANDOFF_DIR}",
    )


def create_handoff_workspace(cwd: str) -> Manifest:
    """Create an isolated detached worktree of a clean checkout for one writer."""
    base = os.path.abspath(cwd)
    if _status_dirty(base):
        raise HandoffError(
            "writer requires a clean base workspace; preserve or integrate existing changes first"
        )
    revision = _git(base, "rev-parse", "HEAD")
    handoff_root = Path(base) / HANDOFF_DIR
    handoff_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = handoff_root / ".writer-lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise HandoffError(
            "active writer handoff already exists; recover or review it before starting another"
        ) from None
    suffix = _sha256(f"{base}\0{revision}\0{secrets.token_hex(8)}".encode())[:12]
    task_id = f"task-{int(time.time() * 1000)}-{suffix}"
    root = Path(tempfile.mkdtemp(prefix=f"{task_id}-", dir=handoff_root))
    path = root / "worktree"
    try:
        _run_git(base, ["worktree", "add", "--detach", str(path), revision])
    except HandoffError:
        _remove_tree(root)
        _remove_tree(lock)
        raise
    _write_new(lock / "owner", json.dumps({"pid": os.getpid(), "taskId": task_id}))
    manifest: Manifest = {
        "schemaVersion": 1,
        "taskId": task_id,
        "base": base,
        "baseRef": "HEAD",
        "revision": revision,
        "workspace": str(path),
        "status": "active",
        "validation": "not-run",
        "review": "pending",
        "acceptance": "pending",
    }
    _write_manifest(manifest)
    return manifest


def _current_patch(manifest: Mapping[str, Any]) -> bytes:
    _git(manifest["workspace"], "add", "--all", "--", *_PATCH_EXCLUDES)
    return _run_git(manifest["workspace"], ["diff", "--cached", "--binary", manifest["revision"]])


def capture_patch(workspace: Mapping[str, Any], status: str = "completed") -> CapturedPatch:
    """Stage the writer's work and record it as a hashed, uncommitted patch."""
    worktree = workspace["workspace"]
    final_revision = _git(worktree, "rev-parse", "HEAD")
    unexpected_commit = final_revision != workspace["revision"]
    _git(worktree, "add", "--all", "--", *_PATCH_EXCLUDES)
    patch = _run_git(worktree, ["diff", "--cached", "--binary", workspace["revision"]])
    names = _run_git(worktree, ["diff", "--cached", "--name-only", "-z", workspace["revision"]])
    changed_files = [name for name in names.decode("utf-8", "replace").split("\0") if name]
    patch_path = _handoff_root(workspace) / "handoff.patch"
    _write_private(patch_path, patch)
    tree_hash = _git(worktree, "write-tree")
    manifest: Manifest = {
        "schemaVersion": 1,
        "taskId": workspace["taskId"],
        "base": workspace["base"],
        "baseRef": "HEAD",
        "revision": workspace["revision"],
        "finalRevision": final_revision,
        "unexpectedCommit": unexpected_commit,
        "workspace": worktree,
        "status": "failed" if unexpected_commit else status,
        "patchPath": str(patch_path),
        "patchSha256": _sha256(patch),
        "treeHash": tree_hash,
        "validation": "not-run",
        "review": "pending",
        "acceptance": "rejected" if unexpected_commit else "pending",
    }
    _write_manifest(manifest)
    if status != "active" or unexpected_commit:
        _release_writer_lock(workspace["base"], workspace["taskId"])
    if unexpected_commit:
        raise HandoffError(
            f"unexpected writer commit detected: base {workspace['revision']}, "
            f"final {final_revision}; acceptance rejected"
        )
    return CapturedPatch(patch.decode("utf-8", "replace"), manifest, str(patch_path), changed_files)


def load_handoff(base_cwd: str, task_id: str) -> Manifest:
    """Load a handoff and verify its paths, patch hash, and workspace against the captured patch."""
    if not _TASK_ID.fullmatch(task_id):
        raise HandoffError("invalid handoff task id")
    base = os.path.abspath(base_cwd)
    directory = next(
        (
            entry
            for entry in sorted((Path(base) / HANDOFF_DIR).iterdir())
            if entry.is_dir() and entry.name.startswith(f"{task_id}-")
        ),
        None,
    )
    if directory is None:
        raise HandoffError("handoff task not found")
    manifest: Manifest = json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("taskId") != task_id
        or manifest.get("base") != base
        or manifest.get("baseRef") != "HEAD"
    ):
        raise HandoffError("handoff manifest does not match requested base")
    root = Path(os.path.abspath(directory))
    if os.path.abspath(manifest["workspace"]) != str(root / "worktree") or os.path.abspath(
        manifest.get("patchPath") or str(root / "handoff.patch")
    ) != str(root / "handoff.patch"):
        raise HandoffError("handoff manifest path escapes its owned directory")
    if os.path.islink(manifest["workspace"]):
        raise HandoffError("handoff workspace must not be a symlink")
    if manifest.get("patchPath") and manifest.get("patchSha256"):
        if _sha256(Path(manifest["patchPath"]).read_bytes()) != manifest["patchSha256"]:
            raise HandoffError("handoff patch hash mismatch")
        if _sha256(_current_patch(manifest)) != manifest["patchSha256"]:
            raise HandoffError("handoff workspace changed since patch capture")
    return manifest


def recover_handoff(base_cwd: str, task_id: str) -> Manifest:
    """Mark an active handoff failed when its writer process is gone; the lock stays for inspection."""
    manifest = load_handoff(base_cwd, task_id)
    if manifest["status"] != "active":
        return manifest
    lock = Path(os.path.abspath(base_cwd)) / HANDOFF_DIR / ".writer-lock"
    try:
        owner = _read_owner(lock)
    except OSError, ValueError:
        raise HandoffError("writer lock owner is unreadable; manual recovery required") from None
    pid = owner.get("pid")
    if (
        owner.get("taskId") != task_id
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
    ):
        raise HandoffError("writer lock owner does not match handoff")
    if _writer_process_alive(pid):
        raise HandoffError("writer process is still alive")
    recovered = {**manifest, "status": "failed"}
    _write_manifest(recovered)
    # A dead writer PID does not prove detached tool descendants are gone. Keep the lock for
    # manual inspection so recovery cannot create a duplicate writer.
    return recovered


def update_validation(manifest: Mapping[str, Any], validation: str) -> Manifest:
    updated = {**manifest, "validation": validation}
    _write_manifest(updated)
    return updated


def _bounded_view(
    value: str, head: int = OUTPUT_HEAD_LIMIT, tail: int = OUTPUT_TAIL_LIMIT
) -> tuple[str, bool]:
    if len(value) <= head + tail:
        return value, False
    return (
        f"{value[:head]}\n…[truncated; complete output is in the artifact]…\n{value[-tail:]}",
        True,
    )


def _ensure_artifact_directory(manifest: Mapping[str, Any]) -> Path:
    directory = _handoff_root(manifest) / "artifacts"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if directory.is_symlink() or not directory.is_dir() or (info.st_mode & 0o077) != 0:
        raise HandoffError("handoff artifact directory must be a private regular directory")
    return directory


def _artifact_record(path: Path, value: bytes, text: str) -> dict[str, Any]:
    view, truncated = _bounded_view(text)
    return {
        "path": str(path),
        "sha256": _sha256(value),
        "bytes": len(value),
        "view": view,
        "truncated": truncated,
        "complete": True,
    }


def record_review(
    manifest: Mapping[str, Any], report: str, status: str = "completed"
) -> dict[str, Any]:
    """Store the complete review report as an artifact and a bounded view in review.json."""
    if not manifest.get("patchPath") or not manifest.get("patchSha256"):
        raise HandoffError("review requires a captured patch")
    if _sha256(Path(manifest["patchPath"]).read_bytes()) != manifest["patchSha256"]:
        raise HandoffError("handoff patch hash mismatch")
    artifacts = _ensure_artifact_directory(manifest)
    data = report.encode("utf-8")
    digest = _sha256(data)
    report_path = artifacts / f"review-report-{digest[:16]}.txt"
    try:
        _write_new(report_path, data)
    except FileExistsError:
        if _sha256(report_path.read_bytes()) != digest:
            raise HandoffError("review report artifact collision") from None
    limit = 4_000
    view, truncated = _bounded_view(report, limit, limit)
    artifact = {
        "path": str(report_path),
        "sha256": digest,
        "bytes": len(data),
        "view": view,
        "truncated": truncated,
        "complete": True,
    }
    record = {
        "schemaVersion": 1,
        "taskId": manifest["taskId"],
        "revision": manifest["revision"],
        "patchSha256": manifest["patchSha256"],
        "status": status,
        "report": view,
        "reportArtifact": artifact,
        "recordedAt": _timestamp(),
    }
    _write_private(_handoff_root(manifest) / "review.json", _json_text(record))
    return record


def _timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class _Capture:
    """Streams one output pipe to a private artifact while keeping a bounded head and tail view."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: IO[bytes] | None = open(  # noqa: SIM115  # reason: the handle spans reader thread lifetime and is closed in finish()
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"
        )
        self.digest = hashlib.sha256()
        self.size = 0
        self.chars = 0
        self.prefix = ""
        self.tail = ""
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.error: str | None = None
        self.on_error: Any = None

    def append(self, part: bytes) -> None:
        self.digest.update(part)
        self.size += len(part)
        self._text(self.decoder.decode(part))
        if self.handle is not None and self.error is None:
            try:
                self.handle.write(part)
            except OSError as error:
                self.error = str(error)
                if self.on_error:
                    self.on_error()

    def _text(self, text: str) -> None:
        self.chars += len(text)
        room = OUTPUT_HEAD_LIMIT + OUTPUT_TAIL_LIMIT - len(self.prefix)
        if room > 0:
            self.prefix += text[:room]
        self.tail = (self.tail + text)[-OUTPUT_TAIL_LIMIT:]

    def finish(self) -> dict[str, Any]:
        trailing = self.decoder.decode(b"", final=True)
        if trailing:
            self._text(trailing)
        if self.handle is not None:
            try:
                self.handle.close()
            except OSError as error:
                self.error = self.error or str(error)
            self.handle = None
        limit = OUTPUT_HEAD_LIMIT + OUTPUT_TAIL_LIMIT
        truncated = self.chars > limit
        view = (
            self.prefix
            if not truncated
            else f"{self.prefix[:OUTPUT_HEAD_LIMIT]}\n…[truncated; complete output is in the artifact]…\n{self.tail}"
        )
        record: dict[str, Any] = {
            "path": str(self.path),
            "sha256": self.digest.hexdigest(),
            "bytes": self.size,
            "view": view,
            "truncated": truncated,
            "complete": self.error is None,
        }
        if self.error:
            record["error"] = self.error
        return record


def _pump(pipe: IO[bytes], capture: _Capture) -> None:
    with pipe:
        while True:
            part = pipe.read1(65536)  # type: ignore[attr-defined]  # reason: BufferedReader.read1 is absent from the IO[bytes] stub
            if not part:
                return
            capture.append(part)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError, PermissionError:
        with contextlib.suppress(ProcessLookupError):
            process.kill()


def _capture_for(directory: Path, index: int, check: ApprovedCheck, stream: str) -> _Capture:
    identity = _sha256(
        json.dumps(
            {"index": index, "command": check.command, "args": list(check.args), "stream": stream},
            separators=(",", ":"),
        ).encode()
    )[:16]
    return _Capture(directory / f"check-{index + 1}-{identity}.{stream}.log")


def _check_fields(check: ApprovedCheck) -> dict[str, Any]:
    return {"command": check.command, "args": list(check.args), "timeoutMs": check.timeout_ms}


def _run_approved_check(
    cwd: str, check: ApprovedCheck, index: int, directory: Path, abort: threading.Event | None
) -> dict[str, Any]:
    stdout = _capture_for(directory, index, check, "stdout")
    stderr = _capture_for(directory, index, check, "stderr")
    if abort is not None and abort.is_set():
        stderr.append(b"approved check aborted before start")
        out, err = stdout.finish(), stderr.finish()
        return {
            **_check_fields(check),
            "code": 130,
            "stdout": out["view"],
            "stderr": err["view"],
            "stdoutArtifact": out,
            "stderrArtifact": err,
        }
    reason: str | None = None
    try:
        process = subprocess.Popen(
            [check.command, *check.args],
            cwd=cwd,
            env=restricted_tool_environment(os.environ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        stderr.append(str(error).encode())
        out, err = stdout.finish(), stderr.finish()
        return {
            **_check_fields(check),
            "code": 1,
            "stdout": out["view"],
            "stderr": err["view"],
            "stdoutArtifact": out,
            "stderrArtifact": err,
        }
    assert (
        process.stdout is not None and process.stderr is not None
    )  # reason: both pipes were requested above
    stdout.on_error = stderr.on_error = lambda: _kill_group(process)
    readers = [
        threading.Thread(target=_pump, args=(process.stdout, stdout), daemon=True),
        threading.Thread(target=_pump, args=(process.stderr, stderr), daemon=True),
    ]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + check.timeout_ms / 1000
    while process.poll() is None:
        if abort is not None and abort.is_set():
            reason = "aborted"
            _kill_group(process)
        elif time.monotonic() >= deadline:
            reason = "timeout"
            _kill_group(process)
        else:
            time.sleep(0.01)
            continue
        process.wait()
    for reader in readers:
        reader.join()
    code = process.returncode
    out, err = stdout.finish(), stderr.finish()
    suffix = {"timeout": "approved check timed out", "aborted": "approved check aborted"}.get(
        reason or "", ""
    )
    captured_stderr = (
        f"{err['view']}{chr(10) if err['view'] else ''}{suffix}" if suffix else err["view"]
    )
    artifact_error = stdout.error or stderr.error
    if artifact_error:
        code = 1
    elif reason == "timeout":
        code = 124
    elif reason == "aborted":
        code = 130
    elif code < 0:
        code = 1
    result: dict[str, Any] = {
        **_check_fields(check),
        "code": code,
        "stdout": out["view"],
        "stderr": captured_stderr,
        "stdoutArtifact": out,
        "stderrArtifact": err,
    }
    if artifact_error:
        result["artifactError"] = artifact_error
    return result


def _empty_artifact(path: Path, text: str) -> dict[str, Any]:
    return _artifact_record(path, text.encode("utf-8"), text)


def _run_checks(
    workspace: Mapping[str, Any],
    checks: Sequence[ApprovedCheck],
    abort: threading.Event | None,
    cwd: str,
    artifact_root: Path,
    verify_patch: bool,
    validation_path: Path,
) -> dict[str, Any]:
    directory = artifact_root / f"validation-{uuid.uuid4()}"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    results: list[dict[str, Any]] = []
    for index, check in enumerate(checks):
        results.append(_run_approved_check(cwd, check, index, directory, abort))
        if abort is not None and abort.is_set():
            break
    if (
        verify_patch
        and workspace.get("patchSha256")
        and _sha256(_current_patch(workspace)) != workspace["patchSha256"]
    ):
        stdout_path = directory / f"check-integrity-{uuid.uuid4()}.stdout.log"
        stderr_path = directory / f"check-integrity-{uuid.uuid4()}.stderr.log"
        message = "workspace changed after approved checks; patch was not unchanged"
        _write_new(stdout_path, "")
        _write_new(stderr_path, message)
        results.append(
            {
                "command": "handoff-patch-integrity",
                "code": 1,
                "stdout": "",
                "stderr": message,
                "stdoutArtifact": _empty_artifact(stdout_path, ""),
                "stderrArtifact": _empty_artifact(stderr_path, message),
            }
        )
    _write_private(
        validation_path,
        _json_text(
            {
                "taskId": workspace["taskId"],
                "revision": workspace["revision"],
                "patchSha256": workspace.get("patchSha256"),
                "cwd": cwd,
                "artifactDirectory": str(directory),
                "results": results,
            }
        ),
    )
    return {
        "passed": bool(results) and all(result["code"] == 0 for result in results),
        "results": results,
        "artifactDirectory": str(directory),
        "validationPath": str(validation_path),
    }


def run_approved_checks(
    workspace: Mapping[str, Any],
    checks: Sequence[ApprovedCheck],
    abort: threading.Event | None = None,
) -> dict[str, Any]:
    """Run approved checks in the handoff worktree and confirm the patch is unchanged afterward.

    Each check runs in its own process group with the credential-scrubbed tool environment.
    """
    artifact_root = _ensure_artifact_directory(workspace)
    return _run_checks(
        workspace, checks, abort, workspace["workspace"], artifact_root, True,
        _handoff_root(workspace) / "validation.json",
    )  # fmt: skip


def run_approved_checks_at(
    cwd: str,
    workspace: Mapping[str, Any],
    checks: Sequence[ApprovedCheck],
    abort: threading.Event | None = None,
) -> dict[str, Any]:
    """Run post-apply checks in the target checkout, keeping artifacts under the reviewed handoff."""
    artifact_root = _ensure_artifact_directory(workspace)
    return _run_checks(
        workspace, checks, abort, os.path.abspath(cwd), artifact_root, False,
        artifact_root / f"post-apply-{uuid.uuid4()}.validation.json",
    )  # fmt: skip


def _acquire_integration_lock(base: str, task_id: str) -> Path:
    lock = Path(base) / HANDOFF_DIR / ".integration-lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise HandoffError(
            "another handoff integration is active; retry after it records its result"
        ) from None
    try:
        _write_new(lock / "owner", json.dumps({"pid": os.getpid(), "taskId": task_id}))
    except OSError:
        _remove_tree(lock)
        raise
    return lock


def _release_integration_lock(lock: Path, task_id: str) -> None:
    try:
        owner = _read_owner(lock)
    except (OSError, ValueError) as error:
        raise HandoffError(
            f"integration lock owner is unreadable; refusing to remove lock: {error}"
        ) from None
    if owner.get("taskId") != task_id:
        raise HandoffError("integration lock owner does not match handoff; refusing to remove lock")
    _remove_tree(lock)


def _review_matches(handoff: Mapping[str, Any]) -> bool:
    review = json.loads((_handoff_root(handoff) / "review.json").read_text(encoding="utf-8"))
    artifact = review.get("reportArtifact") or {}
    return bool(
        review.get("schemaVersion") == 1
        and review.get("taskId") == handoff["taskId"]
        and review.get("revision") == handoff["revision"]
        and review.get("patchSha256") == handoff["patchSha256"]
        and review.get("status") == "completed"
        and artifact.get("complete") is True
    )


def integrate_handoff(
    base_cwd: str,
    task_id: str,
    checks: Sequence[ApprovedCheck],
    abort: threading.Event | None = None,
) -> dict[str, Any]:
    """Apply a reviewed handoff to its exact clean base checkout, never committing or pushing.

    Integration is serialized by a target-local lease. A failed post-apply check leaves the
    uncommitted patch in place for inspection, with failure evidence retained beside the handoff.
    """
    base = os.path.abspath(base_cwd)
    lock = _acquire_integration_lock(base, task_id)
    try:
        handoff = load_handoff(base, task_id)
        artifact_root = _ensure_artifact_directory(handoff)
    except BaseException:
        _release_integration_lock(lock, task_id)
        raise
    directory = artifact_root / f"integration-{uuid.uuid4()}"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    record_path = directory / "integration.json"
    patch_path = handoff["patchPath"]
    snapshot_path = directory / "handoff.patch"
    target_index = directory / "target-index"
    target_revision = "unavailable"
    status = "rejected"
    checks_result: dict[str, Any] | None = None
    post_sha: str | None = None
    post_matches: bool | None = None
    error: str | None = None
    error_artifact: dict[str, Any] | None = None
    applied = False
    try:
        if handoff["status"] != "completed":
            raise HandoffError(
                f"handoff status {handoff['status']} is not eligible for integration"
            )
        if handoff["validation"] != "passed":
            raise HandoffError(
                f"handoff validation is {handoff['validation']}; approved checks must pass before integration"
            )
        if not _review_matches(handoff):
            raise HandoffError("review evidence is missing, failed, or does not match this handoff")
        if not checks:
            raise HandoffError("integration requires at least one approved post-apply check")
        if abort is not None and abort.is_set():
            raise HandoffError("integration aborted before applying handoff")
        if _status_dirty(base):
            raise HandoffError(
                "target checkout is dirty; refusing integration to preserve unrelated work"
            )
        target_revision = _git(base, "rev-parse", "HEAD")
        if target_revision != handoff["revision"]:
            raise HandoffError(
                f"target base changed: expected {handoff['revision']}, found {target_revision}"
            )
        # Reload and copy the handoff under the integration lease: applying the original path after a
        # separate validation would permit a patch-path replacement race, so the private snapshot is
        # the immutable input below.
        handoff = load_handoff(base, task_id)
        if handoff["status"] != "completed" or handoff["validation"] != "passed":
            raise HandoffError(
                "handoff status or validation changed while acquiring integration lease"
            )
        if not _review_matches(handoff):
            raise HandoffError("review evidence changed while acquiring integration lease")
        if _status_dirty(base) or _git(base, "rev-parse", "HEAD") != target_revision:
            raise HandoffError("target changed while acquiring integration lease")
        patch = Path(handoff["patchPath"]).read_bytes()
        if _sha256(patch) != handoff["patchSha256"]:
            raise HandoffError("handoff patch changed while acquiring integration lease")
        _write_new(snapshot_path, patch)
        _run_git(base, ["apply", "--check", "--binary", str(snapshot_path)])
        index_env = {"GIT_INDEX_FILE": str(target_index)}
        _run_git(base, ["read-tree", handoff["revision"]], index_env)
        _run_git(base, ["apply", "--cached", "--binary", str(snapshot_path)], index_env)
        _run_git(base, ["apply", "--binary", str(snapshot_path)])
        applied = True
        applied_patch = _run_git(
            base, ["diff", "--cached", "--binary", handoff["revision"]], index_env
        )
        if _sha256(applied_patch) != handoff["patchSha256"]:
            raise HandoffError("applied target patch identity does not match reviewed handoff")
        checks_result = run_approved_checks_at(base, handoff, checks, abort)
        final_revision = _git(base, "rev-parse", "HEAD")
        if final_revision != target_revision:
            raise HandoffError(
                f"target revision changed during post-apply checks: expected {target_revision}, found {final_revision}"
            )
        _run_git(base, ["add", "--all", "--", ".", f":(exclude){HANDOFF_DIR}"], index_env)
        post_patch = _run_git(
            base, ["diff", "--cached", "--binary", handoff["revision"]], index_env
        )
        post_sha = _sha256(post_patch)
        post_matches = post_sha == handoff["patchSha256"]
        if not checks_result["passed"]:
            status = "failed"
            raise HandoffError(
                "post-apply approved checks failed; applied changes were retained for inspection"
            )
        if not post_matches:
            raise HandoffError(
                "post-apply checks changed the target beyond the reviewed patch; changes were retained for inspection"
            )
        status = "applied"
    except (HandoffError, OSError, ValueError) as caught:
        error = str(caught)
        if status != "failed":
            if applied:
                status = "failed"
            elif any(
                text in error
                for text in ("target base changed", "target changed", "dirty", "apply")
            ):
                status = "conflict"
            else:
                status = "rejected"
        try:
            error_path = directory / "error.txt"
            _write_new(error_path, error)
            error_artifact = _empty_artifact(error_path, error)
        except OSError as artifact_error:
            error = f"{error}; integration error artifact failed: {artifact_error}"
    result: dict[str, Any] = {
        "schemaVersion": 1,
        "taskId": task_id,
        "target": base,
        "expectedRevision": handoff["revision"],
        "targetRevision": target_revision,
        "patchPath": patch_path,
        "patchSnapshotPath": str(snapshot_path),
        "patchSha256": handoff["patchSha256"],
        "status": status,
        "recordPath": str(record_path),
    }
    if checks_result is not None:
        result["checks"] = checks_result
    if post_sha is not None:
        result["postApplyPatchSha256"] = post_sha
    if post_matches is not None:
        result["postApplyPatchMatches"] = post_matches
    if error_artifact is not None:
        result["errorArtifact"] = error_artifact
    if error:
        result["error"] = error
    result["recordedAt"] = _timestamp()
    _write_new(record_path, _json_text(result))
    _release_integration_lock(lock, task_id)
    return result
