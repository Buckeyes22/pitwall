"""Record one release-acceptance subprocess run with source provenance.

The recorder is deliberately narrower than an acceptance evaluator.  It runs one
explicit argv list, retains the command's output, and records whether the source
identity changed while the command ran.  ``acceptance_status`` is always
``"not_evaluated"``; a zero exit code is never turned into a case acceptance.

Callers that supply credentials must pass their known values through
``secret_values`` so output and metadata can be redacted.  This is a caller
boundary, not a universal secret detector: values that the caller does not
declare cannot be reliably identified.  The CLI intentionally has no option for
secret values; use an environment configured outside the recorder and declare
known values through the Python API when redaction is required.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import secrets
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from tools.release_acceptance import candidate

SCHEMA_VERSION = "release-acceptance-run.v1"
REDACTION_MARKER = "[REDACTED]"
DEFAULT_TERMINATE_GRACE_S = 2.0
MAX_RUN_DIR_ATTEMPTS = 10


class RunRecorderError(ValueError):
    """Raised when a recorder request cannot be made safely."""


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _secret_bytes(secret_values: Iterable[str]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for value in secret_values:
        if not isinstance(value, str):
            raise RunRecorderError("secret_values must contain strings")
        if not value:
            continue
        encoded = value.encode("utf-8", "surrogatepass")
        if encoded not in values:
            values.append(encoded)
    return tuple(sorted(values, key=len, reverse=True))


def _redact_bytes(data: bytes, secrets_to_redact: tuple[bytes, ...]) -> bytes:
    marker = REDACTION_MARKER.encode("utf-8")
    redacted = data
    for secret in secrets_to_redact:
        redacted = redacted.replace(secret, marker)
    return redacted


def _redact_text(value: str, secrets_to_redact: tuple[bytes, ...]) -> str:
    redacted = value
    for secret in secrets_to_redact:
        redacted = redacted.replace(secret.decode("utf-8", "surrogatepass"), REDACTION_MARKER)
    return redacted


def _redact_value(value: Any, secrets_to_redact: tuple[bytes, ...]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, secrets_to_redact)
    if isinstance(value, bytes):
        return _redact_bytes(value, secrets_to_redact).decode("utf-8", "replace")
    if isinstance(value, Path):
        return _redact_text(str(value), secrets_to_redact)
    if isinstance(value, Mapping):
        return {
            _redact_text(str(key), secrets_to_redact): _redact_value(item, secrets_to_redact)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets_to_redact) for item in value]
    return value


def _write_json(path: Path, value: Any, secrets_to_redact: tuple[bytes, ...]) -> None:
    path.write_text(
        json.dumps(_redact_value(value, secrets_to_redact), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _ensure_private_directory(path: Path) -> Path:
    path = path.expanduser().resolve()
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)
        stat_result = path.stat()
    except OSError as exc:
        raise RunRecorderError(f"cannot prepare private output root {path}: {exc}") from exc
    if not path.is_dir():
        raise RunRecorderError(f"output root is not a directory: {path}")
    if stat_result.st_uid != os.geteuid():
        raise RunRecorderError(f"output root is not owned by the current user: {path}")
    if stat_result.st_mode & 0o077:
        raise RunRecorderError(f"output root is not private: {path}")
    return path


def _new_run_directory(output_root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    for _ in range(MAX_RUN_DIR_ATTEMPTS):
        prefix = f"run-{stamp}-{secrets.token_hex(4)}-"
        try:
            run_dir = Path(tempfile.mkdtemp(prefix=prefix, dir=output_root))
            run_dir.chmod(0o700)
            return run_dir
        except FileExistsError:
            continue
    raise RunRecorderError("could not create a unique run directory without overwriting")


def _validate_argv(argv: Sequence[str]) -> list[str]:
    if isinstance(argv, (str, bytes)):
        raise RunRecorderError(
            "argv must be an explicit sequence of arguments, not a command string"
        )
    values = list(argv)
    if not values:
        raise RunRecorderError("argv must not be empty")
    for value in values:
        if not isinstance(value, str) or "\0" in value:
            raise RunRecorderError("argv must contain only strings without NUL bytes")
    return values


def _validate_env(env: Mapping[str, str] | None) -> dict[str, str]:
    if env is None:
        return {}
    values = dict(env)
    for name, value in values.items():
        if not isinstance(name, str) or not name or "\0" in name:
            raise RunRecorderError("environment names must be non-empty strings without NUL bytes")
        if not isinstance(value, str) or "\0" in value:
            raise RunRecorderError("environment values must be strings without NUL bytes")
    return values


def _validate_env_names(names: Iterable[str]) -> list[str]:
    values = list(names)
    for name in values:
        if not isinstance(name, str) or not name or "\0" in name:
            raise RunRecorderError("env_allowlist must contain non-empty names without NUL bytes")
    return sorted(set(values))


def _candidate_snapshot(
    root: Path, artifacts: Mapping[str, str] | None, secrets_to_redact: tuple[bytes, ...]
) -> dict[str, Any]:
    try:
        record = candidate.build_candidate(root, artifacts=dict(artifacts or {}))
    except Exception as exc:  # reason: retain any candidate build failure as run evidence
        return {
            "ok": False,
            "error": _redact_text(f"{type(exc).__name__}: {exc}", secrets_to_redact),
        }
    git = record.get("git")
    git_status = record.get("git_status")
    issues = record.get("issues", ())
    manifest = record.get("manifest")
    unreadable_manifest = isinstance(manifest, list) and any(
        isinstance(item, Mapping) and item.get("issue") for item in manifest
    )
    identity_available = (
        isinstance(record.get("candidate_id"), str)
        and bool(record["candidate_id"])
        and isinstance(git, Mapping)
        and isinstance(git.get("commit"), str)
        and bool(git.get("commit"))
        and not git.get("error")
        and isinstance(git_status, Mapping)
        and isinstance(git_status.get("clean"), bool)
        and isinstance(manifest, list)
        and not unreadable_manifest
        and not any(
            any(
                fragment in str(issue)
                for fragment in (
                    "file list unavailable",
                    "git metadata error",
                    "unreadable or unproven",
                )
            )
            for issue in issues
        )
    )
    return {
        "ok": True,
        "identity_available": identity_available,
        "candidate_id": record.get("candidate_id"),
        "record": record,
    }


def _candidate_status(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, bool | None]:
    before_id = (
        before.get("candidate_id")
        if before.get("ok") and before.get("identity_available")
        else None
    )
    after_id = (
        after.get("candidate_id") if after.get("ok") and after.get("identity_available") else None
    )
    if before_id is None or after_id is None:
        return "unknown", None
    if before_id == after_id:
        return "stable", False
    return "stale", True


def _send_group_signal(pid: int, sig: signal.Signals) -> bool:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return False
    except OSError:
        # The child may have exited between poll() and killpg().  This is safe
        # to retain as a false signal result; the final process return code is
        # still recorded below.
        return False
    return True


def _communicate(process: subprocess.Popen[bytes], timeout_s: float) -> tuple[bytes, bytes, bool]:
    """Collect output for a bounded period, retaining partial timeout bytes."""

    try:
        stdout, stderr = process.communicate(timeout=timeout_s)
        return stdout or b"", stderr or b"", False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.output if isinstance(exc.output, bytes) else b""
        stderr = exc.stderr if isinstance(exc.stderr, bytes) else b""
        return stdout, stderr, True


class _DeferredInterrupt:
    """Record SIGINT instead of raising it while the recorder owns a process.

    CPython can raise KeyboardInterrupt between ``os.read`` returning child bytes
    and ``communicate`` storing them, which silently drops output. Deferring the
    signal keeps collection atomic; ``record_run`` re-raises after the receipt.
    """

    def __init__(self) -> None:
        self.count = 0
        self._previous: Any = None
        self._installed = False

    def __enter__(self) -> _DeferredInterrupt:
        if threading.current_thread() is threading.main_thread():
            self._previous = signal.signal(signal.SIGINT, self._handle)
            self._installed = True
        return self

    def _handle(self, _signum: int, _frame: Any) -> None:
        self.count += 1

    def pending(self) -> bool:
        return self.count > 0

    def __exit__(self, *_exc: object) -> None:
        if self._installed:
            signal.signal(signal.SIGINT, self._previous)


_COLLECTION_SLICE_S = 0.1


def _communicate_until_interrupt(
    process: subprocess.Popen[bytes], timeout_s: float, deferred: _DeferredInterrupt
) -> tuple[bytes, bytes, bool, bool]:
    """Collect like ``_communicate`` but return early once an interrupt is pending.

    Returns stdout, stderr, timed_out, interrupted. ``communicate`` resumes after
    ``TimeoutExpired`` with cumulative output, so slicing loses nothing.
    """

    deadline = time.monotonic() + timeout_s
    stdout = stderr = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return stdout, stderr, True, False
        chunk_out, chunk_err, expired = _communicate(process, min(_COLLECTION_SLICE_S, remaining))
        stdout = _prefer_latest_output(stdout, chunk_out)
        stderr = _prefer_latest_output(stderr, chunk_err)
        if not expired:
            return stdout, stderr, False, False
        if deferred.pending():
            return stdout, stderr, False, True


def _prefer_latest_output(previous: bytes, latest: bytes) -> bytes:
    """TimeoutExpired output is cumulative on supported Python versions."""

    return latest if len(latest) >= len(previous) else previous


def _close_process_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()


def _runtime_metadata() -> dict[str, str]:
    return {
        "interpreter": sys.executable,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "os_name": os.name,
    }


def _validate_paths(cwd: Path, candidate_root: Path, output_root: Path) -> tuple[Path, Path, Path]:
    try:
        cwd = cwd.expanduser().resolve(strict=True)
        candidate_root = candidate_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise RunRecorderError(f"cwd and candidate_root must be readable paths: {exc}") from exc
    if not cwd.is_dir() or not candidate_root.is_dir():
        raise RunRecorderError("cwd and candidate_root must be directories")
    if not output_root.is_absolute():
        raise RunRecorderError("output_root must be an absolute external path")
    output_root = output_root.expanduser().resolve()
    if output_root == cwd or output_root.is_relative_to(cwd):
        raise RunRecorderError("output_root must be outside cwd")
    if output_root == candidate_root or output_root.is_relative_to(candidate_root):
        raise RunRecorderError("output_root must be outside candidate_root")
    output_root = _ensure_private_directory(output_root)
    return cwd, candidate_root, output_root


def _validate_result_paths(
    result_paths: Mapping[str, str | Path] | None,
    *,
    cwd: Path,
    candidate_root: Path,
    output_root: Path,
) -> dict[str, Path]:
    """Validate fresh result destinations before spawning the child."""

    if result_paths is None:
        return {}
    if not isinstance(result_paths, Mapping):
        raise RunRecorderError("result_paths must be a label-to-absolute-path mapping")
    validated: dict[str, Path] = {}
    for label, raw_path in result_paths.items():
        if not isinstance(label, str) or not label or "\0" in label:
            raise RunRecorderError("result path labels must be non-empty strings without NUL bytes")
        if not isinstance(raw_path, (str, Path)):
            raise RunRecorderError("result paths must be strings or Path values")
        path = Path(raw_path)
        if not path.is_absolute() or "\0" in str(path):
            raise RunRecorderError(
                f"result path {label!r} must be an absolute path without NUL bytes"
            )
        try:
            if os.path.lexists(path):
                raise RunRecorderError(f"result path already exists before spawn: {label}")
            resolved = path.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise RunRecorderError(f"result path {label!r} cannot be resolved: {exc}") from exc
        if resolved == output_root or not resolved.is_relative_to(output_root):
            raise RunRecorderError(f"result path {label!r} must be inside output_root")
        if resolved == cwd or resolved.is_relative_to(cwd):
            raise RunRecorderError(f"result path {label!r} must be outside cwd")
        if resolved == candidate_root or resolved.is_relative_to(candidate_root):
            raise RunRecorderError(f"result path {label!r} must be outside candidate_root")
        validated[label] = resolved
    return validated


def _requested_result_records(result_paths: Mapping[str, Path]) -> dict[str, dict[str, Any]]:
    return {
        label: {
            "path": str(path),
            "status": "requested",
            "sha256": None,
            "size": None,
            "reason": None,
        }
        for label, path in sorted(result_paths.items())
    }


def _unresolved_result(path: Path, reason: str) -> dict[str, Any]:
    return {
        "path": str(path),
        "status": "unresolved",
        "sha256": None,
        "size": None,
        "reason": reason,
    }


def _capture_result_paths(
    result_paths: Mapping[str, Path], output_root: Path
) -> dict[str, dict[str, Any]]:
    """Capture only regular, non-symlink files under output_root."""

    captured: dict[str, dict[str, Any]] = {}
    for label, path in sorted(result_paths.items()):
        try:
            if not os.path.lexists(path):
                captured[label] = _unresolved_result(path, "missing")
                continue
            if path.is_symlink():
                try:
                    target = path.resolve(strict=False)
                    reason = "escaped" if not target.is_relative_to(output_root) else "symlink"
                except OSError, RuntimeError:
                    reason = "symlink"
                captured[label] = _unresolved_result(path, reason)
                continue
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(output_root):
                captured[label] = _unresolved_result(path, "escaped")
                continue
            stat_result = resolved.stat()
            if not stat.S_ISREG(stat_result.st_mode):
                captured[label] = _unresolved_result(path, "nonregular")
                continue
            data = resolved.read_bytes()
        except FileNotFoundError:
            captured[label] = _unresolved_result(path, "missing")
        except OSError, RuntimeError:
            captured[label] = _unresolved_result(path, "unreadable")
        else:
            captured[label] = {
                "path": str(path),
                "status": "retained",
                "sha256": _sha256(data),
                "size": len(data),
                "reason": None,
            }
    return captured


def record_run(
    argv: Sequence[str],
    *,
    cwd: Path,
    timeout_s: float,
    output_root: Path,
    candidate_root: Path | None = None,
    artifacts: Mapping[str, str] | None = None,
    result_paths: Mapping[str, str | Path] | None = None,
    env: Mapping[str, str] | None = None,
    env_allowlist: Iterable[str] = (),
    secret_values: Iterable[str] = (),
    terminate_grace_s: float = DEFAULT_TERMINATE_GRACE_S,
) -> dict[str, Any]:
    """Execute and retain one explicit subprocess run.

    The child always runs without a shell in a new process group.  ``output_root``
    must be absolute, private, and outside both ``cwd`` and ``candidate_root``.
    Optional ``result_paths`` destinations must be fresh absolute paths inside
    ``output_root`` and outside both source paths.  Requested result artifacts
    are retained only when they finish as regular non-symlink files inside the
    output root; other outcomes are recorded as unresolved.
    Failed, non-zero, and timed-out runs return evidence records instead of
    raising after the subprocess has started.
    """

    command = _validate_argv(argv)
    if not math.isfinite(timeout_s) or not math.isfinite(terminate_grace_s):
        raise RunRecorderError("timeout_s and terminate_grace_s must be finite")
    if timeout_s <= 0 or terminate_grace_s <= 0:
        raise RunRecorderError("timeout_s and terminate_grace_s must be positive")
    child_env_overrides = _validate_env(env)
    allowlisted_names = _validate_env_names(env_allowlist)
    secrets_to_redact = _secret_bytes(secret_values)
    if any(
        secret.decode("utf-8", "surrogatepass") in argument
        for secret in secrets_to_redact
        for argument in command
    ):
        raise RunRecorderError(
            "known secret values are not accepted in argv; provide credentials through the environment"
        )
    cwd, candidate_root, output_root = _validate_paths(
        Path(cwd),
        Path(candidate_root) if candidate_root is not None else Path(cwd),
        Path(output_root),
    )
    validated_result_paths = _validate_result_paths(
        result_paths,
        cwd=cwd,
        candidate_root=candidate_root,
        output_root=output_root,
    )
    if artifacts is not None:
        for name, path in artifacts.items():
            if not isinstance(name, str) or not isinstance(path, str):
                raise RunRecorderError("artifact names and paths must be strings")

    run_dir = _new_run_directory(output_root)
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    candidate_before_path = run_dir / "candidate-before.json"
    candidate_after_path = run_dir / "candidate-after.json"
    run_path = run_dir / "run.json"
    candidate_before = _candidate_snapshot(candidate_root, artifacts, secrets_to_redact)
    _write_json(candidate_before_path, candidate_before, secrets_to_redact)
    started_at = _utc_now()
    child_env = os.environ.copy()
    child_env.update(child_env_overrides)
    initial_record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_dir.name,
        "acceptance_status": "not_evaluated",
        "outcome": "running",
        "argv": command,
        "cwd": str(cwd),
        "candidate_root": str(candidate_root),
        "output_root": str(output_root),
        "started_at": started_at,
        "ended_at": None,
        "timeout_s": timeout_s,
        "terminate_grace_s": terminate_grace_s,
        "shell": False,
        "environment": {"allowlisted_names": allowlisted_names},
        "redaction": {
            "known_values_supplied": len(secrets_to_redact),
            "marker": REDACTION_MARKER,
            "caller_must_declare_credentials": True,
        },
        "runtime": _runtime_metadata(),
        "candidate": {
            "before": str(candidate_before_path),
            "after": str(candidate_after_path),
            "status": "pending",
            "source_drift": None,
        },
        "process": {
            "pid": None,
            "exit_code": None,
            "timed_out": False,
            "start_new_session": True,
            "termination": {"term_sent": False, "kill_sent": False},
        },
        "output": {
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "stdout_sha256": None,
            "stderr_sha256": None,
        },
        "result_paths": _requested_result_records(validated_result_paths),
    }
    _write_json(run_path, initial_record, secrets_to_redact)

    raw_stdout = b""
    raw_stderr = b""
    process: subprocess.Popen[bytes] | None = None
    spawn_error: str | None = None
    execution_error: str | None = None
    interrupted: KeyboardInterrupt | None = None
    timed_out = False
    term_sent = False
    kill_sent = False
    cleanup_unresolved = False
    pipes_closed = False
    output_complete = True
    output_loss_reason: str | None = None
    interrupt_capture_expired = False

    def mark_output_loss(reason: str) -> None:
        nonlocal output_complete, output_loss_reason
        output_complete = False
        output_loss_reason = output_loss_reason or reason

    with _DeferredInterrupt() as deferred:
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=child_env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                start_new_session=True,
            )
            initial_record["process"]["pid"] = process.pid
            _write_json(run_path, initial_record, secrets_to_redact)
            raw_stdout, raw_stderr, timed_out, stopped = _communicate_until_interrupt(
                process, timeout_s, deferred
            )
            if stopped:
                raise KeyboardInterrupt
            if timed_out:
                timed_out = True
                term_sent = _send_group_signal(process.pid, signal.SIGTERM)
                after_term_stdout, after_term_stderr, grace_expired = _communicate(
                    process, terminate_grace_s
                )
                raw_stdout = _prefer_latest_output(raw_stdout, after_term_stdout)
                raw_stderr = _prefer_latest_output(raw_stderr, after_term_stderr)
                if grace_expired:
                    kill_sent = _send_group_signal(process.pid, signal.SIGKILL)
                    after_kill_stdout, after_kill_stderr, cleanup_unresolved = _communicate(
                        process, terminate_grace_s
                    )
                    raw_stdout = _prefer_latest_output(raw_stdout, after_kill_stdout)
                    raw_stderr = _prefer_latest_output(raw_stderr, after_kill_stderr)
                    if cleanup_unresolved:
                        mark_output_loss("pipes remained open after bounded process-group cleanup")
                        _close_process_pipes(process)
                        pipes_closed = True
        except OSError as exc:
            spawn_error = _redact_text(f"{type(exc).__name__}: {exc}", secrets_to_redact)
        except KeyboardInterrupt as exc:
            # Finish the terminal receipt before preserving the caller's interrupt.
            interrupted = exc
            if process is not None:
                try:
                    resumed_stdout, resumed_stderr, interrupt_capture_expired = _communicate(
                        process, terminate_grace_s
                    )
                    raw_stdout = _prefer_latest_output(raw_stdout, resumed_stdout)
                    raw_stderr = _prefer_latest_output(raw_stderr, resumed_stderr)
                except KeyboardInterrupt as repeated:
                    interrupted = repeated
                    mark_output_loss("repeated interrupt during bounded output collection")
        except Exception as exc:  # reason: retain unexpected subprocess errors as run evidence
            execution_error = _redact_text(f"{type(exc).__name__}: {exc}", secrets_to_redact)
            mark_output_loss("subprocess output collection raised an unexpected exception")
        finally:
            if process is not None and interrupted is not None:
                try:
                    if process.poll() is None:
                        term_sent = _send_group_signal(process.pid, signal.SIGTERM) or term_sent
                        if output_complete:
                            try:
                                final_stdout, final_stderr, final_expired = _communicate(
                                    process, terminate_grace_s
                                )
                                raw_stdout = _prefer_latest_output(raw_stdout, final_stdout)
                                raw_stderr = _prefer_latest_output(raw_stderr, final_stderr)
                            except KeyboardInterrupt as repeated:
                                interrupted = repeated
                                mark_output_loss(
                                    "repeated interrupt during post-termination collection"
                                )
                                final_expired = True
                            if final_expired:
                                kill_sent = (
                                    _send_group_signal(process.pid, signal.SIGKILL) or kill_sent
                                )
                                if output_complete:
                                    try:
                                        final_stdout, final_stderr, final_expired = _communicate(
                                            process, terminate_grace_s
                                        )
                                        raw_stdout = _prefer_latest_output(raw_stdout, final_stdout)
                                        raw_stderr = _prefer_latest_output(raw_stderr, final_stderr)
                                    except KeyboardInterrupt as repeated:
                                        interrupted = repeated
                                        mark_output_loss("repeated interrupt after bounded kill")
                                        final_expired = True
                                if final_expired:
                                    mark_output_loss(
                                        "pipes remained open after bounded process-group cleanup"
                                    )
                        try:
                            process.wait(timeout=terminate_grace_s)
                        except subprocess.TimeoutExpired:
                            kill_sent = _send_group_signal(process.pid, signal.SIGKILL) or kill_sent
                            try:
                                process.wait(timeout=terminate_grace_s)
                            except subprocess.TimeoutExpired:
                                cleanup_unresolved = True
                                mark_output_loss(
                                    "owned process did not exit within bounded cleanup"
                                )
                    elif interrupt_capture_expired:
                        mark_output_loss(
                            "output pipes remained open after the interrupted process exited"
                        )
                except KeyboardInterrupt as repeated:
                    interrupted = repeated
                    mark_output_loss("repeated interrupt during process cleanup")
                    if process.poll() is None:
                        kill_sent = _send_group_signal(process.pid, signal.SIGKILL) or kill_sent
                _close_process_pipes(process)
                pipes_closed = True
            elif process is not None and process.poll() is None:
                try:
                    # This is a final ownership guard for exceptional exits.  The
                    # child was created in its own session, so this cannot target
                    # the parent shell or an unrelated process group.
                    term_sent = _send_group_signal(process.pid, signal.SIGTERM) or term_sent
                    final_stdout, final_stderr, final_expired = _communicate(
                        process, terminate_grace_s
                    )
                    raw_stdout = _prefer_latest_output(raw_stdout, final_stdout)
                    raw_stderr = _prefer_latest_output(raw_stderr, final_stderr)
                    if final_expired:
                        kill_sent = _send_group_signal(process.pid, signal.SIGKILL) or kill_sent
                        final_stdout, final_stderr, final_expired = _communicate(
                            process, terminate_grace_s
                        )
                        raw_stdout = _prefer_latest_output(raw_stdout, final_stdout)
                        raw_stderr = _prefer_latest_output(raw_stderr, final_stderr)
                    cleanup_unresolved = cleanup_unresolved or final_expired
                    if final_expired:
                        _close_process_pipes(process)
                        pipes_closed = True
                        mark_output_loss("pipes remained open after bounded process-group cleanup")
                except Exception as exc:  # reason: finalization must still write the receipt whatever cleanup raises
                    execution_error = execution_error or _redact_text(
                        f"{type(exc).__name__}: {exc}", secrets_to_redact
                    )
                    cleanup_unresolved = True
                    mark_output_loss("subprocess cleanup raised an unexpected exception")
                    _close_process_pipes(process)
                    pipes_closed = True

    if interrupted is None and deferred.pending():
        interrupted = KeyboardInterrupt()

    stdout = _redact_bytes(raw_stdout, secrets_to_redact)
    stderr = _redact_bytes(raw_stderr, secrets_to_redact)
    stdout_path.write_bytes(stdout)
    stderr_path.write_bytes(stderr)
    stdout_path.chmod(0o600)
    stderr_path.chmod(0o600)

    candidate_after = _candidate_snapshot(candidate_root, artifacts, secrets_to_redact)
    _write_json(candidate_after_path, candidate_after, secrets_to_redact)
    candidate_status, source_drift = _candidate_status(candidate_before, candidate_after)
    if spawn_error is not None:
        outcome = "spawn_error"
    elif interrupted is not None:
        outcome = "interrupted"
    elif execution_error is not None:
        outcome = "execution_error"
    elif timed_out:
        outcome = "timeout"
    elif process is not None and process.returncode == 0:
        outcome = "completed"
    else:
        outcome = "failed"
    exit_code = process.returncode if process is not None else None
    final_record = {
        **initial_record,
        "outcome": outcome,
        "ended_at": _utc_now(),
        "run_dir": str(run_dir),
        "run_record": str(run_path),
        "spawn_error": spawn_error,
        "execution_error": execution_error,
        "interrupted": interrupted is not None,
        "process": {
            "pid": process.pid if process is not None else None,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "start_new_session": True,
            "termination": {
                "term_sent": term_sent,
                "kill_sent": kill_sent,
                "cleanup_unresolved": cleanup_unresolved,
                "pipes_closed": pipes_closed,
            },
        },
        "candidate": {
            "before": str(candidate_before_path),
            "after": str(candidate_after_path),
            "status": candidate_status,
            "source_drift": source_drift,
        },
        "output": {
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "stdout_sha256": _sha256(stdout),
            "stderr_sha256": _sha256(stderr),
            "stdout_bytes": len(stdout),
            "stderr_bytes": len(stderr),
            "complete": output_complete,
            "loss_reason": output_loss_reason,
        },
        "result_paths": _capture_result_paths(validated_result_paths, output_root),
    }
    _write_json(run_path, final_record, secrets_to_redact)
    if interrupted is not None:
        raise interrupted
    return cast(dict[str, Any], _redact_value(final_record, secrets_to_redact))


def main(argv: Sequence[str] | None = None) -> int:
    """CLI for non-secret explicit command runs.

    Command arguments follow ``--``.  Credentials cannot be supplied through
    this CLI; configure the environment before invocation and use the Python API
    when known credential values must be redacted from retained output.
    """

    parser = argparse.ArgumentParser(prog="run-recorder")
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--terminate-grace", type=float, default=DEFAULT_TERMINATE_GRACE_S)
    parser.add_argument("--env-name", action="append", default=[], dest="env_allowlist")
    parser.add_argument("--artifact", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv) if argv is not None else None)
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("an explicit command argv is required after --")
    artifacts: dict[str, str] = {}
    for item in args.artifact:
        name, separator, path = item.partition("=")
        if not separator or not name or not path:
            parser.error(f"artifact {item!r} must be NAME=PATH")
        artifacts[name] = path
    try:
        record = record_run(
            command,
            cwd=args.cwd,
            candidate_root=args.candidate_root,
            timeout_s=args.timeout,
            terminate_grace_s=args.terminate_grace,
            output_root=args.output_root,
            artifacts=artifacts,
            env_allowlist=args.env_allowlist,
        )
    except RunRecorderError as exc:
        print(f"run-recorder: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "run_dir": record["run_dir"],
                "outcome": record["outcome"],
                "exit_code": record["process"]["exit_code"],
                "candidate_status": record["candidate"]["status"],
                "acceptance_status": record["acceptance_status"],
            },
            sort_keys=True,
        )
    )
    if record["outcome"] == "timeout":
        return 124
    if isinstance(record["process"]["exit_code"], int):
        return record["process"]["exit_code"] if record["process"]["exit_code"] > 0 else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
