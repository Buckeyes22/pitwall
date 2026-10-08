"""Durable task records for workbench children.

Records are plain JSON objects (camelCase keys, schemaVersion 1) so a record written by one
process stays readable by any other. Every read validates the full contract, and every mutation
validates the complete candidate before replacing the last known-good record.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any, TypeGuard

Record = dict[str, Any]

TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
EXECUTION_STATES = frozenset(
    {"queued", "running", "succeeded", "failed", "cancelled", "interrupted", "waiting"}
)
ACCEPTANCE_STATES = frozenset({"unchecked", "checks-passed", "reviewed", "accepted", "rejected"})
USAGE_SOURCES = frozenset({"provider-reported", "estimated", "unavailable"})
ROLES = ("scout", "worker", "reviewer")
VIEW_HEAD = 2_000
VIEW_TAIL = 2_000
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")
_TASK_ID = re.compile(r"task-[A-Za-z0-9_-]+")


def _within(root: str, candidate: str) -> bool:
    return PurePath(candidate).is_relative_to(PurePath(root))


def assert_task_artifact_path(path: object, *, root: str, task_id: str, kind: str) -> None:
    """Validate an artifact path before exposing or persisting it in a task record.

    The artifact must be an existing regular file owned by this task's record directory;
    realpath checks cover both final and intermediate symlink escapes.
    """
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        raise ValueError("artifact path must be an absolute path")
    if not _TASK_ID.fullmatch(task_id):
        raise ValueError("artifact task identity is invalid")
    lexical_root = os.path.abspath(root)
    try:
        real_root = os.path.realpath(lexical_root, strict=True)
    except OSError:
        raise ValueError("artifact root is unavailable") from None
    candidate = os.path.abspath(path)
    if not _within(lexical_root, candidate):
        raise ValueError("artifact path escapes task record directory")
    expected = re.compile(rf"{re.escape(task_id)}\.{re.escape(kind)}-[0-9a-f]{{16}}\.txt")
    if not expected.fullmatch(os.path.basename(candidate)):
        raise ValueError("artifact path is not owned by task")
    try:
        resolved = os.path.realpath(candidate, strict=True)
    except OSError:
        raise ValueError("artifact path is unavailable") from None
    if not _within(real_root, resolved):
        raise ValueError("artifact path symlink escapes task record directory")
    if os.path.dirname(resolved) != real_root or not expected.fullmatch(os.path.basename(resolved)):
        raise ValueError("artifact path symlink is not owned by task")
    if os.path.islink(candidate) or not os.path.isfile(candidate):
        raise ValueError("artifact path must be a regular file")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _is_object(value: object) -> bool:
    return isinstance(value, dict)


def _is_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"invalid task record {field}")
    return value


def _optional_string(value: object, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not value):
        raise ValueError(f"invalid task record {field}")


def process_start_time(pid: int) -> str | None:
    """Return the kernel start tick of a process (Linux only), or None when unavailable."""
    if sys.platform != "linux":
        return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    end = stat.rfind(") ")
    if end < 0:
        return None
    fields = stat[end + 2 :].split()
    return fields[19] if len(fields) > 19 and fields[19] else None


def owner_alive(
    pid: int,
    expected_start_time: str | None = None,
    start_time_reader: Callable[[], str | None] | None = None,
) -> bool:
    """Whether the owning process is still the one that created the record.

    Missing start metadata leaves identity uncertain; the record stays owned in that case,
    because recovering it as stale could let a second process write over a live owner.
    """
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
    except OSError:
        return True
    if re.search(r"^State:\s+Z", status, re.MULTILINE):
        return False
    actual = start_time_reader() if start_time_reader else process_start_time(pid)
    return actual is None or expected_start_time is None or actual == expected_start_time


def _required_timestamp(value: object, field: str) -> None:
    text = _required_string(value, field)
    # Records are emitted in one fixed UTC form; a broad parser would admit values such as "1".
    if not _TIMESTAMP.fullmatch(text):
        raise ValueError(f"invalid task record {field}")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        raise ValueError(f"invalid task record {field}") from None
    if parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z") != text:
        raise ValueError(f"invalid task record {field}")


def _timestamp_value(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


def _validate_artifact(
    value: object, field: str, artifact_root: str | None, task_id: str | None
) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"invalid task record {field}")
    path = _required_string(value.get("path"), f"{field}.path")
    if artifact_root is not None and task_id is not None:
        kind = {"artifacts.result": "result", "artifacts.summary": "summary"}.get(field)
        if kind is None:
            raise ValueError(f"invalid task record {field}")
        try:
            assert_task_artifact_path(path, root=artifact_root, task_id=task_id, kind=kind)
        except ValueError as error:
            raise ValueError(f"invalid task record {field}.path: {error}") from None
    sha = value.get("sha256")
    if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
        raise ValueError(f"invalid task record {field}.sha256")
    size = value.get("bytes")
    if not _is_int(size) or size < 0:
        raise ValueError(f"invalid task record {field}.bytes")
    if (
        value.get("complete") is not True
        or not isinstance(value.get("view"), str)
        or not isinstance(value.get("truncated"), bool)
    ):
        raise ValueError(f"invalid task record {field}")
    try:
        contents = Path(path).read_bytes()
    except OSError:
        raise ValueError(f"invalid task record {field}: artifact content is unavailable") from None
    if len(contents) != size:
        raise ValueError(
            f"invalid task record {field}.bytes: artifact content length does not match metadata"
        )
    if hashlib.sha256(contents).hexdigest() != sha:
        raise ValueError(
            f"invalid task record {field}.sha256: artifact content hash does not match metadata"
        )


def _validate_instruction_provenance(value: object) -> None:
    if (
        not isinstance(value, dict)
        or value.get("source")
        not in ("resource-loader", "public-context-files", "final-system-prompt")
        or not isinstance(value.get("complete"), bool)
        or not isinstance(value.get("files"), list)
    ):
        raise ValueError("invalid task record instructionProvenance")
    _optional_string(value.get("reason"), "instructionProvenance.reason")
    for entry in value["files"]:
        if not (
            isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and entry["path"]
            and isinstance(entry.get("sha256"), str)
            and _SHA256.fullmatch(entry["sha256"])
            and _is_int(entry.get("bytes"))
            and entry["bytes"] >= 0
            and isinstance(entry.get("estimated"), bool)
        ):
            raise ValueError("invalid task record instructionProvenance file")
    prompt_sha = value.get("promptSha256")
    if prompt_sha is not None and (
        not isinstance(prompt_sha, str) or not _SHA256.fullmatch(prompt_sha)
    ):
        raise ValueError("invalid task record instructionProvenance.promptSha256")
    prompt_bytes = value.get("promptBytes")
    if prompt_bytes is not None and (not _is_int(prompt_bytes) or prompt_bytes < 0):
        raise ValueError("invalid task record instructionProvenance.promptBytes")
    inline = value.get("inline")
    if inline is not None and (
        not isinstance(inline, list)
        or any(
            not isinstance(entry, dict)
            or entry.get("source") not in ("customPrompt", "appendSystemPrompt")
            or not isinstance(entry.get("sha256"), str)
            or not _SHA256.fullmatch(entry["sha256"])
            or not _is_int(entry.get("bytes"))
            or entry["bytes"] < 0
            for entry in inline
        )
    ):
        raise ValueError("invalid task record instructionProvenance.inline")


def _evidence_link(record: Mapping[str, Any]) -> Record:
    return {
        "taskId": record["taskId"],
        "runId": record["runId"],
        "attemptId": record["attemptId"],
        "workspace": record["workspace"],
        "revision": record["baseRevision"],
    }


def _validate_evidence_link(value: object, field: str, record: Mapping[str, Any]) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"invalid task record {field}")
    for key in ("taskId", "runId", "attemptId", "workspace", "revision"):
        _required_string(value.get(key), f"{field}.{key}")
    if (
        value["taskId"] != record["taskId"]
        or value["runId"] != record["runId"]
        or value["attemptId"] != record["attemptId"]
        or value["workspace"] != record["workspace"]
        or value["revision"] != record["baseRevision"]
    ):
        raise ValueError(f"invalid task record {field} identity")


def _validate_token(value: object, field: str) -> None:
    if value is not None and not (_is_int(value) and value >= 0):
        raise ValueError(f"invalid task record {field}")


def _assert_owned_evidence_path(path: str, workspace_value: str, field: str) -> None:
    prefix = f"invalid task record {field}.evidencePath"
    if not os.path.isabs(path):
        raise ValueError(f"{prefix}: evidence path must be absolute")
    workspace = os.path.abspath(workspace_value)
    parent = os.path.dirname(workspace)
    lexical_root = (
        parent
        if os.path.basename(workspace) == "worktree"
        and os.path.basename(parent).startswith("task-")
        else workspace
    )
    if not _within(lexical_root, os.path.abspath(path)):
        raise ValueError(f"{prefix}: evidence path escapes task workspace")
    try:
        root = os.path.realpath(lexical_root, strict=True)
        resolved = os.path.realpath(path, strict=True)
    except OSError:
        raise ValueError(f"{prefix}: evidence path is unavailable") from None
    if not _within(root, resolved):
        raise ValueError(f"{prefix}: evidence path escapes task workspace")
    if os.path.islink(path) or not os.path.isfile(path):
        raise ValueError(f"{prefix}: evidence path must be a regular file")


def _string_list(value: object, field: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(entry, str) or not entry for entry in value
    ):
        raise ValueError(f"invalid task record {field}")


def _validate_task_result(value: Mapping[str, Any]) -> None:
    _string_list(value.get("changedFiles"), "changedFiles")
    _string_list(value.get("blockers"), "blockers")
    _string_list(value.get("risks"), "risks")
    verification = value.get("verification")
    if not isinstance(verification, list):
        raise ValueError("invalid task record verification")
    for index, check in enumerate(verification):
        field = f"verification[{index}]"
        if not isinstance(check, dict):
            raise ValueError(f"invalid task record {field}")
        _required_string(check.get("checkId"), f"{field}.checkId")
        code = check.get("exitCode")
        if not _is_int(code) or code < 0:
            raise ValueError(f"invalid task record {field}.exitCode")
        evidence_path = _required_string(check.get("evidencePath"), f"{field}.evidencePath")
        _assert_owned_evidence_path(evidence_path, value["workspace"], field)
        _validate_evidence_link(check.get("evidence"), f"{field}.evidence", value)
    usage = value.get("usage")
    if not isinstance(usage, dict) or usage.get("source") not in USAGE_SOURCES:
        raise ValueError("invalid task record usage")
    for name in (
        "inputTokens",
        "outputTokens",
        "reasoningTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
        "totalTokens",
    ):
        _validate_token(usage.get(name), f"usage.{name}")
    _validate_evidence_link(usage.get("provenance"), "usage.provenance", value)
    handoff = value.get("handoff")
    if handoff is not None:
        if not isinstance(handoff, dict):
            raise ValueError("invalid task record handoff")
        _required_string(handoff.get("handoffId"), "handoff.handoffId")
        patch_path = _required_string(handoff.get("patchPath"), "handoff.patchPath")
        _assert_owned_evidence_path(patch_path, value["workspace"], "handoff")
        patch_sha = handoff.get("patchSha256")
        if not isinstance(patch_sha, str) or not _SHA256.fullmatch(patch_sha):
            raise ValueError("invalid task record handoff.patchSha256")
        if hashlib.sha256(Path(patch_path).read_bytes()).hexdigest() != patch_sha:
            raise ValueError("invalid task record handoff.patchSha256: patch hash mismatch")
        _validate_evidence_link(handoff.get("evidence"), "handoff.evidence", value)


def validate_task_record(
    value: object, expected_task_id: str | None = None, artifact_root: str | None = None
) -> Record:
    """Validate a complete record against the version, identity, state, and envelope contracts."""
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("invalid task record schemaVersion")
    task_id = _required_string(value.get("taskId"), "taskId")
    if expected_task_id is not None and task_id != expected_task_id:
        raise ValueError("task record identity mismatch")
    run_id = _required_string(value.get("runId"), "runId")
    attempt_id = _required_string(value.get("attemptId"), "attemptId")
    if (
        not _TASK_ID.fullmatch(task_id)
        or not re.fullmatch(r"run-[A-Za-z0-9_-]+", run_id)
        or not re.fullmatch(r"attempt-[A-Za-z0-9_-]+", attempt_id)
    ):
        raise ValueError("invalid task record lineage identity")
    if value.get("role") not in ROLES:
        raise ValueError("invalid task record role")
    if value.get("executionState") not in EXECUTION_STATES:
        raise ValueError("invalid task record executionState")
    if value.get("acceptanceState") not in ACCEPTANCE_STATES:
        raise ValueError("invalid task record acceptanceState")
    _required_string(value.get("parentSessionId"), "parentSessionId")
    for name in ("parentSessionFile", "parentInstructionProvenancePath"):
        _optional_string(value.get(name), name)
    owner_pid = value.get("ownerPid")
    if owner_pid is not None and (not _is_int(owner_pid) or owner_pid <= 0):
        raise ValueError("invalid task record ownerPid")
    _optional_string(value.get("ownerStartTime"), "ownerStartTime")
    if value.get("ownerStartTime") is not None and owner_pid is None:
        raise ValueError("invalid task record owner identity")
    for name in ("childId", "backendSessionFile", "backendOutputFile", "rootSessionId"):
        _optional_string(value.get(name), name)
    for name in ("workspace", "baseRevision", "task"):
        _required_string(value.get(name), name)
    for name in ("acceptance", "scope", "summary", "error"):
        _optional_string(value.get(name), name)
    _required_timestamp(value.get("startedAt"), "startedAt")
    _required_timestamp(value.get("updatedAt"), "updatedAt")
    if value.get("completedAt") is not None:
        _required_timestamp(value["completedAt"], "completedAt")
    started = _timestamp_value(value["startedAt"])
    updated = _timestamp_value(value["updatedAt"])
    if updated < started:
        raise ValueError("invalid task record updatedAt ordering")
    if value.get("completedAt") is not None:
        completed = _timestamp_value(value["completedAt"])
        if completed < started or completed > updated:
            raise ValueError("invalid task record completedAt ordering")
    if value["executionState"] in TERMINAL_STATES and value.get("completedAt") is None:
        raise ValueError("invalid task record terminal completion")
    profile = value.get("profile")
    if not isinstance(profile, dict) or profile.get("credentialClass") not in (
        "keyless",
        "api-key-env",
    ):
        raise ValueError("invalid task record profile")
    for name in ("name", "provider", "modelId", "api", "endpoint", "resourceGroup"):
        _required_string(profile.get(name), f"profile.{name}")
    if profile.get("accountRef") is not None:
        _required_string(profile["accountRef"], "profile.accountRef")
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("invalid task record artifacts")
    for kind in ("result", "summary"):
        if artifacts.get(kind) is not None:
            _validate_artifact(artifacts[kind], f"artifacts.{kind}", artifact_root, task_id)
    _validate_task_result(value)
    if value.get("instructionProvenance") is not None:
        _validate_instruction_provenance(value["instructionProvenance"])
    envelope = value.get("envelope")
    if (
        not isinstance(envelope, dict)
        or envelope.get("schemaVersion") != 1
        or envelope.get("taskId") != task_id
        or envelope.get("role") != value["role"]
        or not isinstance(envelope.get("profile"), dict)
    ):
        raise ValueError("invalid task record envelope")
    if _required_string(envelope.get("task"), "envelope.task") != value["task"]:
        raise ValueError("invalid task record envelope task")
    for name in ("validationProfile", "workspacePolicy", "contextPolicy"):
        _required_string(envelope.get(name), f"envelope.{name}")
    if envelope.get("acceptance") != value.get("acceptance") or envelope.get("scope") != value.get(
        "scope"
    ):
        raise ValueError("invalid task record envelope scope")
    worker = value["role"] == "worker"
    if (
        envelope["validationProfile"] != ("approved-checks" if worker else "read-only-observation")
        or envelope["workspacePolicy"]
        != ("isolated-handoff" if worker else "parent-workspace-read-only")
        or envelope["contextPolicy"] != "isolated-without-parent-transcript"
    ):
        raise ValueError("invalid task record envelope policy")
    inputs = envelope.get("inputs")
    constraints = envelope.get("constraints")
    if (
        not isinstance(constraints, list)
        or any(not isinstance(entry, str) for entry in constraints)
        or not isinstance(inputs, dict)
        or any(not key or not isinstance(entry, str) for key, entry in inputs.items())
    ):
        raise ValueError("invalid task record envelope inputs")
    if json.dumps(envelope["profile"], sort_keys=True) != json.dumps(profile, sort_keys=True):
        raise ValueError("invalid task record envelope profile")
    return value


def compact_task_text(text: str) -> tuple[str, bool]:
    """Return a bounded head-and-tail view of text and whether it was truncated."""
    if len(text) <= VIEW_HEAD + VIEW_TAIL:
        return text, False
    return (
        f"{text[:VIEW_HEAD]}\n…[truncated; complete result is in the artifact]…\n{text[-VIEW_TAIL:]}",
        True,
    )


def resolve_base_revision(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            stdin=subprocess.DEVNULL,
        )
    except OSError, subprocess.SubprocessError:
        return "unavailable"
    return result.stdout.strip() or "unavailable"


def _atomic_write(path: Path, value: str) -> None:
    temporary = path.with_name(f"{path.name}.{uuid.uuid4()}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(value)
    os.replace(temporary, path)


def _envelope(
    role: str,
    profile: Mapping[str, Any],
    task: str,
    acceptance: str | None,
    scope: str | None,
    task_id: str,
) -> Record:
    worker = role == "worker"
    envelope: Record = {
        "schemaVersion": 1,
        "taskId": task_id,
        "role": role,
        "profile": dict(profile),
        "task": task,
    }
    if acceptance:
        envelope["acceptance"] = acceptance
    if scope:
        envelope["scope"] = scope
    envelope.update(
        constraints=["single native child", "no unmanaged child spawning"],
        inputs={},
        validationProfile="approved-checks" if worker else "read-only-observation",
        workspacePolicy="isolated-handoff" if worker else "parent-workspace-read-only",
        contextPolicy="isolated-without-parent-transcript",
    )
    return envelope


def _normalized_usage(record: Mapping[str, Any], usage: Mapping[str, Any] | None) -> Record:
    usage = usage or {}
    normalized: Record = {"source": usage.get("source", "unavailable")}
    for name in (
        "inputTokens",
        "outputTokens",
        "reasoningTokens",
        "cacheReadTokens",
        "cacheWriteTokens",
        "totalTokens",
    ):
        normalized[name] = usage.get(name)
    normalized["provenance"] = _evidence_link(record)
    return normalized


class TaskRecordStore:
    """Store of task records under `<root>/task-records`, serialized per task within a process."""

    def __init__(self, root: str) -> None:
        self.directory = Path(os.path.abspath(root)) / "task-records"
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _path(self, task_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", task_id):
            raise ValueError("invalid task record id")
        return self.directory / f"{task_id}.json"

    def _lock(self, task_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(task_id, threading.Lock())

    def _read(self, task_id: str) -> Record | None:
        try:
            raw = self._path(task_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return validate_task_record(json.loads(raw), task_id, str(self.directory))

    def _write(self, record: Record) -> None:
        _atomic_write(
            self._path(record["taskId"]), f"{json.dumps(record, indent=2, ensure_ascii=False)}\n"
        )

    def _update_unlocked(self, task_id: str, patch: Mapping[str, Any]) -> Record:
        current = self._read(task_id)
        if current is None:
            raise ValueError(f"task record not found: {task_id}")
        # A terminal state is immutable. Detached child callbacks can arrive after a deadline or
        # cancellation: keep the terminal result, but let the callback attach the child identity
        # and provenance needed to inspect the detached process.
        if current["executionState"] in TERMINAL_STATES:
            metadata: Record = {}
            if current.get("childId") is None and patch.get("childId") is not None:
                metadata["childId"] = patch["childId"]
            if (
                current.get("instructionProvenance") is None
                and patch.get("instructionProvenance") is not None
            ):
                metadata["instructionProvenance"] = patch["instructionProvenance"]
            if (
                any(key not in ("childId", "instructionProvenance") for key in patch)
                or not metadata
            ):
                return current
            updated = {**current, **metadata, "updatedAt": _now()}
            validate_task_record(updated, task_id, str(self.directory))
            self._write(updated)
            return updated
        updated = {**current, **patch, "updatedAt": _now()}
        for name in (
            "taskId", "runId", "attemptId", "startedAt", "parentSessionId",
            "ownerPid", "ownerStartTime", "workspace", "baseRevision",
        ):  # fmt: skip
            if updated.get(name) != current.get(name):
                raise ValueError(f"task record {name} is immutable")
        validate_task_record(updated, task_id, str(self.directory))
        self._write(updated)
        return updated

    def _build(
        self,
        state: str,
        *,
        role: str,
        profile: Mapping[str, Any],
        workspace: str,
        task: str,
        parent_session_id: str | None = None,
        parent_session_file: str | None = None,
        parent_instruction_provenance_path: str | None = None,
        base_revision: str | None = None,
        acceptance: str | None = None,
        scope: str | None = None,
        instruction_provenance: Mapping[str, Any] | None = None,
        changed_files: Sequence[str] | None = None,
        verification: Sequence[Mapping[str, Any]] | None = None,
        blockers: Sequence[str] | None = None,
        risks: Sequence[str] | None = None,
        usage: Mapping[str, Any] | None = None,
        handoff: Mapping[str, Any] | None = None,
    ) -> Record:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        now = _now()
        task_id = f"task-{uuid.uuid4()}"
        resolved_workspace = os.path.abspath(workspace)
        record: Record = {
            "schemaVersion": 1,
            "taskId": task_id,
            "runId": f"run-{uuid.uuid4()}",
            "attemptId": f"attempt-{uuid.uuid4()}",
            "role": role,
            "parentSessionId": parent_session_id or "unavailable",
            "ownerPid": os.getpid(),
        }
        start = process_start_time(os.getpid())
        if start:
            record["ownerStartTime"] = start
        if parent_session_file:
            record["parentSessionFile"] = parent_session_file
        if parent_instruction_provenance_path:
            record["parentInstructionProvenancePath"] = parent_instruction_provenance_path
        record.update(
            profile=dict(profile),
            workspace=resolved_workspace,
            baseRevision=base_revision
            if base_revision is not None
            else resolve_base_revision(workspace),
            task=task,
        )
        if acceptance:
            record["acceptance"] = acceptance
        if scope:
            record["scope"] = scope
        record.update(
            executionState=state,
            acceptanceState="unchecked",
            changedFiles=list(changed_files or []),
            verification=[],
            blockers=list(blockers or []),
            risks=list(risks or []),
            artifacts={},
            startedAt=now,
            updatedAt=now,
            envelope=_envelope(role, profile, task, acceptance, scope, task_id),
        )
        if instruction_provenance:
            record["instructionProvenance"] = dict(instruction_provenance)
        evidence = _evidence_link(record)
        record["verification"] = [{**check, "evidence": evidence} for check in verification or []]
        record["usage"] = _normalized_usage(record, usage)
        if handoff:
            record["handoff"] = {**handoff, "evidence": evidence}
        validate_task_record(record, task_id, str(self.directory))
        path = self._path(task_id)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{json.dumps(record, indent=2, ensure_ascii=False)}\n")
        return record

    def create(self, **fields: Any) -> Record:
        """Create a queued record; the generated record is validated before it is written."""
        return self._build("queued", **fields)

    def create_sync(self, **fields: Any) -> Record:
        """Create a record already marked running, for a caller that starts the child immediately."""
        return self._build("running", **fields)

    def get(self, task_id: str) -> Record | None:
        return self._read(task_id)

    def recover_stale(self, task_id: str) -> Record | None:
        with self._lock(task_id):
            record = self._read(task_id)
            if (
                record is None
                or not record.get("ownerPid")
                or owner_alive(record["ownerPid"], record.get("ownerStartTime"))
                or record["executionState"] not in ("queued", "running", "waiting")
            ):
                return record
            return self._update_unlocked(
                task_id,
                {
                    "executionState": "interrupted",
                    "acceptanceState": "rejected",
                    "error": (
                        f"owning process {record['ownerPid']} is no longer alive; "
                        "detached child termination is unconfirmed; resume is unsupported"
                    ),
                    "completedAt": _now(),
                },
            )

    def update(self, task_id: str, patch: Mapping[str, Any]) -> Record:
        with self._lock(task_id):
            return self._update_unlocked(task_id, patch)

    def _artifact_unlocked(self, task_id: str, kind: str, text: str) -> Record:
        record = self._read(task_id)
        if record is None:
            raise ValueError(f"task record not found: {task_id}")
        if record["executionState"] in TERMINAL_STATES:
            # A terminal record is immutable: refuse before any artifact file is created.
            raise ValueError(
                f"task record {task_id} is {record['executionState']}; "
                f"a late {kind} artifact is refused"
            )
        data = text.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        path = self.directory / f"{task_id}.{kind}-{digest[:16]}.txt"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("task artifact collision") from None
        else:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
        view, truncated = compact_task_text(text)
        artifact: Record = {
            "path": str(path),
            "sha256": digest,
            "bytes": len(data),
            "complete": True,
            "view": view,
            "truncated": truncated,
        }
        patch: Record = {"artifacts": {**record["artifacts"], kind: artifact}}
        if kind == "summary":
            patch["summary"] = view
        self._update_unlocked(task_id, patch)
        return artifact

    def artifact(self, task_id: str, kind: str, text: str) -> Record:
        with self._lock(task_id):
            return self._artifact_unlocked(task_id, kind, text)

    def finish(
        self,
        task_id: str,
        *,
        execution_state: str,
        acceptance_state: str | None = None,
        result: str | None = None,
        error: str | None = None,
        changed_files: Sequence[str] | None = None,
        verification: Sequence[Mapping[str, Any]] | None = None,
        blockers: Sequence[str] | None = None,
        risks: Sequence[str] | None = None,
        usage: Mapping[str, Any] | None = None,
        handoff: Mapping[str, Any] | None = None,
    ) -> Record:
        with self._lock(task_id):
            record = self._read(task_id)
            if record is None:
                raise ValueError(f"task record not found: {task_id}")
            if record["executionState"] in TERMINAL_STATES:
                return record
            if result is not None:
                self._artifact_unlocked(task_id, "result", result)
                self._artifact_unlocked(task_id, "summary", result)
                record = self._read(task_id)
                assert record is not None  # reason: the record was just written by this call
            evidence = _evidence_link(record)
            patch: Record = {"executionState": execution_state}
            if acceptance_state:
                patch["acceptanceState"] = acceptance_state
            if error:
                patch["error"] = error
            if changed_files is not None:
                patch["changedFiles"] = list(changed_files)
            if verification is not None:
                patch["verification"] = [{**check, "evidence": evidence} for check in verification]
            if blockers is not None:
                patch["blockers"] = list(blockers)
            if risks is not None:
                patch["risks"] = list(risks)
            if usage is not None:
                patch["usage"] = _normalized_usage(record, usage)
            if handoff is not None:
                patch["handoff"] = {**handoff, "evidence": evidence}
            patch["completedAt"] = _now()
            if record.get("summary"):
                patch["summary"] = record["summary"]
            return self._update_unlocked(task_id, patch)

    def list(self, limit: int = 20) -> builtins.list[Record]:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        records = [
            validate_task_record(
                json.loads(path.read_text(encoding="utf-8")),
                path.name[: -len(".json")],
                str(self.directory),
            )
            for path in sorted(self.directory.iterdir())
            if path.name.endswith(".json")
        ]
        records.sort(key=lambda item: item["updatedAt"], reverse=True)
        return records[:limit]
