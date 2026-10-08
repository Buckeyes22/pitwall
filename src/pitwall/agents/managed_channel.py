"""Durable launcher and event waits for the parent-side channel tools.

The parent MCP server is a short-lived transport.  A managed dispatch therefore
has its own process and a small durable sidecar under ``launches/<dispatch-id>``.
The child still owns the run directory and executes through the normal CLI
dispatcher; this module only starts that dispatcher and observes its records.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .channel import (
    effective_deadline_s,
    epoch_of,
    load_channel_config,
    resolve_with_default,
    steer_refusal,
    write_steer_logged,
)
from .events import EventEmitter
from .hooks import HookRunner
from .mailbox import Mailbox, MailboxError, validate_steer_request
from .pids import _process_state as _process_state
from .pids import pid_alive as _pid_alive
from .pids import process_identity as _process_identity
from .run_store import (
    FILE_MODE,
    MANAGED_LAUNCH_ENV,
    TERMINAL_STATES,
    RunStore,
    atomic_write_json,
    ensure_private_directory,
    reconcile_run,
    state_root,
    utc_now,
)

DEFAULT_WAIT_SECONDS = 30.0
MAX_WAIT_SECONDS = 300.0
DEFAULT_DISPATCH_SECONDS = 1140.0
MAX_DISPATCH_SECONDS = 7 * 24 * 60 * 60.0
POLL_INTERVAL_SECONDS = 0.20
# Hang guard for the child-channel handshake probe, not a latency check. A healthy handshake costs
# about 0.08 s of CPU (about 0.08 s wall idle, about 1.1 s wall with 12 busy loops sharing one
# core, over 2 s with 30), so its wall time scales with machine load. A wrong or stale executable
# fails fast (it exits or answers wrongly); only a child that never answers reaches this bound, and
# 30 s is about 400x the healthy CPU cost while still refusing a hung child within one dispatch.
CHANNEL_PROBE_TIMEOUT_SECONDS = 30.0
ORPHAN_GRACE_SECONDS = 2.0
CHILD_CHANNEL_TOOLS = frozenset({"ask_orchestrator", "read_steering", "ack_steer"})
STEERABLE_STATES = {
    "created",
    "preflighting",
    "ready",
    "workspace_preparing",
    "workspace_ready",
    "running",
    "paused",
}


class ManagedChannelError(ValueError):
    """A managed channel request cannot be fulfilled."""


class WaitCancelled(Exception):
    """The MCP request ended while its managed wait was still outstanding."""


@dataclass(frozen=True, slots=True)
class LaunchRequest:
    dispatch_id: str
    route: str | None
    harness: str | None
    prompt: str
    workspace: str = "shared"
    task_mode: str | None = None
    base: str | None = None
    timeout_seconds: float = DEFAULT_DISPATCH_SECONDS
    ask_support: bool = True
    max_asks: int | None = None
    retain_prompt: bool = False
    extra_args: tuple[str, ...] = ()


def require_dispatch_id(dispatch_id: object) -> str:
    """Require the full UUID used by a managed operation.

    ``find_run`` intentionally supports human-friendly prefixes for the CLI;
    parent tools must not inherit that ambiguity or let one session address a
    different run by accident.
    """

    if not isinstance(dispatch_id, str):
        raise ManagedChannelError("dispatch_id must be the full dispatch UUID")
    try:
        parsed = uuid.UUID(dispatch_id)
    except (ValueError, AttributeError) as exc:
        raise ManagedChannelError("dispatch_id must be the full dispatch UUID") from exc
    if str(parsed) != dispatch_id.lower() or len(dispatch_id) != 36:
        raise ManagedChannelError("dispatch_id must be the full dispatch UUID")
    return str(parsed)


def _run_path(env: Mapping[str, str], dispatch_id: str) -> Path:
    return state_root(env) / "runs" / require_dispatch_id(dispatch_id)


def _launch_path(env: Mapping[str, str], dispatch_id: str) -> Path:
    return state_root(env) / "launches" / require_dispatch_id(dispatch_id)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, UnicodeDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _write_file(path: Path, content: bytes) -> None:
    ensure_private_directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        os.fchmod(descriptor, FILE_MODE)
    except OSError:
        # fchmod failed before fdopen took ownership of the descriptor, so
        # this path must still close it itself.
        os.close(descriptor)
        raise
    # os.fdopen takes ownership of the descriptor from this point on; its
    # own close (via the ``with`` block) is the only one allowed to run.
    # Closing it again afterward risks closing an unrelated descriptor a
    # different thread has since reused the same number for.
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    with contextlib.suppress(OSError):
        path.chmod(FILE_MODE)


def _record_launch_error(launch_dir: Path, dispatch_id: str, error: BaseException) -> None:
    """Best-effort durable marker for a launch that never became runnable."""

    # The original launch error is still returned to the caller. There is
    # no safe place to persist a second failure if the sidecar itself is
    # unavailable.
    with contextlib.suppress(OSError):
        atomic_write_json(
            launch_dir / "launch_error.json",
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "error": str(error),
                "recordedAt": utc_now(),
            },
        )


def _clear_staged_prompt(launch_dir: Path) -> None:
    """Remove the pre-run prompt unless the caller explicitly retained it."""

    request = _read_json(launch_dir / "request.json")
    if request and request.get("retainPrompt") is True:
        return
    with contextlib.suppress(OSError):
        (launch_dir / "prompt.md").unlink()


def _execution_argv(env: Mapping[str, str]) -> list[str]:
    """Resolve a recursive Agent Routing command without a shell.

    ``child_execution`` is authoritative for the handoff.
    """

    from .execution import ExecutionError, child_execution

    try:
        return list(child_execution(env).argv)
    except ExecutionError:
        invoked = Path(sys.argv[0])
        if invoked.is_absolute() and invoked.is_file() and os.access(invoked, os.X_OK):
            return [os.path.abspath(sys.executable), str(invoked)]
        return [os.path.abspath(sys.executable), "-m", "pitwall.agents"]


def _launcher_record(path: Path) -> dict[str, Any] | None:
    return _read_json(path / "launcher.json")


def _registered_channel_argv(harness: str, env: Mapping[str, str], home: Path) -> list[str]:
    """Resolve the configured channel command without invoking a shell."""

    from .capability_inventory import channel_entry_disabled
    from .mcp_registration import CHANNEL_ARGS, CHANNEL_COMMAND, current_entry

    entry = current_entry(harness, env, home)
    if not isinstance(entry, Mapping):
        raise ManagedChannelError(f"{harness!r} has no registered pitwall-channel MCP entry")
    if channel_entry_disabled(entry):
        raise ManagedChannelError(
            f"{harness!r} has its pitwall-channel MCP entry disabled; enable it in the harness's "
            "MCP configuration or re-run setup mcp"
        )
    raw_command = entry.get("command")
    raw_args = entry.get("args", [])
    if isinstance(raw_command, list):
        # OpenCode stores the command and its ``mcp serve channel`` arguments in one argv list.
        command_parts = raw_command
        if raw_args not in (None, []):
            raise ManagedChannelError(f"{harness!r} has an invalid pitwall-channel command entry")
    else:
        command_parts = [raw_command, *raw_args] if isinstance(raw_args, list) else []
    if not command_parts or not isinstance(command_parts[0], str) or not command_parts[0]:
        raise ManagedChannelError(f"{harness!r} has an invalid pitwall-channel command entry")
    if not all(isinstance(value, str) and value for value in command_parts):
        raise ManagedChannelError(f"{harness!r} has an invalid pitwall-channel command entry")
    if command_parts[1:4] != list(CHANNEL_ARGS):
        raise ManagedChannelError(
            f"{harness!r} pitwall-channel entry does not launch the MCP server"
        )

    candidate = Path(command_parts[0]).expanduser()
    if not candidate.is_absolute():
        found = shutil.which(command_parts[0], path=env.get("PATH"))
        if found is None:
            raise ManagedChannelError(f"{harness!r} pitwall-channel command is not executable")
        candidate = Path(found)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ManagedChannelError(f"{harness!r} pitwall-channel command is not executable") from exc
    if (
        not resolved.is_file()
        or not os.access(resolved, os.X_OK)
        or resolved.name != CHANNEL_COMMAND
    ):
        raise ManagedChannelError(
            f"{harness!r} pitwall-channel entry does not point to the pitwall executable"
        )
    return [str(resolved), *command_parts[1:]]


def verify_child_channel(harness: str, env: Mapping[str, str], home: Path) -> None:
    """Handshake with the registered server and require the child role tools.

    A config entry named ``pitwall-channel`` is only registration evidence. A
    short, tool-list-only handshake prevents a stale or unrelated executable
    (for example ``/bin/true``) from being treated as an interactive child.
    No harness request or child tool call is made here.
    """

    argv = _registered_channel_argv(harness, env, home)
    dispatch_id = str(uuid.uuid4())
    probe_env = dict(env)
    probe_env["PITWALL_AGENTS_CHANNEL_DISPATCH_ID"] = dispatch_id
    probe_env["PITWALL_AGENTS_CHANNEL_STATE_ROOT"] = str(state_root(env))
    probe_env["PITWALL_AGENTS_CHANNEL_ATTEMPT"] = "1"
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pitwall-managed-preflight", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    payload = (
        "\n".join(json.dumps(message, separators=(",", ":")) for message in messages) + "\n"
    ).encode()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=probe_env,
            close_fds=True,
        )
        stdout, _ = process.communicate(payload, timeout=CHANNEL_PROBE_TIMEOUT_SECONDS)
    except (OSError, subprocess.SubprocessError) as exc:
        if process is not None:
            process.kill()
            process.communicate()
        raise ManagedChannelError(f"{harness!r} child channel MCP handshake failed") from exc
    replies: dict[int, Mapping[str, Any]] = {}
    for line in stdout.splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError, UnicodeDecodeError:
            continue
        if (
            isinstance(message, Mapping)
            and isinstance(message.get("id"), int)
            and isinstance(message.get("result"), Mapping)
        ):
            replies[int(message["id"])] = message
    tools = replies.get(2, {}).get("result", {}).get("tools") if replies.get(2) else None
    names = (
        {
            str(tool.get("name"))
            for tool in tools
            if isinstance(tool, Mapping) and isinstance(tool.get("name"), str)
        }
        if isinstance(tools, list)
        else set()
    )
    if process.returncode != 0 or names != CHILD_CHANNEL_TOOLS:
        raise ManagedChannelError(
            f"{harness!r} child channel MCP handshake did not expose ask_orchestrator/read_steering/ack_steer"
        )


def _copy_launcher_record(env: Mapping[str, str], dispatch_id: str) -> bool:
    """Copy durable launcher metadata once the child has a durable request.

    Return whether a durable child request was observed.  The return value
    lets the launch watcher stop retaining the staged prompt as soon as the
    child has taken ownership of its request, even when no MCP waiter is
    attached.
    """

    sidecar = _launch_path(env, dispatch_id)
    record = _launcher_record(sidecar)
    run_dir = _run_path(env, dispatch_id)
    if record is None or not run_dir.is_dir():
        return False
    target = run_dir / "launcher.json"
    if not target.exists():
        with contextlib.suppress(OSError):
            atomic_write_json(target, record)
    # The staged prompt is needed only until the child has created its durable
    # request record. A run directory can briefly exist before request.json is
    # published, so do not remove the handoff input at that earlier boundary;
    # an explicit retention request is the sole exception after handoff.
    if not (run_dir / "request.json").is_file():
        return False
    request = _read_json(sidecar / "request.json")
    if not (request and request.get("retainPrompt") is True):
        with contextlib.suppress(OSError):
            (sidecar / "prompt.md").unlink()
    return True


def _record_launcher_exit(launch_dir: Path, dispatch_id: str, returncode: int) -> None:
    """Record a child exit without exposing child output through MCP."""

    # A terminal cleanup may remove the sidecar while this daemon monitor is
    # finishing its final wait. Never recreate an explicitly removed launch.
    if not launch_dir.is_dir():
        return
    with contextlib.suppress(OSError):
        atomic_write_json(
            launch_dir / "exit.json",
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "returncode": returncode,
                "recordedAt": utc_now(),
            },
        )


def _monitor_launcher(
    env: Mapping[str, str],
    dispatch_id: str,
    launch_dir: Path,
    process: subprocess.Popen[Any],
) -> None:
    """Reap one launcher and scrub its staged prompt independently of waits.

    The monitor is a daemon thread owned by the parent process, rather than by
    an individual MCP request.  It uses bounded ``wait`` calls so a cancelled
    or disconnected waiter never terminates the child, while a normal exit is
    still reaped promptly and cannot remain a zombie.  If the child has
    created its durable run, the staged prompt is removed during polling; if
    it exits before doing so, the prompt is scrubbed as an orphan.
    """

    while True:
        _copy_launcher_record(env, dispatch_id)
        try:
            returncode = process.wait(timeout=POLL_INTERVAL_SECONDS)
            break
        except subprocess.TimeoutExpired:
            continue
        except OSError, subprocess.SubprocessError:
            return
    _copy_launcher_record(env, dispatch_id)
    # This also handles an orphan whose process exited before creating a run;
    # retainPrompt remains an explicit opt-in through the sidecar request.
    _clear_staged_prompt(launch_dir)
    _record_launcher_exit(launch_dir, dispatch_id, int(returncode))


def start_dispatch(env: Mapping[str, str], request: LaunchRequest) -> dict[str, Any]:
    """Stage and launch one independent CLI dispatch.

    The sidecar is created before the process starts, while the child remains
    responsible for creating ``runs/<uuid>`` and all normal lifecycle records.
    """

    dispatch_id = require_dispatch_id(request.dispatch_id)
    if request.route is not None and (not isinstance(request.route, str) or not request.route):
        raise ManagedChannelError("route must be a non-empty string")
    if request.harness is not None and (
        not isinstance(request.harness, str) or not request.harness
    ):
        raise ManagedChannelError("provider must be a non-empty string")
    if not request.route and not request.harness:
        raise ManagedChannelError("route or provider is required")
    if request.route and request.harness:
        raise ManagedChannelError("route and provider are mutually exclusive")
    if not isinstance(request.prompt, str) or not request.prompt:
        raise ManagedChannelError("prompt must be a non-empty string")
    if not isinstance(request.workspace, str) or request.workspace not in {
        "shared",
        "isolated",
        "auto",
    }:
        raise ManagedChannelError("workspace must be shared, isolated, or auto")
    if request.task_mode is not None and (
        not isinstance(request.task_mode, str) or request.task_mode not in {"read", "write"}
    ):
        raise ManagedChannelError("task_mode must be read or write")
    if isinstance(request.timeout_seconds, bool):
        raise ManagedChannelError("timeout_seconds must be a number")
    try:
        timeout_seconds = float(request.timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ManagedChannelError("timeout_seconds must be a number") from exc
    if (
        not math.isfinite(timeout_seconds)
        or timeout_seconds <= 0
        or timeout_seconds > MAX_DISPATCH_SECONDS
    ):
        raise ManagedChannelError(f"timeout_seconds must be between 0 and {MAX_DISPATCH_SECONDS:g}")
    if not isinstance(request.ask_support, bool):
        raise ManagedChannelError("ask_support must be a boolean")
    if not request.ask_support:
        raise ManagedChannelError("managed dispatches require ask_support=true")
    if request.max_asks is not None and (
        isinstance(request.max_asks, bool)
        or not isinstance(request.max_asks, int)
        or request.max_asks < 1
        or request.max_asks > 50
    ):
        raise ManagedChannelError("max_asks must be between 1 and 50")
    if not isinstance(request.extra_args, (tuple, list)) or any(
        not isinstance(arg, str) for arg in request.extra_args
    ):
        raise ManagedChannelError("extra_args must contain only strings")
    if not isinstance(request.retain_prompt, bool):
        raise ManagedChannelError("retain_prompt must be a boolean")
    if request.base is not None and not isinstance(request.base, str):
        raise ManagedChannelError("base must be a string")
    if any(
        arg == "--routing-ask-support" or arg.startswith("--routing-") for arg in request.extra_args
    ):
        raise ManagedChannelError("extra_args cannot override managed routing options")

    launch_dir = _launch_path(env, dispatch_id)
    if launch_dir.exists():
        raise ManagedChannelError(f"dispatch {dispatch_id} already has a launch record")
    try:
        ensure_private_directory(launch_dir)
        prompt_path = launch_dir / "prompt.md"
        _write_file(prompt_path, request.prompt.encode("utf-8"))
        atomic_write_json(
            launch_dir / "request.json",
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "route": request.route,
                "provider": request.harness,
                "workspace": request.workspace,
                "taskMode": request.task_mode,
                "timeoutSeconds": timeout_seconds,
                "askSupport": request.ask_support,
                "maxAsks": request.max_asks,
                "retainPrompt": request.retain_prompt,
            },
        )
    except (OSError, TypeError, ValueError) as exc:
        if launch_dir.is_dir():
            _record_launch_error(launch_dir, dispatch_id, exc)
            _clear_staged_prompt(launch_dir)
        raise ManagedChannelError(f"cannot stage dispatch: {exc}") from exc

    target = request.route or request.harness
    assert target is not None
    command = [
        *_execution_argv(env),
        "dispatch",
        "route" if request.route else target,
        *([request.route, str(prompt_path)] if request.route else [str(prompt_path)]),
    ]
    if request.workspace:
        command.extend(["--routing-workspace", request.workspace])
    if request.task_mode:
        command.extend(["--routing-task-mode", request.task_mode])
    if request.base:
        command.extend(["--routing-base", request.base])
    if request.ask_support:
        command.append("--routing-ask-support")
    if request.max_asks is not None:
        command.append(f"--routing-max-asks={request.max_asks}")
    if request.retain_prompt:
        command.append("--routing-retain-prompt")
    command.extend(request.extra_args)

    child_env = dict(env)
    child_env["PITWALL_AGENTS_DISPATCH_ID"] = dispatch_id
    child_env[MANAGED_LAUNCH_ENV] = "1"
    child_env["PITWALL_AGENTS_TIMEOUT_SECS"] = str(timeout_seconds)
    if request.ask_support:
        child_env["PITWALL_AGENTS_ASK_SUPPORT"] = "1"
    stdout_path = launch_dir / "launcher.stdout.log"
    stderr_path = launch_dir / "launcher.stderr.log"
    stdout_fd: int | None = None
    stderr_fd: int | None = None
    try:
        stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        os.fchmod(stdout_fd, FILE_MODE)
        os.fchmod(stderr_fd, FILE_MODE)
        popen_options: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_fd,
            "stderr": stderr_fd,
            "env": child_env,
            "close_fds": True,
        }
        if os.name == "posix":
            # A separate process group survives MCP server shutdown while
            # avoiding setsid(), which can fail with EPERM on macOS hosts.
            popen_options["process_group"] = 0
        # argv list, no shell: the executable is resolved by pitwall itself, the subcommand is
        # fixed, and extra_args were validated as plain strings that cannot override options.
        # nosemgrep: python.django.security.injection.command.subprocess-injection.subprocess-injection
        process = subprocess.Popen(command, **popen_options)
    except (OSError, ValueError) as exc:
        _record_launch_error(launch_dir, dispatch_id, exc)
        _clear_staged_prompt(launch_dir)
        raise ManagedChannelError(f"cannot launch dispatch: {exc}") from exc
    finally:
        for descriptor in (stdout_fd, stderr_fd):
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)

    record = {
        "schemaVersion": 1,
        "dispatchId": dispatch_id,
        "pid": process.pid,
        "pidStartIdentity": _process_identity(process.pid),
        "startedAt": utc_now(),
        "promptPath": str(prompt_path),
    }
    try:
        atomic_write_json(launch_dir / "launcher.json", record)
    except OSError as exc:
        # Without launcher.json the child cannot be reattached or safely
        # distinguished from a stale PID. Ask the existing dispatcher to stop
        # and leave a durable error marker instead of leaking an untracked run.
        try:
            process.terminate()
            process.wait(timeout=2)
        except OSError, subprocess.SubprocessError:
            with contextlib.suppress(OSError):
                process.kill()
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                process.wait(timeout=2)
        _record_launch_error(launch_dir, dispatch_id, exc)
        _clear_staged_prompt(launch_dir)
        raise ManagedChannelError(f"cannot record managed launcher: {exc}") from exc
    # Keep the process handle alive until it is reaped, independently of the
    # MCP request that started the dispatch.  The watcher also observes the
    # durable run handoff so prompt.md does not depend on a waiter polling.
    threading.Thread(
        target=_monitor_launcher,
        args=(env, dispatch_id, launch_dir, process),
        name=f"pitwall-launch-reaper-{dispatch_id[:8]}",
        daemon=True,
    ).start()
    return {
        "dispatch_id": dispatch_id,
        "reattach_handle": dispatch_id,
        "launcher": {
            "pid": process.pid,
            "started_at": record["startedAt"],
        },
    }


def _run_record(
    env: Mapping[str, str], dispatch_id: str
) -> tuple[Path, dict[str, Any] | None, dict[str, Any] | None]:
    run_dir = _run_path(env, dispatch_id)
    if not run_dir.is_dir():
        return run_dir, None, None
    run_doc = _read_json(run_dir / "run.json")
    result = _read_json(run_dir / "result.json")
    _copy_launcher_record(env, dispatch_id)
    return run_dir, run_doc, result


def _ask_payload(store: RunStore, ask: Mapping[str, Any]) -> dict[str, Any]:
    config = load_channel_config(store.path)
    effective = effective_deadline_s(ask, config)
    created = epoch_of(ask.get("created_at")) or time.time()
    remaining = int(created + effective - time.time())
    options = list(ask.get("context", {}).get("options", []))
    payload = {
        "event": "ask",
        "type": "ask",
        "dispatch_id": store.dispatch_id,
        "ask_id": ask["ask_id"],
        "ask": {
            "ask_id": ask["ask_id"],
            "question": ask["question"],
            "blocked_on": ask["blocked_on"],
            "severity": ask.get("severity", "normal"),
            "options": options,
            "default": ask["default"],
            "default_rationale": ask.get("default_rationale"),
            "deadline_s": effective,
            "deadline_remaining_s": remaining,
            "created_at": ask.get("created_at"),
            "files_touched": ask.get("context", {}).get("files_touched", []),
        },
        "blocked_on": ask["blocked_on"],
        "severity": ask.get("severity", "normal"),
        "options": options,
        "default": ask["default"],
        "deadline_s": effective,
        "deadline_remaining_s": remaining,
        "default_rationale": ask.get("default_rationale"),
        "reattach_handle": store.dispatch_id,
    }
    return payload


def _ask_deadline_expired(
    ask: Mapping[str, Any], run_dir: Path, *, now: float | None = None
) -> bool:
    """Compare the effective deadline against a precise epoch timestamp."""

    created = epoch_of(ask.get("created_at"))
    if created is None:
        return False
    effective = effective_deadline_s(ask, load_channel_config(run_dir))
    return created + effective <= (time.time() if now is None else now)


def _terminal_payload(
    dispatch_id: str,
    result: Mapping[str, Any],
    *,
    launcher: Mapping[str, Any] | None = None,
    soft_denial_reason: object = None,
    artifacts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    receipt = dict(result)
    if artifacts is not None:
        # result.json stores paths as of writing; a migrated run has moved since.
        receipt["artifacts"] = dict(artifacts)
    payload: dict[str, Any] = {
        "event": "terminal",
        "type": "terminal",
        "dispatch_id": dispatch_id,
        "status": receipt.get("status"),
        "outcome": receipt.get("outcome"),
        "receipt": receipt,
        "artifacts": receipt.get("artifacts", {}),
        "reattach_handle": dispatch_id,
    }
    if isinstance(soft_denial_reason, str) and soft_denial_reason:
        payload["soft_denial_reason"] = soft_denial_reason
    if launcher is not None:
        payload["launcher"] = {
            key: launcher[key]
            for key in ("pid", "pidStartIdentity", "startedAt")
            if key in launcher
        }
    return payload


def _still_running(
    dispatch_id: str, *, reason: str = "wait_deadline", steer: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": "still_running",
        "type": "still_running",
        "dispatch_id": dispatch_id,
        "status": "running",
        "outcome": "in_progress",
        "receipt": {"dispatch_id": dispatch_id, "status": "running", "outcome": "in_progress"},
        "reattach_handle": dispatch_id,
        "reason": reason,
    }
    if steer is not None:
        payload["steer"] = dict(steer)
        payload["steer_acknowledged"] = bool(steer.get("acknowledged"))
    return payload


def _unacked_steer_ids(run_dir: Path, dispatch_id: str) -> list[str] | None:
    """Blocking steers still unacknowledged, or ``None`` when the run has no mailbox."""
    if not (run_dir / "mailbox").is_dir():
        return None
    return [steer["steer_id"] for steer in Mailbox(run_dir, dispatch_id).unacked_steers()]


def _run_artifacts(env: Mapping[str, str], dispatch_id: str) -> dict[str, str] | None:
    """The run directory's current artifact paths, or ``None`` when it does not exist."""
    if not _run_path(env, dispatch_id).is_dir():
        return None
    return RunStore(state_root(env), dispatch_id).artifact_summary()


def _orphan_payload(
    dispatch_id: str,
    *,
    reason: str,
    unacked_steer_ids: list[str] | None = None,
    artifacts: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    # Keep the event bounded and free of command lines or child output.
    payload: dict[str, Any] = {
        "event": "orphan",
        "type": "orphan",
        "dispatch_id": dispatch_id,
        "status": "error",
        "outcome": "orphaned",
        "error": reason,
        "receipt": {"dispatch_id": dispatch_id, "status": "error", "outcome": "orphaned"},
        "reattach_handle": dispatch_id,
    }
    if artifacts is not None:
        payload["artifacts"] = dict(artifacts)
        payload["receipt"]["artifacts"] = dict(artifacts)
    if unacked_steer_ids is not None:
        payload["unacked_steer_ids"] = unacked_steer_ids
    return payload


def _supervisor_is_authoritative(run_doc: Mapping[str, Any]) -> bool:
    """Whether run.json's own supervisor, not a launch sidecar, decides if the run is alive.

    True for a running (nonterminal, unpaused) attempt that recorded its supervisor pid.
    """
    state = run_doc.get("state")
    supervisor = run_doc.get("supervisor")
    pid = supervisor.get("pid") if isinstance(supervisor, dict) else None
    return (
        isinstance(state, str)
        and state not in TERMINAL_STATES
        and state != "paused"
        and isinstance(pid, int)
        and not isinstance(pid, bool)
    )


def _launcher_alive(env: Mapping[str, str], dispatch_id: str) -> bool:
    record = _launcher_record(_launch_path(env, dispatch_id))
    if not record:
        return False
    try:
        pid = int(record["pid"])
    except KeyError, TypeError, ValueError:
        return False
    identity = record.get("pidStartIdentity")
    return _pid_alive(pid, identity if isinstance(identity, str) else None)


def _write_orphan(env: Mapping[str, str], dispatch_id: str, payload: Mapping[str, Any]) -> None:
    launch_dir = _launch_path(env, dispatch_id)
    if launch_dir.is_dir() and not (launch_dir / "orphan.json").exists():
        with contextlib.suppress(OSError):
            atomic_write_json(launch_dir / "orphan.json", dict(payload))
    run_dir = _run_path(env, dispatch_id)
    if run_dir.is_dir() and not (run_dir / "orphan.json").exists():
        with contextlib.suppress(OSError):
            atomic_write_json(run_dir / "orphan.json", dict(payload))


def _cancel_requested(cancel: Any) -> bool:
    """Read cancellation without assuming a concrete Event implementation."""

    if cancel is None:
        return False
    is_set = getattr(cancel, "is_set", None)
    return bool(is_set()) if callable(is_set) else False


def _raise_if_cancelled(cancel: Any) -> None:
    if _cancel_requested(cancel):
        raise WaitCancelled()


DEFAULTS_REPORTED = "defaults_reported.json"


def _defaults_applied(env: Mapping[str, str], dispatch_id: str) -> list[dict[str, Any]]:
    run_dir = _run_path(env, dispatch_id)
    if not run_dir.is_dir():
        return []
    try:
        box = RunStore(state_root(env), dispatch_id).mailbox_if_present()
        answers = box.answers() if box is not None else []
    except MailboxError, OSError:
        return []
    return sorted(
        (
            {"ask_id": a["ask_id"], "choice": a["choice"], "answered_at": a.get("answered_at")}
            for a in answers
            if a.get("answered_by") == "default"
        ),
        key=lambda d: d["ask_id"],
    )


def _reported_defaults(env: Mapping[str, str], dispatch_id: str) -> set[str]:
    record = _read_json(_launch_path(env, dispatch_id) / DEFAULTS_REPORTED) or {}
    return {str(x) for x in record.get("askIds", [])}


def _record_reported_default(env: Mapping[str, str], dispatch_id: str, ask_id: str) -> None:
    launch_dir = _launch_path(env, dispatch_id)
    if not launch_dir.is_dir():
        return  # unmanaged runs keep defaults_applied only
    ids = sorted(_reported_defaults(env, dispatch_id) | {ask_id})
    atomic_write_json(launch_dir / DEFAULTS_REPORTED, {"schemaVersion": 1, "askIds": ids})


def _next_event(
    env: Mapping[str, str],
    dispatch_id: str,
    cancel: Any,
    progress: Callable[[str], None] | None = None,
    *,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    skip_ask_id: str | None = None,
    steer_id: str | None = None,
) -> dict[str, Any]:
    """Wait for the next durable ask, terminal receipt, or bounded deadline."""

    dispatch_id = require_dispatch_id(dispatch_id)
    if isinstance(wait_seconds, bool):
        raise ManagedChannelError("wait_seconds must be a number")
    try:
        wait_seconds = float(wait_seconds)
    except (TypeError, ValueError) as exc:
        raise ManagedChannelError("wait_seconds must be a number") from exc
    if not math.isfinite(wait_seconds) or wait_seconds <= 0 or wait_seconds > MAX_WAIT_SECONDS:
        raise ManagedChannelError(f"wait_seconds must be between 0 and {MAX_WAIT_SECONDS:g}")
    deadline = time.monotonic() + wait_seconds
    next_progress = time.monotonic() + 15.0
    launcher_dead_at: float | None = None
    while True:
        # Cancellation has priority over an already-visible ask or terminal
        # receipt.  This check is deliberately before any durable event read;
        # a cancelled MCP waiter must never consume an event just because it
        # happened to be present when the request ended.
        _raise_if_cancelled(cancel)
        run_dir, run_doc, result = _run_record(env, dispatch_id)
        _raise_if_cancelled(cancel)
        if steer_id is not None and _steer_acknowledged(run_dir, steer_id):
            _raise_if_cancelled(cancel)
            ack = _steer_ack(run_dir, steer_id)
            _raise_if_cancelled(cancel)
            return {
                "event": "steer_ack",
                "type": "steer_ack",
                "dispatch_id": dispatch_id,
                "steer_id": steer_id,
                "ack": ack,
                "steer_acknowledged": True,
                "reattach_handle": dispatch_id,
            }
        if result is not None:
            _raise_if_cancelled(cancel)
            # run.json was read before result.json; the supervisor writes run.json's
            # terminal state (with softDenialReason) first, so read it again now.
            terminal_run_doc = _read_json(run_dir / "run.json") or run_doc or {}
            payload = _terminal_payload(
                dispatch_id,
                result,
                launcher=_launcher_record(_launch_path(env, dispatch_id)),
                soft_denial_reason=terminal_run_doc.get("softDenialReason"),
                artifacts=RunStore(state_root(env), dispatch_id).artifact_summary(),
            )
            unacked = _unacked_steer_ids(run_dir, dispatch_id)
            if unacked is not None:
                payload["unacked_steer_ids"] = unacked
            if steer_id is not None:
                payload["steer_acknowledged"] = _steer_acknowledged(run_dir, steer_id)
            _raise_if_cancelled(cancel)
            return payload
        if run_doc is not None:
            reported = _reported_defaults(env, dispatch_id)
            for default in _defaults_applied(env, dispatch_id):
                if default["ask_id"] not in reported and default["ask_id"] != skip_ask_id:
                    _raise_if_cancelled(cancel)
                    _record_reported_default(env, dispatch_id, default["ask_id"])
                    return {
                        "event": "defaulted",
                        "type": "defaulted",
                        "dispatch_id": dispatch_id,
                        **default,
                        "reattach_handle": dispatch_id,
                    }
        if run_doc is not None:
            state = run_doc.get("state")
            try:
                if state not in TERMINAL_STATES:
                    store = RunStore(state_root(env), dispatch_id)
                    box = store.mailbox_if_present()
                    for ask in box.pending_asks() if box is not None else []:
                        if skip_ask_id is not None and ask["ask_id"] == skip_ask_id:
                            continue
                        # Use the precise deadline for suppression; the
                        # integer display field rounds down and can reach zero
                        # just before the actual deadline.
                        created = epoch_of(ask.get("created_at"))
                        effective = effective_deadline_s(ask, load_channel_config(run_dir))
                        if created is not None and created + effective <= time.time():
                            continue
                        payload = _ask_payload(store, ask)
                        if steer_id is not None:
                            payload["steer_acknowledged"] = _steer_acknowledged(run_dir, steer_id)
                        _raise_if_cancelled(cancel)
                        return payload
                    if steer_id is not None and _steer_acknowledged(run_dir, steer_id):
                        _raise_if_cancelled(cancel)
                        ack = _steer_ack(run_dir, steer_id)
                        _raise_if_cancelled(cancel)
                        return {
                            "event": "steer_ack",
                            "type": "steer_ack",
                            "dispatch_id": dispatch_id,
                            "steer_id": steer_id,
                            "ack": ack,
                            "steer_acknowledged": True,
                            "reattach_handle": dispatch_id,
                        }
            except MailboxError, OSError, TypeError, ValueError:
                # A concurrently published mailbox document is retried on the
                # next bounded observation rather than turning a transient read
                # into a false terminal event.
                pass
        now = time.monotonic()
        sidecar = _launch_path(env, dispatch_id).is_dir()
        if run_doc is not None and (not sidecar or _supervisor_is_authoritative(run_doc)):
            # A standalone dispatch has no launcher to watch, and a running managed one is
            # judged by the supervisor its run.json names for the current attempt (a stale
            # sidecar may name a first attempt's exited launcher, or hide a dead supervisor).
            # The wait ends early only when that supervisor is gone (run_store.reconcile_run
            # ends its harness and records the failure).
            reason = None
            with contextlib.suppress(OSError, ValueError):
                reason = reconcile_run(env, run_dir)
            if reason is None and run_doc.get("state") in TERMINAL_STATES and result is None:
                # The failure is already on record (here, by `runs list`, or by cleanup), or the
                # supervisor died after the terminal transition: answer at once with the reason
                # it kept. A live supervisor is still writing result.json, so keep waiting.
                abandoned = _read_json(run_dir / "abandoned.json") or {}
                supervisor = run_doc.get("supervisor")
                pid = supervisor.get("pid") if isinstance(supervisor, dict) else None
                identity = (
                    supervisor.get("pidStartIdentity") if isinstance(supervisor, dict) else None
                )
                gone = isinstance(pid, int) and not _pid_alive(
                    pid, identity if isinstance(identity, str) else None
                )
                if abandoned.get("reason") or gone:
                    reason = str(
                        abandoned.get("reason")
                        or f"run reached {run_doc.get('state')} without result.json"
                    )
            if reason is not None:
                if _read_json(run_dir / "result.json") is not None:
                    # The supervisor published its result after this poll's read and then
                    # exited; deliver the result on the next pass, never an orphan.
                    continue
                _raise_if_cancelled(cancel)
                orphan = _orphan_payload(
                    dispatch_id,
                    reason=reason,
                    unacked_steer_ids=_unacked_steer_ids(run_dir, dispatch_id),
                    artifacts=_run_artifacts(env, dispatch_id),
                )
                _write_orphan(env, dispatch_id, orphan)
                if sidecar:
                    _clear_staged_prompt(_launch_path(env, dispatch_id))
                return orphan
            launcher_alive = True
        else:
            launcher_alive = _launcher_alive(env, dispatch_id)
        if launcher_alive:
            launcher_dead_at = None
        else:
            launch_error = _read_json(_launch_path(env, dispatch_id) / "launch_error.json")
            reason = (
                str(launch_error.get("error"))
                if launch_error and launch_error.get("error")
                else "managed launcher exited without a terminal receipt"
            )
            # A dispatcher can exit a harness process a few milliseconds
            # before it publishes result.json. Give a run that was already
            # created a short, bounded finalization grace period; a launch
            # that never created a run remains an immediate orphan.
            if launch_error or run_doc is None:
                _raise_if_cancelled(cancel)
                orphan = _orphan_payload(
                    dispatch_id,
                    reason=reason,
                    unacked_steer_ids=_unacked_steer_ids(run_dir, dispatch_id),
                    artifacts=_run_artifacts(env, dispatch_id),
                )
                _write_orphan(env, dispatch_id, orphan)
                _clear_staged_prompt(_launch_path(env, dispatch_id))
                _raise_if_cancelled(cancel)
                return orphan
            if launcher_dead_at is None:
                launcher_dead_at = now
            elif now - launcher_dead_at >= ORPHAN_GRACE_SECONDS:
                _raise_if_cancelled(cancel)
                orphan = _orphan_payload(
                    dispatch_id,
                    reason=reason,
                    unacked_steer_ids=_unacked_steer_ids(run_dir, dispatch_id),
                    artifacts=_run_artifacts(env, dispatch_id),
                )
                _write_orphan(env, dispatch_id, orphan)
                _clear_staged_prompt(_launch_path(env, dispatch_id))
                _raise_if_cancelled(cancel)
                return orphan
        if now >= deadline:
            if run_doc is not None and run_doc.get("state") in TERMINAL_STATES:
                _raise_if_cancelled(cancel)
                orphan = _orphan_payload(
                    dispatch_id,
                    reason=f"run reached {run_doc.get('state')} without result.json",
                    unacked_steer_ids=_unacked_steer_ids(run_dir, dispatch_id),
                    artifacts=_run_artifacts(env, dispatch_id),
                )
                _write_orphan(env, dispatch_id, orphan)
                _clear_staged_prompt(_launch_path(env, dispatch_id))
                _raise_if_cancelled(cancel)
                return orphan
            steer = None
            if steer_id is not None:
                steer = {
                    "steer_id": steer_id,
                    "acknowledged": _steer_acknowledged(run_dir, steer_id),
                }
            _raise_if_cancelled(cancel)
            return _still_running(dispatch_id, steer=steer)
        if cancel is not None and cancel.wait(min(POLL_INTERVAL_SECONDS, max(0.0, deadline - now))):
            raise WaitCancelled()
        if progress is not None and time.monotonic() >= next_progress:
            progress(f"waiting for managed dispatch {dispatch_id}")
            next_progress = time.monotonic() + 15.0


def wait_for_event(
    env: Mapping[str, str],
    dispatch_id: str,
    cancel: Any,
    progress: Callable[[str], None] | None = None,
    *,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
    skip_ask_id: str | None = None,
    steer_id: str | None = None,
) -> dict[str, Any]:
    """Wait for the next durable event; every event lists the defaults applied so far."""

    event = _next_event(
        env,
        dispatch_id,
        cancel,
        progress,
        wait_seconds=wait_seconds,
        skip_ask_id=skip_ask_id,
        steer_id=steer_id,
    )
    event["defaults_applied"] = _defaults_applied(env, require_dispatch_id(dispatch_id))
    return event


def _steer_ack(run_dir: Path, steer_id: str) -> dict[str, Any] | None:
    try:
        box = RunStore(run_dir.parent.parent, run_dir.name).mailbox_if_present()
        for ack in box.acks() if box is not None else []:
            if ack.get("steer_id") == steer_id:
                return ack
    except MailboxError, OSError:
        pass
    return None


def _steer_acknowledged(run_dir: Path, steer_id: str) -> bool:
    return _steer_ack(run_dir, steer_id) is not None


def answer_once(
    env: Mapping[str, str],
    dispatch_id: str,
    ask_id: str,
    choice: str,
    *,
    note: str | None = None,
    answered_by: str = "orchestrator",
) -> dict[str, Any]:
    """Write an idempotent answer, rejecting a conflicting duplicate."""

    dispatch_id = require_dispatch_id(dispatch_id)
    if not isinstance(ask_id, str) or len(ask_id) != 4 or not ask_id.isdigit():
        raise ManagedChannelError("ask_id must be a four-digit sequence id")
    if not isinstance(choice, str) or not choice:
        raise ManagedChannelError("choice must be a non-empty string")
    if not isinstance(answered_by, str) or answered_by not in {"orchestrator", "operator"}:
        raise ManagedChannelError("answered_by must be orchestrator or operator")
    if note is not None and not isinstance(note, str):
        raise ManagedChannelError("note must be a string")
    run_dir = _run_path(env, dispatch_id)
    if not run_dir.is_dir():
        raise ManagedChannelError(f"run {dispatch_id} not found")
    store = RunStore(state_root(env), dispatch_id)
    box = store.mailbox()
    try:
        ask = box.get_ask(ask_id)
    except MailboxError as exc:
        raise ManagedChannelError(str(exc)) from exc
    existing = box.get_answer(ask_id)
    emitter = EventEmitter(
        store,
        harness="mcp",
        model="channel",
        callback=HookRunner(env),
    )
    if existing is not None:
        # Once a default is durably published it owns the ask. A late retry
        # must observe that answer even if it proposed a different choice;
        # ordinary explicit duplicates and conflicts retain their previous
        # idempotence semantics.
        if existing.get("answered_by") == "default":
            return existing
        if existing.get("choice") != choice:
            raise ManagedChannelError(
                f"ask {ask_id} already has a conflicting answer {existing.get('choice')!r}"
            )
        return existing
    # The parent endpoint is allowed to answer an ask only before its precise
    # effective deadline.  Resolve the validated default through the same
    # exclusive mailbox create used by the child so a concurrent writer cannot
    # replace a default that already won the race.
    if _ask_deadline_expired(ask, run_dir):
        try:
            answer = resolve_with_default(store, ask, emitter=emitter)
        except MailboxError as exc:
            raise ManagedChannelError(str(exc)) from exc
        if answer.get("answered_by") == "default" or answer.get("choice") == choice:
            return answer
        raise ManagedChannelError(
            f"ask {ask_id} already has a conflicting answer {answer.get('choice')!r}"
        )
    # Close the ordinary check/write window as much as possible.  The mailbox
    # create remains exclusive; if the child resolves the ask between this
    # check and write_answer, the collision path below observes the winner.
    if _ask_deadline_expired(ask, run_dir):
        try:
            answer = resolve_with_default(store, ask, emitter=emitter)
        except MailboxError as exc:
            raise ManagedChannelError(str(exc)) from exc
        if answer.get("answered_by") == "default" or answer.get("choice") == choice:
            return answer
        raise ManagedChannelError(
            f"ask {ask_id} already has a conflicting answer {answer.get('choice')!r}"
        )
    try:
        answer = box.write_answer(
            ask_id,
            choice=choice,
            answered_by=answered_by,
            note=note,
        )
    except MailboxError as exc:
        # A concurrent writer may have won the exclusive create.  Re-read and
        # apply the same idempotence rule before surfacing the error.
        existing = box.get_answer(ask_id)
        if existing is not None:
            if existing.get("answered_by") == "default" or existing.get("choice") == choice:
                return existing
            raise ManagedChannelError(
                f"ask {ask_id} already has a conflicting answer {existing.get('choice')!r}"
            ) from exc
        raise ManagedChannelError(str(exc)) from exc
    emitter.emit_ask_resolved(ask_id, str(answer["answered_by"]))
    del ask
    return answer


def steer_once(
    env: Mapping[str, str],
    dispatch_id: str,
    *,
    kind: str,
    message: str,
    requires_ack: bool = True,
    deadline_s: int = 300,
) -> dict[str, Any]:
    dispatch_id = require_dispatch_id(dispatch_id)
    try:
        validate_steer_request(
            dispatch_id,
            kind=kind,
            message=message,
            requires_ack=requires_ack,
            deadline_s=deadline_s,
        )
    except MailboxError as exc:
        raise ManagedChannelError(str(exc)) from exc
    run_dir = _run_path(env, dispatch_id)
    if not run_dir.is_dir():
        raise ManagedChannelError(f"run {dispatch_id} not found")
    run_doc = _read_json(run_dir / "run.json") or {}
    if run_doc.get("state") not in STEERABLE_STATES:
        raise ManagedChannelError(
            f"run {dispatch_id} is {run_doc.get('state')}; steering applies to a live or paused run"
        )
    refusal = steer_refusal(run_dir, kind, run_doc.get("state"))
    if refusal is not None:
        raise ManagedChannelError(refusal)
    has_channel = load_channel_config(run_dir) is not None
    store = RunStore(state_root(env), dispatch_id)
    try:
        steer = write_steer_logged(
            store,
            EventEmitter(store, harness="mcp", model="channel", callback=HookRunner(env)),
            kind=kind,
            message=message,
            requires_ack=requires_ack,
            deadline_s=deadline_s,
        )
    except MailboxError as exc:
        raise ManagedChannelError(str(exc)) from exc
    return steer if has_channel else {**steer, "delivery": "abort-at-deadline"}


__all__ = [
    "DEFAULT_DISPATCH_SECONDS",
    "DEFAULT_WAIT_SECONDS",
    "LaunchRequest",
    "MAX_DISPATCH_SECONDS",
    "MAX_WAIT_SECONDS",
    "ManagedChannelError",
    "WaitCancelled",
    "answer_once",
    "require_dispatch_id",
    "start_dispatch",
    "steer_once",
    "verify_child_channel",
    "wait_for_event",
]
