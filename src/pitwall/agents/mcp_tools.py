"""The orchestrator-channel MCP tools, registered by role (plan Tasks 14 and 15)."""

from __future__ import annotations

import math
import re
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, BinaryIO

from .channel import (
    CHANNEL_DISPATCH_ENV,
    CHANNEL_STATE_ROOT_ENV,
    effective_deadline_s,
    epoch_of,
    load_channel_config,
    read_channel_inbox,
    resolve_with_default,
)
from .errors import RoutingError
from .events import EventEmitter
from .hooks import HookRunner
from .mailbox import (
    BLOCKED_ON,
    MAX_DEADLINE_S,
    MAX_OPTIONS,
    SEVERITIES,
    STEER_KINDS,
    MailboxCapError,
    MailboxError,
)
from .managed_channel import (
    DEFAULT_DISPATCH_SECONDS,
    DEFAULT_WAIT_SECONDS,
    MAX_DISPATCH_SECONDS,
    MAX_WAIT_SECONDS,
    LaunchRequest,
    ManagedChannelError,
    WaitCancelled,
    answer_once,
    require_dispatch_id,
    start_dispatch,
    steer_once,
    verify_child_channel,
    wait_for_event,
)
from .mcp_server import ChannelServer, ToolCancelled, ToolError
from .run_store import RunStore, state_root

POLL_INTERVAL_S = 0.5
PROGRESS_INTERVAL_S = 15.0
STOP_INSTRUCTION = "Wrap up now: start no new work, report what you completed, and exit normally."
_UNEXPANDED = re.compile(
    r"^(\$\{[A-Za-z_][A-Za-z0-9_]*(:-[^}]*)?\}|\{env:[A-Za-z_][A-Za-z0-9_]*\})$"
)


def clean_env(env: Mapping[str, str]) -> dict[str, str]:
    """Drop empty values and references a harness passed through without expanding."""
    return {key: value for key, value in env.items() if value and not _UNEXPANDED.match(value)}


_OPTION = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "text": {"type": "string"}},
    "required": ["id", "text"],
}
ASK_TOOL = {
    "name": "ask_orchestrator",
    "title": "Ask the orchestrator",
    "annotations": {
        "title": "Ask the orchestrator",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "description": (
        "Ask the orchestrator one blocking question when the default is dangerous or irreversible. "
        "Blocks until an answer or the deadline, then returns the answer or your default. Give 2-8 "
        "options, a real default with a rationale, and file paths only, never diffs. If the call times "
        "out, call it again with the same question to keep waiting."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "minLength": 1},
            "blocked_on": {"type": "string", "enum": sorted(BLOCKED_ON)},
            "options": {"type": "array", "minItems": 1, "maxItems": MAX_OPTIONS, "items": _OPTION},
            "default": {"type": "string", "description": "One of the option ids, or 'abort'."},
            "default_rationale": {"type": "string"},
            "files_touched": {"type": "array", "items": {"type": "string"}},
            "severity": {"type": "string", "enum": sorted(SEVERITIES)},
            "deadline_s": {"type": "integer", "minimum": 1, "maximum": MAX_DEADLINE_S},
        },
        "required": ["question", "blocked_on", "options", "default"],
        "additionalProperties": False,
    },
}
READ_STEERING_TOOL = {
    "name": "read_steering",
    "title": "Read orchestrator steering",
    "annotations": {
        "title": "Read orchestrator steering",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "description": "Return orchestrator directives not yet acknowledged. Call before risky tool calls and after each milestone.",
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}
ACK_STEER_TOOL = {
    "name": "ack_steer",
    "title": "Acknowledge a steering directive",
    "annotations": {
        "title": "Acknowledge a steering directive",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "description": "Acknowledge one directive after applying it. A stop directive means wrap up and exit normally.",
    "inputSchema": {
        "type": "object",
        "properties": {"steer_id": {"type": "string"}, "note": {"type": "string"}},
        "required": ["steer_id"],
        "additionalProperties": False,
    },
}


_WAIT_PROPERTIES = {
    "wait_seconds": {
        "type": "number",
        "minimum": 0.01,
        "maximum": MAX_WAIT_SECONDS,
        "description": "How long this MCP call may wait before returning still_running.",
    },
}
DISPATCH_AND_WAIT_TOOL = {
    "name": "dispatch_and_wait",
    "title": "Dispatch an external model and wait",
    "annotations": {
        "title": "Dispatch an external model and wait",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": False,
        "openWorldHint": True,
    },
    "description": (
        "Start one external-model dispatch through Pitwall's durable supervisor and wait for its next "
        "event: ask, defaulted, steer_ack, terminal, orphan, or still_running. Every event lists "
        "defaults_applied. Use this tool instead of launching a harness CLI, shim script, or "
        "background Agent/Bash process directly."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "route": {"type": "string", "minLength": 1, "description": "A configured route spec."},
            "provider": {
                "type": "string",
                "minLength": 1,
                "description": "A direct harness (agent CLI).",
            },
            "prompt": {"type": "string", "minLength": 1},
            "workspace": {"type": "string", "enum": ["shared", "isolated", "auto"]},
            "task_mode": {"type": "string", "enum": ["read", "write"]},
            "base": {"type": "string"},
            "timeout_seconds": {
                "type": "number",
                "exclusiveMinimum": 0,
                "maximum": MAX_DISPATCH_SECONDS,
            },
            "ask_support": {"type": "boolean", "default": True},
            "max_asks": {"type": "integer", "minimum": 1, "maximum": 50},
            "retain_prompt": {"type": "boolean", "default": False},
            "extra_args": {"type": "array", "items": {"type": "string"}},
            **_WAIT_PROPERTIES,
        },
        "required": ["prompt"],
        "additionalProperties": False,
    },
}
WAIT_DISPATCH_TOOL = {
    "name": "wait_dispatch",
    "title": "Wait for a managed dispatch",
    "annotations": {
        "title": "Wait for a managed dispatch",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "description": (
        "Reattach to a durable Pitwall dispatch by its full UUID or reattach handle and return its next "
        "event: ask, defaulted, steer_ack, terminal, orphan, or still_running. Every event lists "
        "defaults_applied."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "dispatch_id": {"type": "string"},
            "reattach_handle": {"type": "string"},
            **_WAIT_PROPERTIES,
        },
        "additionalProperties": False,
    },
}
ANSWER_AND_WAIT_TOOL = {
    "name": "answer_and_wait",
    "title": "Answer a child ask and wait",
    "annotations": {
        "title": "Answer a child ask and wait",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "description": (
        "Record a choice for a child ask, idempotently, then wait for the next event: ask, defaulted, "
        "steer_ack, terminal, orphan, or still_running. Every event lists defaults_applied. A "
        "conflicting duplicate answer is an error."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "dispatch_id": {"type": "string", "minLength": 36},
            "ask_id": {"type": "string", "pattern": "^[0-9]{4}$"},
            "choice": {"type": "string", "minLength": 1},
            "note": {"type": "string"},
            "answered_by": {"type": "string", "enum": ["orchestrator", "operator"]},
            **_WAIT_PROPERTIES,
        },
        "required": ["dispatch_id", "ask_id", "choice"],
        "additionalProperties": False,
    },
}
STEER_AND_WAIT_TOOL = {
    "name": "steer_and_wait",
    "title": "Steer a managed dispatch and wait",
    "annotations": {
        "title": "Steer a managed dispatch and wait",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
    "description": (
        "Record a directive for a live or paused managed dispatch and wait for its next event: ask, "
        "defaulted, steer_ack, terminal, orphan, or still_running. Every event lists defaults_applied."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "dispatch_id": {"type": "string", "minLength": 36},
            "kind": {"type": "string", "enum": sorted(STEER_KINDS)},
            "message": {"type": "string", "minLength": 1},
            "requires_ack": {"type": "boolean", "default": True},
            "deadline_s": {
                "type": "integer",
                "minimum": 1,
                "maximum": MAX_DEADLINE_S,
                "default": 300,
            },
            **_WAIT_PROPERTIES,
        },
        "required": ["dispatch_id", "kind", "message"],
        "additionalProperties": False,
    },
}


def _answer_payload(ask: Mapping[str, Any], answer: Mapping[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ask_id": ask["ask_id"],
        "choice": answer["choice"],
        "answered_by": answer["answered_by"],
        "resolved_by": answer["answered_by"],
    }
    if answer.get("note"):
        payload["note"] = answer["note"]
    if answer["choice"] == "abort":
        payload["instruction"] = (
            "The orchestrator chose to abort: stop now, report what you completed, and exit normally."
        )
    return payload


class SubagentTools:
    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = clean_env(env)
        self.dispatch_id = self.env[CHANNEL_DISPATCH_ENV]
        root = self.env.get(CHANNEL_STATE_ROOT_ENV)
        self.state_root = Path(root) if root else state_root(self.env)

    def _store(self) -> RunStore:
        store = RunStore(self.state_root, self.dispatch_id)
        if not store.path.is_dir():
            raise ToolError(
                f"run {self.dispatch_id} not found under {self.state_root}; use the file contract in your prompt"
            )
        return store

    def _emitter(self, store: RunStore) -> EventEmitter:
        return EventEmitter(store, harness="mcp", model="channel", callback=HookRunner(self.env))

    def ask(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        store = self._store()
        config = load_channel_config(store.path)
        if config is None:
            raise ToolError(
                "asks are not enabled for this dispatch; proceed with your best judgment and state the assumption in your report"
            )
        box = store.mailbox()
        question = str(args.get("question", ""))
        requested = args.get("deadline_s")
        cap = config.deadline_cap_s(time.time())
        deadline = (
            min(requested, cap)
            if isinstance(requested, int) and not isinstance(requested, bool)
            else cap
        )
        try:
            # reenter_open: a retry after a client-side timeout lands on the existing ask
            ask = box.write_ask(
                blocked_on=str(args.get("blocked_on", "")),
                question=question,
                options=list(args.get("options") or []),
                default=args.get("default"),
                deadline_s=deadline,
                files_touched=list(args.get("files_touched") or []),
                default_rationale=args.get("default_rationale"),
                severity=str(args.get("severity", "normal")),
                max_open=config.max_open_asks,
                reenter_open=True,
            )
        except MailboxCapError as exc:  # includes MailboxOpenAskError
            raise ToolError(f"{exc}; proceed with your default") from exc
        except MailboxError as exc:
            raise ToolError(f"invalid ask: {exc}") from exc
        return self._await(store, ask, cancel, progress)

    def _await(
        self,
        store: RunStore,
        ask: Mapping[str, Any],
        cancel: threading.Event,
        progress: Callable[[str], None],
    ) -> dict[str, Any]:
        ask_id = str(ask["ask_id"])
        answer_file = store.path / "mailbox" / "answers" / f"{ask_id}.json"
        created = epoch_of(ask.get("created_at")) or time.time()
        effective = effective_deadline_s(ask, load_channel_config(store.path))
        deadline_at = created + effective
        escalate_at = created + effective / 2
        escalated = False
        next_progress = time.monotonic() + PROGRESS_INTERVAL_S
        while True:
            now = time.time()
            if not escalated and now >= escalate_at:
                escalated = True
                self._emitter(store).emit(
                    "ask.escalated",
                    {"askId": ask_id, "reason": "unanswered past half its deadline"},
                )
            remaining = deadline_at - now
            if remaining <= 0:
                # Apply the deadline before accepting a durable answer. The
                # mailbox's ask-scoped arbitration lock makes this default
                # race safely with an explicit writer that crossed the
                # deadline between its caller-side checks and its commit.
                return _answer_payload(
                    ask, resolve_with_default(store, ask, emitter=self._emitter(store))
                )
            if answer_file.exists() and (answer := store.mailbox().get_answer(ask_id)) is not None:
                return _answer_payload(ask, answer)
            if cancel.wait(min(POLL_INTERVAL_S, remaining)):
                raise ToolCancelled()
            if time.monotonic() >= next_progress:
                progress(
                    f"waiting for the orchestrator's answer to ask {ask_id} ({int(remaining)}s left)"
                )
                next_progress = time.monotonic() + PROGRESS_INTERVAL_S

    def read_steering(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        store = self._store()
        box = store.mailbox_if_present()
        if box is None:
            return {"steers": []}
        acked = {ack["steer_id"] for ack in box.acks()}
        pending = [steer for steer in box.steers() if steer["steer_id"] not in acked]
        emitter = self._emitter(store)
        for steer in pending:
            if not steer["requires_ack"]:  # advisory notes are delivered exactly once
                box.write_ack(steer["steer_id"], note="delivered by read_steering")
                emitter.emit_steer_acked(steer["steer_id"], {"delivery": "read_steering"})
        keys = ("steer_id", "kind", "message", "requires_ack", "deadline_s", "created_at")
        return {"steers": [{key: steer[key] for key in keys} for steer in pending]}

    def ack_steer(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        steer_id = str(args.get("steer_id", ""))
        store = self._store()
        box = store.mailbox_if_present()
        if box is None:
            raise ToolError(f"unknown steer {steer_id}")
        steer = next((s for s in box.steers() if s["steer_id"] == steer_id), None)
        if steer is None:
            raise ToolError(f"unknown steer {steer_id}")
        if steer_id in {ack["steer_id"] for ack in box.acks()}:
            return {"acked": steer_id, "kind": steer["kind"], "already": True}
        note = args.get("note")
        box.write_ack(steer_id, note=note if isinstance(note, str) else None)
        self._emitter(store).emit_steer_acked(steer_id)
        result: dict[str, Any] = {"acked": steer_id, "kind": steer["kind"]}
        if steer["kind"] == "stop":
            result["instruction"] = f"{steer['message']} — {STOP_INSTRUCTION}"
        return result


def build_server(env: Mapping[str, str], stdin: BinaryIO, stdout: BinaryIO) -> ChannelServer:
    server = ChannelServer(env, stdin, stdout)
    if server.role == "subagent":
        subagent = SubagentTools(env)
        server.register(ASK_TOOL, subagent.ask)
        server.register(READ_STEERING_TOOL, subagent.read_steering)
        server.register(ACK_STEER_TOOL, subagent.ack_steer)
    elif server.role == "orchestrator":
        orchestrator = OrchestratorTools(env)
        server.register(INBOX_TOOL, orchestrator.inbox)
        server.register(ANSWER_ASK_TOOL, orchestrator.answer)
        server.register(DISPATCH_AND_WAIT_TOOL, orchestrator.dispatch_and_wait)
        server.register(ANSWER_AND_WAIT_TOOL, orchestrator.answer_and_wait)
        server.register(WAIT_DISPATCH_TOOL, orchestrator.wait_dispatch)
        server.register(STEER_AND_WAIT_TOOL, orchestrator.steer_and_wait)
    return server


INBOX_TOOL = {
    "name": "inbox",
    "title": "Channel inbox",
    "annotations": {
        "title": "Channel inbox",
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "description": (
        "List unresolved asks and unacknowledged steers across runs (or one run). Each ask carries "
        "its options, default, remaining deadline, and a `runs diff` command for context."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"dispatch_id": {"type": "string"}},
        "additionalProperties": False,
    },
}
ANSWER_ASK_TOOL = {
    "name": "answer_ask",
    "title": "Answer a subagent's ask",
    "annotations": {
        "title": "Answer a subagent's ask",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
    "description": (
        "Answer one ask by choosing one of its option ids (or 'abort'). Escalate schema, destructive, "
        "spend, and blocking asks to the operator instead of answering them yourself."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "dispatch_id": {"type": "string"},
            "ask_id": {"type": "string"},
            "choice": {"type": "string"},
            "note": {"type": "string"},
            "answered_by": {"type": "string", "enum": ["orchestrator", "operator"]},
        },
        "required": ["dispatch_id", "ask_id", "choice"],
        "additionalProperties": False,
    },
}


def _pitwall_validation_errors() -> tuple[type[Exception], ...]:
    """Pitwall's own validation errors, whose authored text is safe to show the client."""
    from .profiles import ProfilesError
    from .profiles_toml import ProfilesTomlError
    from .registry import RegistryError

    return (RoutingError, RegistryError, ProfilesError, ProfilesTomlError)


def _log_unexpected(what: str, name: str) -> None:
    print(f"channel {what} for {name!r} failed unexpectedly", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)


class OrchestratorTools:
    def __init__(self, env: Mapping[str, str]) -> None:
        self.env = clean_env(env)

    @staticmethod
    def _wait_seconds(args: Mapping[str, Any]) -> float:
        raw = args.get("wait_seconds", DEFAULT_WAIT_SECONDS)
        if isinstance(raw, bool):
            raise ToolError("wait_seconds must be a number")
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ToolError("wait_seconds must be a number") from exc
        if not math.isfinite(value) or value <= 0 or value > MAX_WAIT_SECONDS:
            raise ToolError(f"wait_seconds must be between 0 and {MAX_WAIT_SECONDS:g}")
        return value

    def _harness_for_request(self, args: Mapping[str, Any]) -> str:
        requested = args.get("provider")
        route = args.get("route")
        if requested and route:
            raise ToolError("route and provider are mutually exclusive")
        if isinstance(requested, str) and requested:
            try:
                from .registry import load_registry

                if requested not in load_registry()["harnesses"]:
                    raise ToolError(f"unknown harness {requested!r}")
            except ToolError:
                raise
            except _pitwall_validation_errors() as exc:
                raise ToolError(f"cannot validate harness {requested!r}: {exc}") from exc
            except (
                Exception
            ) as exc:  # reason: an unexpected failure is logged, never echoed to the client
                _log_unexpected("harness validation", requested)
                raise ToolError(
                    f"cannot validate harness {requested!r}; see the server's stderr log"
                ) from exc
            return requested
        if not isinstance(route, str) or not route:
            raise ToolError("dispatch_and_wait requires route or provider")
        try:
            from .profiles import load_profiles
            from .profiles_resolve import resolve_profile
            from .registry import load_registry

            registry = load_registry()
            resolved = resolve_profile(
                route,
                registry=registry,
                routes=load_profiles(self.env, registry=registry),
                env=self.env,
                home=Path(self.env.get("HOME", "~")).expanduser(),
                prompt_source="<managed-dispatch>",
                caller_args=tuple(args.get("extra_args") or ()),
            )
        except _pitwall_validation_errors() as exc:
            raise ToolError(f"cannot resolve route {route!r}: {exc}") from exc
        except (
            Exception
        ) as exc:  # reason: an unexpected failure is logged, never echoed to the client
            _log_unexpected("route resolution", route)
            raise ToolError(f"cannot resolve route {route!r}; see the server's stderr log") from exc
        return resolved.harness

    def _require_child_channel(self, args: Mapping[str, Any]) -> str:
        raw = args.get("ask_support", True)
        if not isinstance(raw, bool):
            raise ToolError("ask_support must be a boolean")
        if not raw:
            raise ToolError("managed dispatches require ask_support=true")
        harness = self._harness_for_request(args)
        try:
            from .capability_inventory import mcp_channel_registered

            home = Path(self.env.get("HOME", "~")).expanduser()
            registered = mcp_channel_registered(harness, self.env, home)
        except (
            Exception
        ) as exc:  # reason: an unexpected failure is logged, never echoed to the client
            _log_unexpected("child channel capability check", harness)
            raise ToolError(
                f"cannot verify child channel capability for {harness!r}; "
                "see the server's stderr log"
            ) from exc
        if not registered:
            raise ToolError(
                f"managed interactive dispatch requires the pitwall-channel MCP server registered for "
                f"child harness {harness!r}; use setup mcp before launching the managed dispatch"
            )
        try:
            verify_child_channel(harness, self.env, home)
        except ManagedChannelError as exc:
            raise ToolError(
                f"managed interactive dispatch cannot verify the child channel for {harness!r}: {exc}"
            ) from exc
        return harness

    def _launch_request(self, args: Mapping[str, Any], dispatch_id: str) -> LaunchRequest:
        prompt = args.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise ToolError("prompt must be a non-empty string")
        route = args.get("route")
        harness = args.get("provider")
        if route is not None and (not isinstance(route, str) or not route):
            raise ToolError("route must be a non-empty string")
        if harness is not None and (not isinstance(harness, str) or not harness):
            raise ToolError("provider must be a non-empty string")
        raw_timeout = args.get("timeout_seconds", DEFAULT_DISPATCH_SECONDS)
        if isinstance(raw_timeout, bool):
            raise ToolError("timeout_seconds must be a number")
        try:
            timeout = float(raw_timeout)
        except (TypeError, ValueError) as exc:
            raise ToolError("timeout_seconds must be a number") from exc
        if not math.isfinite(timeout):
            raise ToolError("timeout_seconds must be finite")
        raw_extra = args.get("extra_args", [])
        if not isinstance(raw_extra, list) or not all(
            isinstance(value, str) for value in raw_extra
        ):
            raise ToolError("extra_args must be a list of strings")
        if any(
            value == "--routing-ask-support" or value.startswith("--routing-")
            for value in raw_extra
        ):
            raise ToolError("extra_args cannot override managed routing options")
        max_asks = args.get("max_asks")
        if max_asks is not None and (isinstance(max_asks, bool) or not isinstance(max_asks, int)):
            raise ToolError("max_asks must be an integer")
        ask_support = args.get("ask_support", True)
        if not isinstance(ask_support, bool):
            raise ToolError("ask_support must be a boolean")
        if not ask_support:
            raise ToolError("managed dispatches require ask_support=true")
        retain_prompt = args.get("retain_prompt", False)
        if not isinstance(retain_prompt, bool):
            raise ToolError("retain_prompt must be a boolean")
        workspace = args.get("workspace", "shared")
        task_mode = args.get("task_mode")
        base = args.get("base")
        if not isinstance(workspace, str) or (
            task_mode is not None and not isinstance(task_mode, str)
        ):
            raise ToolError("workspace and task_mode must be strings")
        if base is not None and not isinstance(base, str):
            raise ToolError("base must be a string")
        return LaunchRequest(
            dispatch_id=dispatch_id,
            route=route,
            harness=harness,
            prompt=prompt,
            workspace=workspace,
            task_mode=task_mode,
            base=base,
            timeout_seconds=timeout,
            ask_support=ask_support,
            max_asks=max_asks,
            retain_prompt=retain_prompt,
            extra_args=tuple(raw_extra),
        )

    def _wait(
        self,
        dispatch_id: str,
        cancel: threading.Event,
        progress: Callable[[str], None],
        args: Mapping[str, Any],
        *,
        skip_ask_id: str | None = None,
        steer_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return wait_for_event(
                self.env,
                dispatch_id,
                cancel,
                progress,
                wait_seconds=self._wait_seconds(args),
                skip_ask_id=skip_ask_id,
                steer_id=steer_id,
            )
        except WaitCancelled as exc:
            # MCP cancellation ends this wait only.  The independent launcher
            # remains durable and can be reattached with wait_dispatch.
            raise ToolCancelled() from exc
        except ManagedChannelError as exc:
            raise ToolError(str(exc)) from exc

    def dispatch_and_wait(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        self._wait_seconds(args)
        harness = self._require_child_channel(args)
        dispatch_id = str(uuid.uuid4())
        request = self._launch_request(args, dispatch_id)
        # A route can change between preflight and the independent dispatcher.
        # Pin the verified harness and make dispatch_route reject a different
        # resolution before it starts any harness process.
        launch_env = dict(self.env)
        launch_env["PITWALL_AGENTS_MANAGED_EXPECTED_HARNESS"] = harness
        try:
            launch = start_dispatch(launch_env, request)
            event = self._wait(dispatch_id, cancel, progress, args)
        except ManagedChannelError as exc:
            raise ToolError(str(exc)) from exc
        event.setdefault("launch", launch["launcher"])
        return event

    def wait_dispatch(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        dispatch_id = args.get("dispatch_id")
        handle = args.get("reattach_handle")
        if dispatch_id is not None and handle is not None and dispatch_id != handle:
            raise ToolError("dispatch_id and reattach_handle must identify the same run")
        target = dispatch_id or handle
        if not isinstance(target, str) or not target:
            raise ToolError("wait_dispatch requires dispatch_id or reattach_handle")
        return self._wait(target, cancel, progress, args)

    def answer_and_wait(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        try:
            self._wait_seconds(args)
            dispatch_raw = args["dispatch_id"]
            ask_raw = args["ask_id"]
            choice_raw = args["choice"]
            answered_by = args.get("answered_by", "orchestrator")
            note = args.get("note")
            if (
                not isinstance(dispatch_raw, str)
                or not isinstance(ask_raw, str)
                or not isinstance(choice_raw, str)
            ):
                raise ManagedChannelError("dispatch_id, ask_id, and choice must be strings")
            if not isinstance(answered_by, str) or answered_by not in {"orchestrator", "operator"}:
                raise ManagedChannelError("answered_by must be orchestrator or operator")
            if note is not None and not isinstance(note, str):
                raise ManagedChannelError("note must be a string")
            dispatch_id = require_dispatch_id(dispatch_raw)
            ask_id = ask_raw
            choice = choice_raw
            answer = answer_once(
                self.env,
                dispatch_id,
                ask_id,
                choice,
                note=note,
                answered_by=answered_by,
            )
            event = self._wait(dispatch_id, cancel, progress, args, skip_ask_id=ask_id)
        except KeyError as exc:
            raise ToolError("dispatch_id, ask_id, and choice are required") from exc
        except (FileNotFoundError, MailboxError, ManagedChannelError) as exc:
            raise ToolError(str(exc)) from exc
        event["answer"] = answer
        return event

    def steer_and_wait(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        try:
            self._wait_seconds(args)
            dispatch_raw = args["dispatch_id"]
            kind = args["kind"]
            message = args["message"]
            if (
                not isinstance(dispatch_raw, str)
                or not isinstance(kind, str)
                or not isinstance(message, str)
            ):
                raise ManagedChannelError("dispatch_id, kind, and message must be strings")
            dispatch_id = require_dispatch_id(dispatch_raw)
            requires_ack = args.get("requires_ack", True)
            deadline_s = args.get("deadline_s", 300)
            if not isinstance(requires_ack, bool):
                raise ManagedChannelError("requires_ack must be a boolean")
            if isinstance(deadline_s, bool) or not isinstance(deadline_s, int):
                raise ManagedChannelError("deadline_s must be an integer")
            steer = steer_once(
                self.env,
                dispatch_id,
                kind=kind,
                message=message,
                requires_ack=requires_ack,
                deadline_s=deadline_s,
            )
            event = self._wait(dispatch_id, cancel, progress, args, steer_id=str(steer["steer_id"]))
        except KeyError as exc:
            raise ToolError("dispatch_id, kind, and message are required") from exc
        except (FileNotFoundError, MailboxError, ManagedChannelError) as exc:
            raise ToolError(str(exc)) from exc
        event["steer"] = steer
        if event.get("event") == "steer_ack":
            event["ack"] = event.get("ack")
        return event

    def inbox(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        dispatch_id = args.get("dispatch_id")
        if isinstance(dispatch_id, str) and dispatch_id:
            try:
                dispatch_id = require_dispatch_id(dispatch_id)
            except ManagedChannelError as exc:
                raise ToolError(str(exc)) from exc
        try:
            return read_channel_inbox(
                self.env, dispatch_id if isinstance(dispatch_id, str) and dispatch_id else None
            )
        except FileNotFoundError as exc:
            raise ToolError(str(exc)) from exc

    def answer(
        self, args: dict[str, Any], cancel: threading.Event, progress: Callable[[str], None]
    ) -> dict[str, Any]:
        answered_by = args.get("answered_by", "orchestrator")
        if answered_by not in ("orchestrator", "operator"):
            raise ToolError("answered_by must be orchestrator or operator")
        try:
            dispatch_id = require_dispatch_id(args["dispatch_id"])
            answer = answer_once(
                self.env,
                dispatch_id,
                str(args["ask_id"]),
                choice=str(args["choice"]),
                answered_by=answered_by,
                note=args.get("note") if isinstance(args.get("note"), str) else None,
            )
            run_id = dispatch_id
        except KeyError as exc:
            raise ToolError("dispatch_id, ask_id, and choice are required") from exc
        except (FileNotFoundError, MailboxError, ManagedChannelError) as exc:
            raise ToolError(str(exc)) from exc
        return {"answer": answer, "contextCommand": f"pitwall agents runs diff {run_id}"}
