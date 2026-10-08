"""Read-only provenance checks for a recorded release-acceptance run.

This module validates the receipt produced by :mod:`run_recorder`.  It does
not execute the recorded command, rebuild a candidate, import product code, or
follow paths from the receipt outside the caller supplied evidence root.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.release_acceptance import candidate

RUN_SCHEMA_VERSION = "release-acceptance-run.v1"
CANDIDATE_SCHEMA_VERSION = "release-acceptance-candidate.v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
INDEX_MODES = frozenset({"100644", "100755", "120000"})


def _error(errors: list[str], message: str) -> None:
    if message not in errors:
        errors.append(message)


def _contained_path(
    raw_path: Any,
    evidence_root: Path,
    errors: list[str],
    label: str,
) -> Path | None:
    """Resolve one path and check containment without opening it."""

    if isinstance(raw_path, Path):
        raw = raw_path
    elif isinstance(raw_path, str) and raw_path:
        raw = Path(raw_path)
    else:
        _error(errors, f"{label} must be a non-empty path string")
        return None
    candidate_path = raw if raw.is_absolute() else evidence_root / raw
    try:
        resolved = candidate_path.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        _error(errors, f"{label} cannot be resolved: {type(exc).__name__}")
        return None
    try:
        resolved.relative_to(evidence_root)
    except ValueError:
        _error(errors, f"{label} escapes evidence_root")
        return None
    return resolved


def _contained_file(
    raw_path: Any,
    evidence_root: Path,
    errors: list[str],
    label: str,
    *,
    require_nonempty: bool = False,
) -> Path | None:
    """Check one evidence artifact before opening it."""

    resolved = _contained_path(raw_path, evidence_root, errors, label)
    if resolved is None:
        return None
    try:
        stat_result = resolved.stat()
        mode = stat_result.st_mode
        size = stat_result.st_size
    except (OSError, RuntimeError) as exc:
        _error(errors, f"{label} is unavailable: {type(exc).__name__}")
        return None
    if not stat.S_ISREG(mode):
        _error(errors, f"{label} is not a regular file")
        return None
    if require_nonempty and size <= 0:
        _error(errors, f"{label} is empty")
        return None
    return resolved


def _raw_evidence_path(raw_path: Any, evidence_root: Path) -> Path | None:
    if isinstance(raw_path, Path):
        raw = raw_path
    elif isinstance(raw_path, str) and raw_path:
        raw = Path(raw_path)
    else:
        return None
    return raw if raw.is_absolute() else evidence_root / raw


def _lexically_contained_path(
    raw_path: Any,
    evidence_root: Path,
    errors: list[str],
    label: str,
) -> Path | None:
    """Contain a possibly unresolved artifact without following its leaf."""

    raw = _raw_evidence_path(raw_path, evidence_root)
    if raw is None:
        _error(errors, f"{label} must be a non-empty path string")
        return None
    if "\0" in str(raw):
        _error(errors, f"{label} contains a NUL byte")
        return None
    try:
        parent = raw.parent.resolve(strict=False)
        parent.relative_to(evidence_root)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, ValueError):
            _error(errors, f"{label} escapes evidence_root")
        else:
            _error(errors, f"{label} cannot be resolved: {type(exc).__name__}")
        return None
    return parent / raw.name


def _load_json(path: Path, errors: list[str], label: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _error(errors, f"{label} is not readable JSON: {type(exc).__name__}")
        return None
    if not isinstance(value, dict):
        _error(errors, f"{label} must contain a JSON object")
        return None
    return value


def _hash_file(path: Path, errors: list[str], label: str) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError as exc:
        _error(errors, f"{label} cannot be hashed: {type(exc).__name__}")
        return None


def _timestamp(value: Any, errors: list[str], label: str) -> datetime | None:
    if not isinstance(value, str) or not value:
        _error(errors, f"{label} must be a timezone-aware timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        _error(errors, f"{label} must be a timezone-aware timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _error(errors, f"{label} must include a timezone")
        return None
    return parsed


def _candidate_record_id(record: dict[str, Any], errors: list[str], label: str) -> str | None:
    """Recompute the ID through candidate.py's established identity payload."""

    try:
        identity = candidate._identity(record)
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        _error(errors, f"{label} candidate record cannot be canonicalized: {type(exc).__name__}")
        return None


def _validate_candidate_snapshot(
    path: Path,
    expected_candidate_id: str,
    errors: list[str],
    label: str,
) -> None:
    snapshot = _load_json(path, errors, label)
    if snapshot is None:
        return
    if snapshot.get("ok") is not True:
        _error(errors, f"{label} is not a successful candidate snapshot")
    if snapshot.get("identity_available") is not True:
        _error(errors, f"{label} does not prove candidate identity")
    wrapper_id = snapshot.get("candidate_id")
    record = snapshot.get("record")
    if not isinstance(wrapper_id, str) or not wrapper_id:
        _error(errors, f"{label} is missing candidate_id")
    if not isinstance(record, dict):
        _error(errors, f"{label} is missing a candidate record")
        return
    if record.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        _error(errors, f"{label} has an unsupported candidate schema")
    record_id = record.get("candidate_id")
    if not isinstance(record_id, str) or not record_id:
        _error(errors, f"{label} record is missing candidate_id")
    if isinstance(wrapper_id, str) and isinstance(record_id, str) and wrapper_id != record_id:
        _error(errors, f"{label} self-asserted candidate_id differs from its record")
    if isinstance(wrapper_id, str) and wrapper_id != expected_candidate_id:
        _error(errors, f"{label} candidate_id differs from expected candidate")
    if isinstance(record_id, str) and record_id != expected_candidate_id:
        _error(errors, f"{label} record candidate_id differs from expected candidate")
    canonical_id = _candidate_record_id(record, errors, label)
    if canonical_id is not None and record_id != canonical_id:
        _error(errors, f"{label} candidate_id does not match its canonical record")

    git = record.get("git")
    if (
        not isinstance(git, Mapping)
        or not isinstance(git.get("commit"), str)
        or GIT_COMMIT_RE.fullmatch(git["commit"]) is None
    ):
        _error(errors, f"{label} has no readable git identity")
    if isinstance(git, Mapping) and git.get("error"):
        _error(errors, f"{label} contains a git identity error")
    git_status = record.get("git_status")
    if not isinstance(git_status, Mapping) or not isinstance(git_status.get("clean"), bool):
        _error(errors, f"{label} has no proven git status")

    manifest = record.get("manifest")
    if not isinstance(manifest, list):
        _error(errors, f"{label} manifest is unreadable")
    else:
        for index, item in enumerate(manifest):
            if not isinstance(item, Mapping):
                _error(errors, f"{label} manifest entry {index} is unreadable")
                continue
            if item.get("issue"):
                _error(errors, f"{label} manifest entry {index} is unproven")
            digest = item.get("sha256")
            tombstone = item.get("mode") == "missing" and item.get("index_mode") in INDEX_MODES
            if not tombstone and (
                not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None
            ):
                _error(errors, f"{label} manifest entry {index} has no content digest")
    issues = record.get("issues")
    if isinstance(issues, list) and any(
        any(
            fragment in str(issue)
            for fragment in (
                "file list unavailable",
                "git metadata error",
                "unreadable or unproven",
            )
        )
        for issue in issues
    ):
        _error(errors, f"{label} contains an unproven identity issue")


def _validate_hash_field(value: Any, errors: list[str], label: str) -> None:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        _error(errors, f"{label} must be a sha256 digest")


def _validate_result_paths(
    record: dict[str, Any],
    evidence_root: Path,
    errors: list[str],
    expected_result_path: str | Path | None,
    expected_result_sha256: str | None,
) -> None:
    requested = record.get("result_paths")
    if requested is None:
        if expected_result_path is not None or expected_result_sha256 is not None:
            _error(errors, "expected result was not recorded")
        return
    if not isinstance(requested, Mapping):
        _error(errors, "result_paths must be a label-to-record mapping")
        return

    output_root = _contained_path(record.get("output_root"), evidence_root, errors, "output_root")
    retained: dict[Path, tuple[str, str]] = {}
    for label, details in requested.items():
        label_text = str(label)
        if not isinstance(label, str) or not label or "\0" in label:
            _error(errors, "result path labels must be non-empty strings without NUL bytes")
            continue
        if not isinstance(details, Mapping):
            _error(errors, f"result path {label_text!r} record is malformed")
            continue
        raw_path = details.get("path")
        lexical_path = _lexically_contained_path(
            raw_path, evidence_root, errors, f"result path {label_text!r}"
        )
        if lexical_path is None:
            continue
        if output_root is not None:
            try:
                lexical_path.relative_to(output_root)
            except ValueError:
                _error(errors, f"result path {label_text!r} is outside output_root")
        status = details.get("status")
        if status == "retained":
            digest = details.get("sha256")
            size = details.get("size")
            _validate_hash_field(digest, errors, f"result path {label_text!r} sha256")
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                _error(errors, f"result path {label_text!r} size is invalid")
            raw_evidence_path = _raw_evidence_path(raw_path, evidence_root)
            if raw_evidence_path is None:
                continue
            try:
                is_symlink = raw_evidence_path.is_symlink()
            except OSError:
                is_symlink = False
            if is_symlink:
                _error(errors, f"result path {label_text!r} retained a symlink")
                continue
            retained_path = _contained_file(
                raw_path, evidence_root, errors, f"result path {label_text!r}"
            )
            if retained_path is None:
                continue
            if output_root is not None:
                try:
                    retained_path.relative_to(output_root)
                except ValueError:
                    _error(errors, f"result path {label_text!r} is outside output_root")
                    continue
            actual_digest = _hash_file(retained_path, errors, f"result path {label_text!r}")
            if actual_digest is not None and actual_digest != digest:
                _error(errors, f"result path {label_text!r} hash does not match retained bytes")
            try:
                actual_size = retained_path.stat().st_size
            except OSError as exc:
                _error(
                    errors, f"result path {label_text!r} size is unreadable: {type(exc).__name__}"
                )
            else:
                if isinstance(size, int) and not isinstance(size, bool) and actual_size != size:
                    _error(errors, f"result path {label_text!r} size does not match retained bytes")
            if isinstance(digest, str):
                retained[retained_path] = (label, digest)
        elif status == "unresolved":
            if details.get("sha256") is not None or details.get("size") is not None:
                _error(errors, f"result path {label_text!r} unresolved record has retained fields")
            if not isinstance(details.get("reason"), str) or not details["reason"]:
                _error(errors, f"result path {label_text!r} unresolved record has no reason")
        else:
            _error(errors, f"result path {label_text!r} has an invalid status")

    if expected_result_path is None and expected_result_sha256 is None:
        return
    if expected_result_path is None:
        _error(errors, "expected_result_path is required with expected_result_sha256")
        return
    if expected_result_sha256 is None:
        _error(errors, "expected_result_sha256 is required with expected_result_path")
        return
    _validate_hash_field(expected_result_sha256, errors, "expected_result_sha256")
    expected_raw = _raw_evidence_path(expected_result_path, evidence_root)
    if expected_raw is not None and expected_raw.is_symlink():
        _error(errors, "expected result path is a symlink")
        return
    expected = _contained_file(
        expected_result_path, evidence_root, errors, "expected result artifact"
    )
    if expected is None:
        return
    if output_root is not None:
        try:
            expected.relative_to(output_root)
        except ValueError:
            _error(errors, "expected result path is outside output_root")
            return
    retained_entry = retained.get(expected)
    if retained_entry is None:
        _error(errors, "expected result does not match a retained recorded result")
        return
    _, recorded_digest = retained_entry
    if recorded_digest != expected_result_sha256:
        _error(errors, "expected result hash differs from recorded result")
    actual_digest = _hash_file(expected, errors, "expected result artifact")
    if actual_digest is not None and actual_digest != expected_result_sha256:
        _error(errors, "expected result hash does not match actual bytes")


def validate_run_record(
    path: Path,
    evidence_root: Path,
    expected_candidate_id: str,
    expected_result_path: str | Path | None = None,
    expected_result_sha256: str | None = None,
) -> list[str]:
    """Return provenance errors for one recorded run, without side effects.

    An empty list means that the receipt and its retained artifacts establish
    stable, completed provenance for ``expected_candidate_id``.  The function
    never executes ``argv``, invokes candidate discovery, or reads a path from
    ``cwd``/``candidate_root``.  Every evidence artifact is contained and
    checked before it is opened.
    """

    errors: list[str] = []
    if not isinstance(expected_candidate_id, str) or not expected_candidate_id:
        _error(errors, "expected_candidate_id must be a non-empty string")
    try:
        root = Path(evidence_root).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        _error(errors, f"evidence_root is unavailable: {type(exc).__name__}")
        return errors
    if not root.is_dir():
        _error(errors, "evidence_root must be a directory")
        return errors

    receipt = _contained_file(path, root, errors, "run receipt", require_nonempty=True)
    if receipt is None:
        return errors
    record = _load_json(receipt, errors, "run receipt")
    if record is None:
        return errors

    if record.get("schema_version") != RUN_SCHEMA_VERSION:
        _error(errors, "run receipt has an unsupported schema")
    if record.get("acceptance_status") != "not_evaluated":
        _error(errors, "run receipt acceptance_status is not not_evaluated")
    if record.get("outcome") != "completed":
        _error(errors, "run outcome is not completed")
    if record.get("interrupted") is not False:
        _error(errors, "run was interrupted")
    argv = record.get("argv")
    if not isinstance(argv, list) or not argv:
        _error(errors, "argv must be a non-empty string list")
    elif any(not isinstance(argument, str) or "\0" in argument for argument in argv):
        _error(errors, "argv must contain strings without NUL bytes")
    if record.get("shell") is not False:
        _error(errors, "shell must be false")
    cwd = record.get("cwd")
    if not isinstance(cwd, str) or not cwd or "\0" in cwd:
        _error(errors, "cwd must be a non-empty path string")
    candidate_root = record.get("candidate_root")
    if not isinstance(candidate_root, str) or not candidate_root or "\0" in candidate_root:
        _error(errors, "candidate_root must be a non-empty path string")

    started = _timestamp(record.get("started_at"), errors, "started_at")
    ended = _timestamp(record.get("ended_at"), errors, "ended_at")
    if started is not None and ended is not None and ended < started:
        _error(errors, "ended_at precedes started_at")

    process = record.get("process")
    if not isinstance(process, Mapping):
        _error(errors, "process metadata is missing")
    else:
        exit_code = process.get("exit_code")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code != 0:
            _error(errors, "process exit_code is not zero")
        if process.get("timed_out") is not False:
            _error(errors, "process timed_out is not false")
        if process.get("start_new_session") is not True:
            _error(errors, "process start_new_session must be true")
        termination = process.get("termination")
        if not isinstance(termination, Mapping):
            _error(errors, "process termination metadata is missing")
        elif termination.get("cleanup_unresolved") is not False:
            _error(errors, "process cleanup is unresolved")

    output = record.get("output")
    if not isinstance(output, Mapping):
        _error(errors, "output metadata is missing")
        output = {}
    if output.get("complete") is not True:
        _error(errors, "retained output is incomplete")
    if output.get("loss_reason") is not None:
        _error(errors, "retained output has a loss reason")
    _validate_hash_field(output.get("stdout_sha256"), errors, "stdout_sha256")
    _validate_hash_field(output.get("stderr_sha256"), errors, "stderr_sha256")

    stdout = _contained_file(output.get("stdout"), root, errors, "stdout artifact")
    stderr = _contained_file(output.get("stderr"), root, errors, "stderr artifact")
    if stdout is not None:
        actual = _hash_file(stdout, errors, "stdout artifact")
        if actual is not None and actual != output.get("stdout_sha256"):
            _error(errors, "stdout hash does not match retained bytes")
    if stderr is not None:
        actual = _hash_file(stderr, errors, "stderr artifact")
        if actual is not None and actual != output.get("stderr_sha256"):
            _error(errors, "stderr hash does not match retained bytes")

    _validate_result_paths(
        record,
        root,
        errors,
        expected_result_path,
        expected_result_sha256,
    )

    candidate_metadata = record.get("candidate")
    if not isinstance(candidate_metadata, Mapping):
        _error(errors, "candidate metadata is missing")
    else:
        if candidate_metadata.get("status") != "stable":
            _error(errors, "candidate status is not stable")
        if candidate_metadata.get("source_drift") is not False:
            _error(errors, "candidate source_drift is not false")
        before = _contained_file(
            candidate_metadata.get("before"),
            root,
            errors,
            "candidate-before artifact",
            require_nonempty=True,
        )
        after = _contained_file(
            candidate_metadata.get("after"),
            root,
            errors,
            "candidate-after artifact",
            require_nonempty=True,
        )
        if before is not None:
            _validate_candidate_snapshot(before, expected_candidate_id, errors, "candidate-before")
        if after is not None:
            _validate_candidate_snapshot(after, expected_candidate_id, errors, "candidate-after")

    run_record_field = record.get("run_record")
    if isinstance(run_record_field, str):
        referenced = _contained_file(run_record_field, root, errors, "run_record reference")
        if referenced is not None and referenced != receipt:
            _error(errors, "run_record reference does not identify this receipt")
    else:
        _error(errors, "run_record reference is missing")
    run_dir_field = record.get("run_dir")
    if isinstance(run_dir_field, str):
        run_dir = Path(run_dir_field)
        run_dir_candidate = run_dir if run_dir.is_absolute() else root / run_dir
        try:
            run_dir_resolved = run_dir_candidate.resolve(strict=False)
            run_dir_resolved.relative_to(root)
        except OSError, RuntimeError, ValueError:
            _error(errors, "run_dir reference escapes evidence_root")
        else:
            if run_dir_resolved != receipt.parent:
                _error(errors, "run_dir reference does not identify the receipt parent")
    else:
        _error(errors, "run_dir reference is missing")

    return errors


__all__ = ["validate_run_record"]
