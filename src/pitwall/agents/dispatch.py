"""Shared dispatch state machine and legacy-shim compatibility surface."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .profiles_resolve import ResolvedProfile

import contextlib

from .channel import (
    CHANNEL_ATTEMPT_ENV,
    CHANNEL_DISPATCH_ENV,
    CHANNEL_STATE_ROOT_ENV,
    ChannelConfig,
    SteerWatcher,
    expire_overdue,
    ledger_fields,
    load_channel_config,
    parse_max_asks,
    write_channel_config,
)
from .errors import EX_NOPERM, UsageError
from .events import EventEmitter
from .harnesses import get_adapter
from .harnesses.base import ParsedRequest, parse_duration_seconds
from .hooks import HookRunner
from .mailbox import DEFAULT_MAX_ASKS, MAX_DEADLINE_S, derive_deadline_s
from .pids import process_identity
from .process import ProcessResult, run_process
from .result import validate_result
from .run_store import (
    MANAGED_LAUNCH_ENV,
    TERMINAL_STATES,
    RunStore,
    append_jsonl,
    find_run,
    ledger_path,
    state_root,
    utc_now,
)
from .workspace import (
    UsageConfigurationError,
    WorkspaceError,
    WorkspaceRequest,
    capture_changes,
    prepare_isolated_worktree,
    resolve_workspace,
)

TRANSITIONS = {
    "created": {"preflighting"},
    "preflighting": {"preflight_failed", "ready", "failed"},
    "ready": {"workspace_preparing", "running", "failed"},
    "workspace_preparing": {"workspace_ready", "failed"},
    "workspace_ready": {"running", "failed"},
    "running": {"succeeded", "failed", "timed_out", "cancelled", "paused"},
    "paused": {"preflighting", "running", "failed", "cancelled"},
}

#: A harness exits with this code when it pauses on a blocking question it
#: wrote to the run mailbox (tier-4 ask contract). 124 (timeout) and 130
#: (cancellation) are already reserved; 75 (EX_TEMPFAIL) was free.
PAUSED_EXIT_CODE = 75

#: Dispatcher-identity variables that must never reach a harness environment:
#: a harness that dispatches again would read them as its own identity and
#: collide with the parent's dispatch id (plan Decision 8).
_DISPATCHER_IDENTITY = (
    "PITWALL_AGENTS_DISPATCH_ID",
    "PITWALL_AGENTS_ATTEMPT",
    "PITWALL_AGENTS_WORKFLOW_ID",
    "PITWALL_AGENTS_TASK_ID",
    MANAGED_LAUNCH_ENV,
)


def _legacy_timestamp() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")


_ledger_path = ledger_path


def _ledger_record(
    env: Mapping[str, str],
    *,
    dispatch_id: str,
    harness: str,
    model: str,
    event: str,
    exit_code: int | None = None,
    wall_seconds: int | None = None,
    outcome: str | None = None,
    supervisor_timeout: bool | None = None,
    workspace: str = "shared",
    profile: str | None = None,
    route: str | None = None,
    revived: bool = False,
    ask_ids: list[str] | None = None,
    channel: Mapping[str, Any] | None = None,
    soft_denial_reason: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "ts": _legacy_timestamp(),
        "shim": harness,
        "model": model,
        "event": event,
        "source": "shim",
        "schema_version": 4,
        "dispatch_id": dispatch_id,
        "workflow_id": env.get("PITWALL_AGENTS_WORKFLOW_ID") or None,
        "task_id": env.get("PITWALL_AGENTS_TASK_ID") or None,
        "attempt": int(env.get("PITWALL_AGENTS_ATTEMPT", "1")),
        "workspace": workspace,
        "route": route,
    }
    if profile is not None:
        record["profile"] = profile
    if revived:
        record["revived"] = True
    if ask_ids is not None:
        record["askIds"] = list(ask_ids)
    if channel:
        record.update(channel)
    if soft_denial_reason is not None:
        record["softDenialReason"] = soft_denial_reason
    if exit_code is not None:
        record["exit"] = exit_code
    if wall_seconds is not None:
        record["wall_s"] = wall_seconds
    if outcome is not None:
        record["outcome"] = outcome
    if supervisor_timeout is not None:
        record["supervisor_timeout"] = supervisor_timeout
    with contextlib.suppress(OSError):
        append_jsonl(_ledger_path(env), record)
    # Returned even when the append fails: the receipt describes the transport,
    # not the ledger write.
    return record


def _receipts_enabled(env: Mapping[str, str]) -> bool:
    return env.get("SHIM_RESULT", "0") == "1"


def _emit_sentinel(
    exit_code: int,
    *,
    leading_newline: bool,
    receipt: dict[str, Any] | None = None,
) -> None:
    payload = b"\n" if leading_newline else b""
    if receipt is not None:
        encoded = json.dumps(receipt, ensure_ascii=False, separators=(",", ":"))
        payload += f"SHIM-RESULT {encoded}\n".encode()
    payload += f"SHIM-DONE exit={exit_code}\n".encode("ascii")
    sys.stdout.flush()  # text-layer output (the --help usage) must precede the sentinel
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _timeout_from_env(env: Mapping[str, str], harness_id: str) -> float:
    raw = env.get("PITWALL_AGENTS_TIMEOUT_SECS", "1140")
    try:
        return parse_duration_seconds(raw)
    except ValueError:
        raise UsageError(
            f"{harness_id}-shim: PITWALL_AGENTS_TIMEOUT_SECS={raw!r} is not a positive duration "
            "(use seconds, or a number with an s, m, h, or d suffix)"
        ) from None


def _read_prompt(source: str) -> bytes:
    if source == "-":
        return sys.stdin.buffer.read()
    return Path(source).read_bytes()


@dataclass(slots=True)
class RoutingOptions:
    retain_prompt: bool = False
    workspace: str = "shared"
    task_mode: str | None = None
    base: str | None = None
    ask_support: bool = False
    max_asks: int | None = None


@dataclass(slots=True, frozen=True)
class DispatchContext:
    dispatch_id: str
    workflow_id: str | None
    task_id: str | None
    attempt: int
    effort: str | None


def _dispatch_context(env: Mapping[str, str]) -> DispatchContext:
    raw_dispatch = env.get("PITWALL_AGENTS_DISPATCH_ID")
    dispatch_id = raw_dispatch or str(uuid.uuid4())
    try:
        uuid.UUID(dispatch_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise UsageError("PITWALL_AGENTS_DISPATCH_ID must be a UUID") from exc
    workflow_id = env.get("PITWALL_AGENTS_WORKFLOW_ID") or None
    task_id = env.get("PITWALL_AGENTS_TASK_ID") or None
    try:
        attempt = int(env.get("PITWALL_AGENTS_ATTEMPT", "1"))
    except ValueError as exc:
        raise UsageError("PITWALL_AGENTS_ATTEMPT must be a positive integer") from exc
    if attempt < 1:
        raise UsageError("PITWALL_AGENTS_ATTEMPT must be a positive integer")
    return DispatchContext(
        dispatch_id=dispatch_id,
        workflow_id=workflow_id,
        task_id=task_id,
        attempt=attempt,
        effort=env.get("PITWALL_AGENTS_EFFORT") or None,
    )


def _strip_routing_args(argv: list[str]) -> tuple[list[str], RoutingOptions]:
    options = RoutingOptions()
    forwarded: list[str] = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--routing-retain-prompt":
            options.retain_prompt = True
            index += 1
            continue
        if argument == "--routing-ask-support":
            options.ask_support = True
            index += 1
            continue
        matched = False
        for flag, field, choices in (
            ("--routing-workspace", "workspace", {"shared", "isolated", "auto"}),
            ("--routing-task-mode", "task_mode", {"read", "write"}),
            ("--routing-base", "base", None),
            ("--routing-max-asks", "max_asks", None),
        ):
            if argument == flag:
                if index + 1 >= len(argv):
                    raise UsageError(f"{flag} requires a value")
                value = argv[index + 1]
                index += 2
                matched = True
            elif argument.startswith(flag + "="):
                value = argument.split("=", 1)[1]
                index += 1
                matched = True
            else:
                continue
            if field == "max_asks":
                setattr(options, field, parse_max_asks(value, source=flag))
                break
            if not value or (choices is not None and value not in choices):
                expected = "|".join(sorted(choices)) if choices else "COMMIT"
                raise UsageError(f"{flag} expects {expected}")
            setattr(options, field, value)
            break
        if matched:
            continue
        if argument.startswith("--routing-"):
            raise UsageError(f"unknown routing option: {argument}")
        else:
            forwarded.append(argument)
        index += 1
    try:
        options.workspace = resolve_workspace(
            WorkspaceRequest(options.workspace, options.task_mode)
        )
    except UsageConfigurationError as exc:
        raise UsageError(str(exc)) from exc
    return forwarded, options


class Lifecycle:
    # Why dispatch rewrote a zero exit to 77; set before the terminal transition and
    # recorded in run.json (never in result.json, whose field set is fixed).
    soft_denial_reason: str | None = None
    # The running harness's process group and start identity, so the run store can end a
    # harness whose supervisor was killed (run_store.finalize_abandoned). Per attempt.
    harness_process: dict[str, Any] | None = None

    def __init__(
        self,
        store: RunStore,
        harness: str,
        model: str,
        emitter: EventEmitter,
        context: DispatchContext | None = None,
        created_data: dict[str, Any] | None = None,
    ) -> None:
        self.store = store
        self.harness = harness
        self.model = model
        self.context = context or DispatchContext(store.dispatch_id, None, None, 1, None)
        self.created_at = utc_now()
        self.state = "created"
        self.transitions = [{"state": "created", "timestamp": self.created_at}]
        self.supervisor = _supervisor_record()
        self._write()
        emitter.emit("dispatch.created", created_data or {})

    @classmethod
    def resume(
        cls,
        store: RunStore,
        harness: str,
        model: str,
        emitter: EventEmitter,
        context: DispatchContext,
    ) -> Lifecycle:
        """Continue a paused run instead of resetting its lifecycle.

        The stored ``createdAt`` and transition history survive; the new
        attempt is recorded in ``run.json`` and the state moves
        ``paused -> running``. Anything but a paused run is a caller bug.
        """
        self = cls.__new__(cls)
        self.store = store
        self.harness = harness
        self.model = model
        self.context = context
        try:
            previous = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
            self.created_at = str(previous.get("createdAt") or utc_now())
            transitions = previous.get("transitions")
            self.transitions = (
                list(transitions)
                if isinstance(transitions, list) and transitions
                else [{"state": "created", "timestamp": self.created_at}]
            )
            self.state = str(previous.get("state") or "paused")
        except OSError, json.JSONDecodeError, TypeError, ValueError:
            self.created_at = utc_now()
            self.state = "created"
            self.transitions = [{"state": "created", "timestamp": self.created_at}]
        if self.state != "paused":
            raise RuntimeError(f"cannot resume a run in state {self.state!r}")
        self.supervisor = _supervisor_record()
        self._write()
        emitter.emit("dispatch.resumed", {"attempt": context.attempt})
        return self

    def transition(self, state: str) -> None:
        if state not in TRANSITIONS.get(self.state, set()):
            raise RuntimeError(f"invalid dispatch transition {self.state} -> {state}")
        if self.state in TERMINAL_STATES:
            raise RuntimeError(f"terminal dispatch cannot transition from {self.state}")
        self.state = state
        self.transitions.append({"state": state, "timestamp": utc_now()})
        self._write()

    def _write(self) -> None:
        document: dict[str, Any] = {
            "schemaVersion": 1,
            "dispatchId": self.store.dispatch_id,
            "workflowId": self.context.workflow_id,
            "taskId": self.context.task_id,
            "attempt": self.context.attempt,
            "provider": self.harness,
            "model": self.model,
            "state": self.state,
            "createdAt": self.created_at,
            "transitions": self.transitions,
            # Lets another process tell a live run from one whose supervisor was
            # killed (run_store.abandoned_reason). A resumed attempt records its own.
            "supervisor": self.supervisor,
        }
        if self.soft_denial_reason is not None:
            document["softDenialReason"] = self.soft_denial_reason
        if self.harness_process is not None:
            document["harnessProcess"] = self.harness_process
        self.store.write_json("run.json", document)

    def record_harness(self, pid: int) -> None:
        """Record the harness started with *pid* (its own process-group leader) in run.json."""
        self.harness_process = {"pgid": pid, "pidStartIdentity": process_identity(pid)}
        self._write()


def _missing_binary_hint(harness_id: str) -> str:
    return (
        f"{harness_id}-shim: install it with `pitwall agents setup harnesses` (select {harness_id}), "
        "then check the setup with `pitwall agents doctor`"
    )


def _supervisor_record() -> dict[str, Any]:
    """This process's pid and start identity, computed once per lifecycle (it may spawn ``ps``)."""
    return {"pid": os.getpid(), "pidStartIdentity": process_identity(os.getpid())}


def _base_result(
    store: RunStore,
    lifecycle: Lifecycle,
    request: ParsedRequest,
    *,
    status: str,
    outcome: str,
    exit_code: int,
    started_at: str | None,
    wall_ms: int,
    signal_number: int | None,
    arguments: list[str] | None = None,
    workspace: dict[str, Any] | None = None,
    route: ResolvedProfile | None = None,
) -> dict[str, Any]:
    finished_at = utc_now()
    stdout_path = store.artifact("stdout.log")
    stderr_path = store.artifact("stderr.log")

    def digest(path: Path) -> dict[str, Any]:
        hasher = hashlib.sha256()
        byte_count = 0
        if path.is_file():
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    byte_count += len(chunk)
                    hasher.update(chunk)
        return {"bytes": byte_count, "sha256": hasher.hexdigest()}

    result = {
        "schemaVersion": 1,
        "dispatchId": store.dispatch_id,
        "workflowId": lifecycle.context.workflow_id,
        "taskId": lifecycle.context.task_id,
        "provider": lifecycle.harness,
        "model": request.model,
        "requestedModel": request.model,
        "effort": lifecycle.context.effort,
        "arguments": arguments or [],
        "providerVersion": None,
        "status": status,
        "outcome": outcome,
        "createdAt": lifecycle.created_at,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "wallMs": wall_ms,
        "exitCode": exit_code,
        "signal": signal_number,
        "timeout": {"seconds": None, "expired": status == "timed_out"},
        "sentinel": {"emitted": True, "exit": exit_code},
        "workspace": workspace
        or {"mode": "shared", "path": str(Path.cwd()), "baseSha": None, "finalSha": None},
        "output": {"stdout": digest(stdout_path), "stderr": digest(stderr_path)},
        "artifacts": store.artifact_summary(),
        "integration": {"status": "not_applied", "appliedAt": None, "target": None},
    }
    if route is not None:
        result["route"] = route.to_public_dict()
    return result


def _finish_early(
    *,
    store: RunStore,
    lifecycle: Lifecycle,
    emitter: EventEmitter,
    request: ParsedRequest,
    state: str,
    event: str,
    exit_code: int,
    outcome: str,
    leading_newline: bool,
    started_at: str | None = None,
    arguments: list[str] | None = None,
    workspace: dict[str, Any] | None = None,
    receipt: dict[str, Any] | None = None,
    route: ResolvedProfile | None = None,
) -> int:
    try:
        lifecycle.transition(state)
        result = _base_result(
            store,
            lifecycle,
            request,
            status=state,
            outcome=outcome,
            exit_code=exit_code,
            started_at=started_at,
            wall_ms=0,
            signal_number=None,
            arguments=arguments,
            workspace=workspace,
            route=route,
        )
        validate_result(result)
        store.write_json("result.json", result)
        unacked = SteerWatcher(store, emitter, threading.Event()).report_unacked_at_exit()
        data: dict[str, Any] = {"exitCode": exit_code, "outcome": outcome}
        if unacked:
            data["unackedSteerIds"] = unacked
        emitter.emit(event, data)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"{lifecycle.harness}-shim: cannot finalize run metadata: {exc}", file=sys.stderr)
    finally:
        _emit_sentinel(exit_code, leading_newline=leading_newline, receipt=receipt)
    return exit_code


def _finish_paused(
    *,
    env: dict[str, str],
    store: RunStore,
    lifecycle: Lifecycle,
    emitter: EventEmitter,
    harness_id: str,
    request: ParsedRequest,
    process_result: ProcessResult,
    wall_seconds: int,
    routing_workspace: str,
    profile: str | None,
    route_spec: str | None,
    route_revived: bool,
    receipts: bool,
    worktree_metadata: Any | None,
    workspace_result: dict[str, Any],
    reason: str = "question",
) -> int:
    """Finalize a tier-4 pause: exit 75 with an ask from this attempt, answered or not.

    A paused dispatch is not a failure and emits no result document (the
    result schema has no ``paused`` status in v1); the resumed run's final
    result covers the whole dispatch. The completion contract holds via the
    ``SHIM-DONE exit=75`` sentinel plus this recorded ``paused``
    classification.
    """
    pending_ids = [ask["ask_id"] for ask in store.mailbox().pending_asks()]
    pause_record = None
    try:
        pause_record = _ledger_record(
            env,
            dispatch_id=store.dispatch_id,
            harness=harness_id,
            model=request.model,
            event="paused",
            exit_code=process_result.exit_code,
            wall_seconds=wall_seconds,
            outcome="paused",
            workspace=routing_workspace,
            profile=profile,
            route=route_spec,
            revived=route_revived,
            ask_ids=pending_ids,
        )
        lifecycle.transition("paused")
        if (config := load_channel_config(store.path)) is not None:
            write_channel_config(
                store, replace(config, wall_seconds_prior=config.wall_seconds_prior + wall_seconds)
            )
        if worktree_metadata is not None:
            try:
                changes = capture_changes(env, store.dispatch_id, metadata=worktree_metadata)
                workspace_result["finalSha"] = changes.finalSha
            except WorkspaceError as exc:
                print(f"{harness_id}-shim: cannot capture isolated changes: {exc}", file=sys.stderr)
        store.write_json(
            "pause.json",
            {
                "schemaVersion": 1,
                "dispatchId": store.dispatch_id,
                "state": "paused",
                "exitCode": process_result.exit_code,
                "pendingAskIds": pending_ids,
                "reason": reason,
                "workspace": workspace_result,
            },
        )
        emitter.emit_dispatch_paused(
            {"askIds": pending_ids, "exitCode": process_result.exit_code, "reason": reason}
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"{harness_id}-shim: cannot finalize paused run metadata: {exc}", file=sys.stderr)
    finally:
        _emit_sentinel(
            process_result.exit_code,
            leading_newline=True,
            receipt=pause_record if receipts else None,
        )
    return process_result.exit_code


def tier4_prompt_suffix(
    mailbox_dir: Path, dispatch_id: str, *, max_asks: int, deadline_cap_s: int = MAX_DEADLINE_S
) -> str:
    """Tier-4 ask instructions appended to dispatches opted into ask support."""
    template = """
---

# Orchestrator channel (tier-4 ask contract)

Do not guess when a blocking decision is dangerous or irreversible. Instead,
ask the orchestrator and pause:

1. Write ONE file `mailbox/asks/NNNN.json` under `__MAILBOX_DIR__` (`NNNN` is the
   next zero-padded number; at most one open ask at a time, at most __MAX_ASKS__
   asks for this run). Shape:
   ```json
   {"version": 1, "dispatch_id": "__DISPATCH_ID__", "ask_id": "0001",
     "blocked_on": "choice | naming | file-selection | schema | destructive | spend",
     "question": "one precise question",
     "context": {"files_touched": ["path/…"], "options": [{"id": "a", "text": "…"}]},
     "default": "b", "deadline_s": 600, "severity": "normal | blocking"}
   ```
   Rules: `options` holds at most 8 `{id, text}` entries and carries file
   paths or option ids only, never diffs. `default` is mandatory (it may be
   `"abort"`) and must name one of the options. `deadline_s` is at most __DEADLINE_CAP__;
   if the deadline passes, your stated default is applied by the orchestrator
   when the run is resumed. The run stays paused until then.
2. Exit 75 immediately without further tool calls. The run pauses; you are
   resumed later with the answer appended to this prompt.
3. Before destructive actions, check `__MAILBOX_DIR__/steer/` for orchestrator
   notes left while you ran.
"""
    return (
        template.replace("__MAILBOX_DIR__", str(mailbox_dir))
        .replace("__MAX_ASKS__", str(max_asks))
        .replace("__DEADLINE_CAP__", str(deadline_cap_s))
        .replace("__DISPATCH_ID__", dispatch_id)
    )


def tier1_prompt_block(*, max_asks: int) -> str:
    """Tier-1 instructions for harnesses with the pitwall-channel MCP server registered."""
    return f"""
---

# Orchestrator channel (tier-1 ask tool)

When a blocking decision is dangerous or irreversible, call the MCP tool
`ask_orchestrator` (server `pitwall-channel`) instead of guessing. Pass one precise
`question`, its `blocked_on` class, 2-8 `options` as {{id, text}}, a real `default`
with a `default_rationale`, and `files_touched` (paths only, never diffs). The call
blocks until the orchestrator answers or the deadline passes, then returns the
answer or your default. If the call fails with a timeout, call it again with the
same question: it resumes waiting on the same open ask. One open ask at a time, at
most {max_asks} asks for this run. Never ask anything the worktree can answer.

At natural boundaries (before a risky tool call, after each milestone) call
`read_steering` and acknowledge each directive with `ack_steer`. A `stop` directive
means: wrap up, report what you completed, and exit normally.

If `ask_orchestrator` is not available in this session, use the file contract below.
"""


def build_resume_prompt(
    original: bytes,
    resolved: list[tuple[dict[str, Any], dict[str, Any]]],
    attempt: int,
    steers: Sequence[Mapping[str, Any]] = (),
) -> bytes:
    """Rebuild a paused run's prompt: original plus answered Q&A in ask order."""
    lines = [
        "---",
        "",
        f"# Clarifications (resume attempt {attempt})",
        "",
        "The orchestrator answered your blocking questions. Continue the original",
        "task from the retained worktree state; do not redo completed work.",
        "",
    ]
    for ask, answer in resolved:
        options = ask.get("context", {}).get("options", [])
        option_lines = "\n".join(f"- {o.get('id')}: {o.get('text')}" for o in options)
        lines.append(f"## Q (ask {ask['ask_id']}, blocked_on={ask['blocked_on']})")
        lines.append(str(ask["question"]))
        if option_lines:
            lines.append("Options:")
            lines.append(option_lines)
        lines.append(f"Your default was: {ask['default']}")
        lines.append(f"## A (by {answer['answered_by']} at {answer['answered_at']})")
        lines.append(f"Choice: {answer['choice']}")
        if answer["choice"] == "abort":
            lines.append(
                "The orchestrator chose to abort: stop now, report what you completed, and exit 0."
            )
        if answer.get("note"):
            lines.append(f"Note: {answer['note']}")
        lines.append("")
    if steers:
        lines.append("# Orchestrator steering (delivered on resume)")
        lines.append("")
        for steer in steers:
            lines.append(f"- [{steer['kind']} {steer['steer_id']}] {steer['message']}")
        lines.append("")
        lines.append("Apply these directives before continuing.")
        lines.append("")
    appendix = "\n".join(lines).encode("utf-8", errors="replace")
    return original.rstrip(b"\n") + b"\n\n" + appendix


def dispatch_legacy(
    harness_id: str,
    argv: list[str],
    *,
    environ: Mapping[str, str] | None = None,
    route: ResolvedProfile | None = None,
) -> int:
    """Supervisor entry point: SIGTERM/SIGHUP request a graceful abort instead of killing the supervisor."""
    abort = threading.Event()
    abort_reasons: list[str] = []

    def request_abort(signum: int, _frame: object) -> None:
        if not abort.is_set():
            abort_reasons.append(f"signal {signal.Signals(signum).name}")
        abort.set()

    previous: dict[Any, Any] = {}
    if threading.current_thread() is threading.main_thread():
        for signum in (signal.SIGTERM, signal.SIGHUP):
            previous[signum] = signal.signal(signum, request_abort)
    try:
        return _dispatch_legacy(
            harness_id,
            argv,
            environ=environ,
            route=route,
            abort=abort,
            abort_reasons=abort_reasons,
        )
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


_OUTPUT_TAIL_BYTES = 65536


def _stdout_is_blank(stdout_bytes: int, stdout_tail: bytes) -> bool:
    """True when the child wrote nothing, or only whitespace, to stdout.

    ``stdout_tail`` is the last ``_OUTPUT_TAIL_BYTES`` of the stdout artifact; a larger
    stdout is never blank, and an unreadable artifact never counts as blank.
    """
    if stdout_bytes == 0:
        return True
    return (
        stdout_bytes <= _OUTPUT_TAIL_BYTES
        and len(stdout_tail) == stdout_bytes
        and not stdout_tail.strip()
    )


def _dispatch_legacy(
    harness_id: str,
    argv: list[str],
    *,
    environ: Mapping[str, str] | None = None,
    route: ResolvedProfile | None = None,
    abort: Any,
    abort_reasons: list[str],
) -> int:
    run = _LegacyDispatch(harness_id, argv, environ, route, abort, abort_reasons)
    for step in (
        run.parse_request,
        run.open_run,
        run.check_binary,
        run.load_prompt,
        run.deliver_prompt,
        run.prepare_workspace,
    ):
        exit_code = step()
        if exit_code is not None:
            return exit_code
    run.launch_child()
    run.apply_soft_denial()
    return run.finish()


class _LegacyDispatch:
    """One harness dispatch, run as named steps over shared state.

    Each ``step`` method returns an exit code when the dispatch ended early and
    ``None`` when the next step should run.
    """

    def __init__(
        self,
        harness_id: str,
        argv: list[str],
        environ: Mapping[str, str] | None,
        route: ResolvedProfile | None,
        abort: Any,
        abort_reasons: list[str],
    ) -> None:
        self.harness_id = harness_id
        self.argv = argv
        self.route = route
        self.abort = abort
        self.abort_reasons = abort_reasons
        self.env = dict(os.environ if environ is None else environ)
        # Internal resume handoff (set by resume_legacy only): re-enter a paused
        # run with the same dispatch id and attempt+1. Popped here so child
        # harnesses never inherit it.
        self.resume_mode = self.env.pop("PITWALL_AGENTS_RESUME", "0") == "1"
        self.route_spec = route.spec if route is not None else None
        self.route_revived = route.revived if route is not None else False
        self.adapter = get_adapter(harness_id)
        self.home = Path(self.env.get("HOME", "~")).expanduser()
        self.receipts = _receipts_enabled(self.env)
        self.ledger_started = False
        self.ledger_started_monotonic = time.monotonic()
        self.worktree_metadata: Any = None
        self.workspace_result: dict[str, Any] = {}
        self._terminal_record: dict[str, Any] | None = None
        # Why apply_soft_denial rewrote a zero exit to 77; None when it did not.
        self.soft_denial_reason: str | None = None

    # ---- steps ------------------------------------------------------------

    def parse_request(self) -> int | None:
        env = self.env
        try:
            self.context = _dispatch_context(env)
            forwarded, self.routing = _strip_routing_args(self.argv)
            if self.routing.max_asks is None and env.get("PITWALL_AGENTS_MAX_ASKS"):
                self.routing.max_asks = parse_max_asks(
                    env["PITWALL_AGENTS_MAX_ASKS"], source="PITWALL_AGENTS_MAX_ASKS"
                )
            if forwarded[:1] in (["-h"], ["--help"]):
                print(self.adapter.usage())
                _emit_sentinel(0, leading_newline=False)
                return 0
            self.request = self.adapter.parse(forwarded, env, self.home)
            for label, value, hint in (
                (
                    "prompt source",
                    self.request.source,
                    "pass the prompt file (or - for stdin) where the usage below shows "
                    "<prompt-source>, and a file whose name begins with - as ./-name",
                ),
                (
                    "model",
                    self.request.model,
                    "pass the model where the usage below shows it",
                ),
            ):
                if value != "-" and value.startswith("-"):
                    raise UsageError(
                        f"{self.harness_id}-shim: {value!r} is a flag, not a {label}; {hint}\n"
                        f"{self.adapter.usage()}"
                    )
            if self.context.effort is None:
                self.context = replace(self.context, effort=self.request.adapter_data.get("effort"))
            self.timeout_seconds = _timeout_from_env(env, self.harness_id)
        except UsageError as exc:
            print(str(exc), file=sys.stderr)
            _emit_sentinel(64, leading_newline=False)
            return 64
        return None

    def open_run(self) -> int | None:
        env, harness_id = self.env, self.harness_id
        self.dispatch_id = self.context.dispatch_id
        existing = RunStore(state_root(env), self.dispatch_id).path
        if existing.exists() and not self.resume_mode:
            print(
                f"{harness_id}-shim: dispatch ID already exists: {self.dispatch_id}",
                file=sys.stderr,
            )
            _emit_sentinel(64, leading_newline=False)
            return 64
        self.store = RunStore.create(env, self.dispatch_id)
        if self.resume_mode:
            self.store.rotate_attempt_logs(self.context.attempt - 1)
        self.store.touch_artifact("stdout.log")
        self.store.touch_artifact("stderr.log")
        self.emitter = EventEmitter(
            self.store,
            harness=harness_id,
            model=self.request.model,
            workflow_id=self.context.workflow_id,
            task_id=self.context.task_id,
            callback=HookRunner(env),
        )
        try:
            self.lifecycle = (
                Lifecycle.resume(
                    self.store, harness_id, self.request.model, self.emitter, self.context
                )
                if self.resume_mode
                else Lifecycle(
                    self.store,
                    harness_id,
                    self.request.model,
                    self.emitter,
                    self.context,
                    created_data={"route": self.route.to_public_dict()}
                    if self.route is not None
                    else None,
                )
            )
        except RuntimeError as exc:
            print(f"{harness_id}-shim: {exc}", file=sys.stderr)
            _emit_sentinel(1, leading_newline=False)
            return 1
        self.lifecycle.transition("preflighting")
        self.emitter.emit("dispatch.preflight_started")
        return None

    def _ledger(self, event: str, **fields: Any) -> dict[str, Any]:
        return _ledger_record(
            self.env,
            dispatch_id=self.dispatch_id,
            harness=self.harness_id,
            model=self.request.model,
            event=event,
            workspace=self.routing.workspace,
            route=self.route_spec,
            revived=self.route_revived,
            **fields,
        )

    def _finish_early(self, **fields: Any) -> int:
        # An early failure after deliver_prompt (e.g. workspace preparation) is terminal.
        if not self.routing.retain_prompt:
            self.store.remove_delivery_prompt()
        return _finish_early(
            store=self.store,
            lifecycle=self.lifecycle,
            emitter=self.emitter,
            request=self.request,
            leading_newline=False,
            route=self.route,
            **fields,
        )

    def check_binary(self) -> int | None:
        adapter, env = self.adapter, self.env
        self.binary = adapter.resolve_binary(env, self.home)
        if adapter.preflight_binary and self.binary is None:
            print(adapter.missing_binary_message(), file=sys.stderr)
            print(_missing_binary_hint(self.harness_id), file=sys.stderr)
            terminal_record = None
            if adapter.missing_binary_ledger == "finished":
                terminal_record = self._ledger(
                    "finished",
                    profile=adapter.policy_profile(env),
                    exit_code=127,
                    wall_seconds=0,
                    outcome="error",
                )
            self.store.record_request(
                self.request.source, None, retain_prompt=False, error="harness binary unavailable"
            )
            return self._finish_early(
                state="preflight_failed",
                event="dispatch.preflight_failed",
                exit_code=127,
                outcome="error",
                workspace={
                    "mode": self.routing.workspace,
                    "path": str(Path.cwd()),
                    "baseSha": None,
                    "finalSha": None,
                },
                receipt=terminal_record if self.receipts else None,
            )
        assert self.binary is not None
        self.preflight_data = adapter.preflight(self.request, self.binary, env)
        self.profile = adapter.policy_profile(env, self.preflight_data)
        self.lifecycle.transition("ready")
        self.emitter.emit("dispatch.preflight_succeeded")
        if adapter.start_ledger_before_prompt:
            self._ledger("started", profile=self.profile)
            self.ledger_started = True
        return None

    def load_prompt(self) -> int | None:
        request, harness_id = self.request, self.harness_id
        try:
            self.prompt = _read_prompt(request.source)
        except OSError:
            print(f"{harness_id}-shim: cannot read {request.source}", file=sys.stderr)
            self.store.record_request(
                request.source, None, retain_prompt=False, error="unreadable prompt source"
            )
            return self._fail_before_start(66)
        self.ask_support = (
            self.routing.ask_support or self.env.get("PITWALL_AGENTS_ASK_SUPPORT") == "1"
        )
        self.previous_channel = load_channel_config(self.store.path)
        self.max_asks = self.routing.max_asks or (
            self.previous_channel.max_asks if self.previous_channel else DEFAULT_MAX_ASKS
        )
        # Registered-harness detection loads the capability inventory; ordinary
        # dispatches must not pay that import cost.
        self.channel_tier = "4"
        if self.ask_support:
            from .capability_inventory import mcp_channel_registered

            self.channel_tier = (
                "1" if mcp_channel_registered(harness_id, self.env, self.home) else "4"
            )
        if self.ask_support:
            # Recorded now, not at launch, so a steer sent while the run is still
            # preparing already sees the channel; launch rewrites the epoch.
            self._write_channel_record()
        # A resumed run replays prompt.deliver.md, which already carries the
        # tier-4 suffix from its first attempt; appending again would stack one
        # duplicate contract block per resume.
        if self.ask_support and not self.resume_mode:
            suffix = tier4_prompt_suffix(
                self.store.path / "mailbox",
                self.dispatch_id,
                max_asks=self.max_asks,
                deadline_cap_s=derive_deadline_s(self.timeout_seconds),
            )
            if self.channel_tier == "1":
                suffix = tier1_prompt_block(max_asks=self.max_asks) + suffix
            self.prompt = self.prompt + suffix.encode("utf-8")
        try:
            self.adapter.check_prompt_size(self.prompt)
        except UsageError as exc:
            print(str(exc), file=sys.stderr)
            self.store.record_request(
                request.source,
                None,
                retain_prompt=False,
                error="prompt too large for argv delivery",
            )
            return self._fail_before_start(64)
        return None

    def _write_channel_record(self) -> None:
        write_channel_config(
            self.store,
            ChannelConfig(
                self.dispatch_id,
                max_asks=self.max_asks,
                timeout_seconds=self.timeout_seconds,
                attempt_started_epoch=time.time(),
                wall_seconds_prior=self.previous_channel.wall_seconds_prior
                if self.previous_channel
                else 0,
                tier=self.channel_tier,
            ),
        )

    def _fail_before_start(self, exit_code: int) -> int:
        terminal_record = self._ledger(
            "finished",
            profile=self.profile,
            exit_code=exit_code,
            wall_seconds=0,
            outcome="error",
        )
        return self._finish_early(
            state="failed",
            event="dispatch.failed",
            exit_code=exit_code,
            outcome="error",
            receipt=terminal_record if self.receipts else None,
        )

    def deliver_prompt(self) -> int | None:
        adapter, store, request = self.adapter, self.store, self.request
        store.record_request(
            request.source,
            self.prompt,
            retain_prompt=self.routing.retain_prompt,
            delivery="file" if adapter.prompt_delivery == "file" else "inline",
        )
        if adapter.prompt_delivery == "file":
            if self.ask_support or request.source == "-" or adapter.private_prompt_file:
                # Ask-support runs always execute the assembled prompt (original +
                # tier-4 suffix) from the retained delivery file so resume rebuilds
                # from exactly what the subagent saw. Adapters that take a private
                # prompt file always get their own mode-0600 copy in the run directory.
                prompt_file = store.write_delivery_prompt(self.prompt)
            else:
                prompt_file = Path(request.source)
            self.preflight_data = {**self.preflight_data, "promptFile": str(prompt_file)}
        elif self.ask_support:
            store.write_delivery_prompt(self.prompt)
        if self.ask_support:
            store.write_json(
                "resume.json",
                {
                    "schemaVersion": 1,
                    "dispatchId": self.dispatch_id,
                    "provider": self.harness_id,
                    "model": request.model,
                    "extraArgs": list(request.extra_args),
                    "workspace": self.routing.workspace,
                    "base": self.routing.base,
                    "askSupport": True,
                    "maxAsks": self.max_asks,
                    "retainPrompt": self.routing.retain_prompt,
                },
            )
        if not self.ledger_started:
            self.ledger_started_monotonic = time.monotonic()
            self._ledger("started", profile=self.profile)
        return None

    def prepare_workspace(self) -> int | None:
        routing, env = self.routing, self.env
        self.lifecycle.transition("workspace_preparing")
        self.emitter.emit("dispatch.workspace_preparing", {"mode": routing.workspace})
        self.workspace_path = Path.cwd()
        self.workspace_result = {
            "mode": "shared",
            "path": str(self.workspace_path),
            "baseSha": None,
            "finalSha": None,
        }
        try:
            if routing.workspace == "isolated":
                self.worktree_metadata = prepare_isolated_worktree(
                    env,
                    self.dispatch_id,
                    Path.cwd(),
                    base_ref=routing.base,
                )
                self.workspace_path = Path(self.worktree_metadata.path)
                self.workspace_result = {
                    "mode": "isolated",
                    "path": self.worktree_metadata.path,
                    "baseSha": self.worktree_metadata.baseSha,
                    "finalSha": self.worktree_metadata.baseSha,
                }
            self.lifecycle.transition("workspace_ready")
            self.emitter.emit(
                "dispatch.workspace_ready",
                {"mode": routing.workspace, "path": str(self.workspace_path)},
            )
        except WorkspaceError as exc:
            print(f"{self.harness_id}-shim: workspace preflight failed: {exc}", file=sys.stderr)
            terminal_record = self._ledger(
                "finished",
                profile=self.profile,
                exit_code=1,
                wall_seconds=0,
                outcome="error",
            )
            return self._finish_early(
                state="failed",
                event="dispatch.failed",
                exit_code=1,
                outcome="error",
                workspace={
                    "mode": routing.workspace,
                    "path": str(Path.cwd()),
                    "baseSha": None,
                    "finalSha": None,
                },
                receipt=terminal_record if self.receipts else None,
            )
        self.preflight_data = {**self.preflight_data, "workspacePath": str(self.workspace_path)}
        return None

    def launch_child(self) -> None:
        env, store = self.env, self.store
        harness_env = dict(env)
        for key in _DISPATCHER_IDENTITY:
            harness_env.pop(key, None)
        harness_env[CHANNEL_DISPATCH_ENV] = self.dispatch_id
        harness_env[CHANNEL_STATE_ROOT_ENV] = str(state_root(env))
        harness_env[CHANNEL_ATTEMPT_ENV] = str(self.context.attempt)
        if self.harness_id == "claude":
            harness_env.setdefault("MCP_TOOL_TIMEOUT", "3660000")
        assert self.binary is not None
        self.prepared = self.adapter.prepare(
            self.request, self.binary, self.prompt, harness_env, self.preflight_data
        )
        self.started_at = utc_now()
        self.lifecycle.transition("running")
        self.emitter.emit("dispatch.started", {"arguments": self.prepared.sanitized_args})
        if self.ask_support:
            self._write_channel_record()
        self.watcher = SteerWatcher(store, self.emitter, self.abort)
        self.grace = 10.0
        raw_grace = env.get("PITWALL_AGENTS_ABORT_GRACE_SECS")
        if raw_grace:
            try:
                parsed_grace = float(raw_grace)
                if parsed_grace > 0:
                    self.grace = parsed_grace
            except ValueError:
                pass
        try:
            if self.abort.is_set():
                # An abort signal arrived before the child started: skip the spawn
                # and finish the same way an in-flight abort would.
                self.process_result = ProcessResult(
                    143, int(signal.SIGTERM), False, False, 0, 0, 0, aborted=True
                )
            else:
                # A managed launcher already captures the supervisor's own streams
                # in launches/<id>/; copying child output there too doubled every byte.
                managed = env.get(MANAGED_LAUNCH_ENV) == "1"
                self.process_result = run_process(
                    self.prepared.argv,
                    env=self.prepared.env,
                    stdin=self.prepared.stdin,
                    stdout_path=store.artifact("stdout.log"),
                    stderr_path=store.artifact("stderr.log"),
                    timeout_seconds=self.timeout_seconds,
                    cwd=self.workspace_path,
                    output_callback=None,
                    abort_event=self.abort,
                    abort_grace_seconds=self.grace,
                    watch=self.watcher,
                    terminal_stdout_fd=None if managed else 1,
                    terminal_stderr_fd=None if managed else 2,
                    on_start=self.lifecycle.record_harness,
                )
        except OSError as exc:
            print(f"{self.harness_id}-shim: cannot execute {self.binary}: {exc}", file=sys.stderr)
            if isinstance(exc, FileNotFoundError):
                print(_missing_binary_hint(self.harness_id), file=sys.stderr)
            self.process_result = ProcessResult(127, None, False, False, 0, 0, 0)

    def apply_soft_denial(self) -> None:
        result = self.process_result
        if result.exit_code != 0 or result.timed_out or result.cancelled:
            return
        output_tails: list[bytes] = []
        for artifact_name in ("stderr.log", "stdout.log"):
            try:
                output_tails.append(
                    self.store.artifact(artifact_name).read_bytes()[-_OUTPUT_TAIL_BYTES:]
                )
            except OSError:
                output_tails.append(b"")
        soft_denial = next(
            (
                reason
                for output_tail in output_tails
                if (reason := self.adapter.detect_soft_denial(result.exit_code, output_tail))
                is not None
            ),
            None,
        )
        if (
            soft_denial is None
            and self.adapter.empty_stdout_is_failure
            and _stdout_is_blank(result.stdout_bytes, output_tails[1])
        ):
            soft_denial = "exited 0 without writing anything to stdout; recording exit 77"
        if soft_denial is not None:
            print(f"{self.harness_id}-shim: {soft_denial}", file=sys.stderr)
            self.soft_denial_reason = soft_denial
            self.process_result = replace(result, exit_code=EX_NOPERM)

    def _classify(self) -> tuple[str, str, str]:
        result = self.process_result
        if result.aborted:
            return "cancelled", "cancelled", "dispatch.cancelled"
        if result.timed_out:
            return "timed_out", "timeout", "dispatch.timed_out"
        if result.cancelled:
            return "cancelled", "cancelled", "dispatch.cancelled"
        if result.exit_code == 0:
            return "succeeded", "ok", "dispatch.succeeded"
        return "failed", "error", "dispatch.failed"

    def _pause_reason(self, state: str) -> str | None:
        """Why this attempt should pause instead of finishing, or None."""
        store, result, harness_id = self.store, self.process_result, self.harness_id
        has_mailbox = (store.path / "mailbox").is_dir()
        # Quarantine over-cap asks before counting what is pending: an attempt whose
        # only open ask is past max_asks must not pause on an empty ask list.
        capped: list[str] = []
        if state == "failed" and result.exit_code == PAUSED_EXIT_CODE and has_mailbox:
            capped = store.mailbox(max_asks=self.max_asks).enforce_cap()
        pending_asks = (
            state == "failed"
            and has_mailbox
            and bool(store.mailbox(max_asks=self.max_asks).pending_asks())
        )
        if capped and not pending_asks:
            print(
                f"{harness_id}-shim: ask cap exceeded: ask(s) {', '.join(capped)} were quarantined and no "
                "ask remains open; recording a failure instead of a pause",
                file=sys.stderr,
            )
        # Asks are answered while an attempt runs, so an answer can land before this
        # point. An attempt that asked and exited 75 paused either way; the resumed
        # attempt delivers the answer. Only an ask written during this attempt counts,
        # and not when the exit was for an ask past the cap, which stays a failure.
        answered_before_exit = (
            state == "failed"
            and result.exit_code == PAUSED_EXIT_CODE
            and not pending_asks
            and not capped
            and has_mailbox
            and any(
                str(ask.get("created_at", "")) >= self.started_at
                for ask in store.mailbox(max_asks=self.max_asks).asks()
            )
        )
        if (pending_asks or answered_before_exit) and result.exit_code == PAUSED_EXIT_CODE:
            return "question"
        if pending_asks and self.ask_support:
            print(
                f"{harness_id}-shim: attempt ended with exit {result.exit_code} while ask(s) "
                "were pending; pausing so `runs resume` can apply their defaults",
                file=sys.stderr,
            )
            return "orphaned-ask"
        if state == "failed" and result.exit_code == PAUSED_EXIT_CODE:
            print(
                f"{harness_id}-shim: exit 75 without a mailbox ask from this attempt; "
                "recording a failure (the pause contract requires an ask)",
                file=sys.stderr,
            )
        return None

    def finish(self) -> int:
        env, store, result = self.env, self.store, self.process_result
        wall_seconds = max(0, int(time.monotonic() - self.ledger_started_monotonic))
        for channel, byte_count in (
            ("stdout", result.stdout_bytes),
            ("stderr", result.stderr_bytes),
        ):
            if byte_count:
                self.emitter.emit("dispatch.output", {"channel": channel, "bytes": byte_count})
        state, outcome, terminal_event = self._classify()
        pause_reason = self._pause_reason(state)
        if pause_reason is not None:
            return _finish_paused(
                env=env,
                store=store,
                lifecycle=self.lifecycle,
                emitter=self.emitter,
                harness_id=self.harness_id,
                request=self.request,
                process_result=result,
                wall_seconds=wall_seconds,
                routing_workspace=self.routing.workspace,
                profile=self.profile,
                route_spec=self.route_spec,
                route_revived=self.route_revived,
                receipts=self.receipts,
                worktree_metadata=self.worktree_metadata,
                workspace_result=self.workspace_result,
                reason=pause_reason,
            )
        try:
            self._write_terminal(state, outcome, terminal_event, wall_seconds)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            print(f"{self.harness_id}-shim: cannot finalize run metadata: {exc}", file=sys.stderr)
        finally:
            _emit_sentinel(
                result.exit_code,
                leading_newline=True,
                receipt=self._terminal_record if self.receipts else None,
            )
        return result.exit_code

    def _write_terminal(
        self, state: str, outcome: str, terminal_event: str, wall_seconds: int
    ) -> None:
        env, store, result = self.env, self.store, self.process_result
        channel_fields: dict[str, Any] | None = None
        if (store.path / "mailbox").is_dir():
            config = load_channel_config(store.path)
            prior = config.wall_seconds_prior if config is not None else 0
            channel_fields = ledger_fields(store, wall_seconds_total=prior + wall_seconds)
        abort_reason = self.watcher.reason or (
            self.abort_reasons[0] if self.abort_reasons else None
        )
        if result.aborted:
            ledger_outcome = "killed" if result.killed else "cancelled"
            if channel_fields is None:
                channel_fields = {}
            if abort_reason:
                channel_fields["abortReason"] = abort_reason
        else:
            ledger_outcome = (
                "timeout" if result.exit_code == 124 else "ok" if result.exit_code == 0 else "error"
            )
        self._terminal_record = self._ledger(
            "finished",
            profile=self.profile,
            exit_code=result.exit_code,
            wall_seconds=wall_seconds,
            outcome=ledger_outcome,
            supervisor_timeout=result.timed_out,
            channel=channel_fields,
            soft_denial_reason=self.soft_denial_reason,
        )
        self.lifecycle.soft_denial_reason = self.soft_denial_reason
        self.lifecycle.transition(state)
        # The run is terminal from here on, so the delivery prompt goes even if a later step
        # fails. A paused run keeps it for `runs resume` (_finish_paused never gets here);
        # a finished run keeps it only on explicit request.
        try:
            if result.aborted:
                store.write_json(
                    "abort.json",
                    {
                        "schemaVersion": 1,
                        "reason": abort_reason,
                        "signal": result.signal,
                        "killed": result.killed,
                        "graceSeconds": self.grace,
                    },
                )
            if self.worktree_metadata is not None:
                try:
                    changes = capture_changes(
                        env, self.dispatch_id, metadata=self.worktree_metadata
                    )
                    self.workspace_result["finalSha"] = changes.finalSha
                except WorkspaceError as exc:
                    print(
                        f"{self.harness_id}-shim: cannot capture isolated changes: {exc}",
                        file=sys.stderr,
                    )
            document = _base_result(
                store,
                self.lifecycle,
                self.request,
                status=state,
                outcome=outcome,
                exit_code=result.exit_code,
                started_at=self.started_at,
                wall_ms=result.wall_ms,
                signal_number=result.signal,
                arguments=self.prepared.sanitized_args,
                workspace=self.workspace_result,
                route=self.route,
            )
            document["timeout"] = {"seconds": self.timeout_seconds, "expired": result.timed_out}
            validate_result(document)
            store.write_json("result.json", document)
            unacked = self.watcher.report_unacked_at_exit()
            terminal_data: dict[str, Any] = {"exitCode": result.exit_code, "outcome": outcome}
            if unacked:
                terminal_data["unackedSteerIds"] = unacked
            if self.soft_denial_reason is not None:
                terminal_data["softDenialReason"] = self.soft_denial_reason
            self.emitter.emit(terminal_event, terminal_data)
        finally:
            if not self.routing.retain_prompt:
                store.remove_delivery_prompt()


def resume_legacy(dispatch_id: str, *, environ: Mapping[str, str] | None = None) -> int:
    """Resume a paused tier-4 run with its answers appended.

    Reads the retained prompt (``prompt.deliver.md``, else the retained
    request ``prompt.md``), rebuilds it with the resolved Q&A appendix, and
    re-dispatches with the same ``PITWALL_AGENTS_DISPATCH_ID`` and
    ``ATTEMPT+1`` so worktree linkage and the ask cap survive. Fails closed
    when the run is not paused, when asks are still unresolved, when no
    prompt was retained (never re-dispatch a guessed prompt), or when the
    dispatch inputs needed for a faithful replay are missing.
    """
    env = dict(os.environ if environ is None else environ)
    try:
        run_path = find_run(env, dispatch_id)
    except FileNotFoundError as exc:
        print(f"pitwall agents: {exc}", file=sys.stderr)
        return 1
    full_id = run_path.name
    try:
        run_doc = json.loads((run_path / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"pitwall agents: cannot read run {full_id!r}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(run_doc, dict) or run_doc.get("state") != "paused":
        print(
            f"pitwall agents: run {full_id!r} is not paused "
            f"(state {run_doc.get('state') if isinstance(run_doc, dict) else '?'}); "
            "only paused runs resume",
            file=sys.stderr,
        )
        return 1
    harness = run_doc.get("provider")
    if not isinstance(harness, str) or not harness:
        print(
            f"pitwall agents: run {full_id!r} has no harness; cannot resume",
            file=sys.stderr,
        )
        return 1
    try:
        attempt = int(run_doc.get("attempt", 1))
    except TypeError, ValueError:
        attempt = 1

    store = RunStore(state_root(env), full_id)
    box = store.mailbox()
    emitter = EventEmitter(
        store,
        harness=harness,
        model=str(run_doc.get("model") or "unknown"),
        callback=HookRunner(env),
    )
    for expired in expire_overdue(store, emitter=emitter):
        print(
            f"pitwall agents: ask {expired['ask_id']} expired; "
            f"applied its default {str(expired['choice'])!r}",
            file=sys.stderr,
        )
    pending = box.pending_asks()
    if pending:
        ids = ", ".join(ask["ask_id"] for ask in pending)
        print(
            f"pitwall agents: run {full_id!r} still waits on ask(s) {ids}; "
            "answer them before resuming",
            file=sys.stderr,
        )
        return 1

    deliver_path = store.artifact("prompt.deliver.md")
    retained_path = store.artifact("prompt.md")
    try:
        if deliver_path.is_file():
            original = deliver_path.read_bytes()
        elif retained_path.is_file():
            original = retained_path.read_bytes()
        else:
            print(
                f"pitwall agents: run {full_id!r} kept no prompt "
                "(re-dispatch with --routing-retain-prompt or ask support); "
                "refusing to resume a guessed prompt",
                file=sys.stderr,
            )
            return 1
    except OSError as exc:
        print(f"pitwall agents: cannot read retained prompt: {exc}", file=sys.stderr)
        return 1

    resolved: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for ask in box.asks():
        answer = box.get_answer(ask["ask_id"])
        if answer is not None:
            resolved.append((ask, answer))

    acked = {ack["steer_id"] for ack in box.acks()}
    undelivered_steers = [s for s in box.steers() if s["steer_id"] not in acked]

    new_attempt = attempt + 1

    try:
        resume_doc = json.loads(store.artifact("resume.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"pitwall agents: run {full_id!r} kept no replay inputs ({exc}); "
            "only ask-support dispatches resume",
            file=sys.stderr,
        )
        return 1
    if not isinstance(resume_doc, dict):
        print(f"pitwall agents: run {full_id!r} has a corrupt resume record", file=sys.stderr)
        return 1
    extra_args = resume_doc.get("extraArgs", [])
    if not isinstance(extra_args, list) or not all(isinstance(a, str) for a in extra_args):
        print(f"pitwall agents: run {full_id!r} has a corrupt resume record", file=sys.stderr)
        return 1
    # Only rewrite the retained prompt once every replay input has validated,
    # so a refused resume never clobbers what the paused attempt actually saw.
    store.write_delivery_prompt(
        build_resume_prompt(original, resolved, new_attempt, steers=undelivered_steers)
    )
    resume_emitter = EventEmitter(
        store,
        harness=harness,
        model=str(run_doc.get("model") or "unknown"),
        callback=HookRunner(env),
    )
    for steer in undelivered_steers:
        box.write_ack(steer["steer_id"], note="delivered on resume")
        resume_emitter.emit_steer_acked(steer["steer_id"], {"delivery": "resume"})
    argv: list[str] = []
    model = resume_doc.get("model") or run_doc.get("model")
    if get_adapter(harness).model_positional and isinstance(model, str) and model:
        # opencode takes the model as a leading positional; re-emit it so the
        # resumed parse sees ``<model> <prompt-source>`` again.
        argv.append(model)
    argv.append(str(store.artifact(RunStore.DELIVERY_NAME)))
    workspace_mode = resume_doc.get("workspace", "shared")
    if workspace_mode in ("shared", "isolated", "auto"):
        argv.append(f"--routing-workspace={workspace_mode}")
    base = resume_doc.get("base")
    if isinstance(base, str) and base:
        argv.append(f"--routing-base={base}")
    if resume_doc.get("askSupport"):
        argv.append("--routing-ask-support")
    if isinstance(resume_doc.get("maxAsks"), int):
        argv.append(f"--routing-max-asks={resume_doc['maxAsks']}")
    if resume_doc.get("retainPrompt") is True:
        argv.append("--routing-retain-prompt")
    argv.extend(extra_args)

    child_env = {
        **env,
        "PITWALL_AGENTS_DISPATCH_ID": full_id,
        "PITWALL_AGENTS_ATTEMPT": str(new_attempt),
        "PITWALL_AGENTS_RESUME": "1",
    }
    return dispatch_legacy(harness, argv, environ=child_env)
