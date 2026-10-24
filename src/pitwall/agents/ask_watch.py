"""Stream orchestrator questions for the current Claude Code session."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, TextIO

from . import channel
from .channel import load_channel_config, read_channel_inbox
from .run_store import find_run


def matches_session(event: Mapping[str, Any], session_id: str | None) -> bool:
    """Check whether an event belongs to the watched Claude session."""
    if session_id is None:
        return True
    data = event.get("data")
    return isinstance(data, Mapping) and data.get("orchestratorSession") == session_id


def format_ask_line(event: Mapping[str, Any]) -> str:
    """Render one ask event as a compact notification with its reply command."""
    data = event.get("data")
    data = data if isinstance(data, Mapping) else {}
    dispatch_id = str(event.get("dispatchId", ""))
    ask_id = str(data.get("askId", ""))
    event_kind = str(event.get("event", "ask.created"))
    prefix = "PITWALL ASK ESCALATED" if event_kind == "ask.escalated" else "PITWALL ASK"
    harness = str(data.get("harness") or "unknown")
    tier = str(data.get("tier") or "4")
    deadline = data.get("deadlineS", "?")
    default = data.get("default", "?")
    question = str(data.get("question", "")).replace("\n", " ")[:400]
    options = data.get("options")
    rendered_options = []
    if isinstance(options, list):
        for option in options:
            if isinstance(option, Mapping):
                rendered_options.append(
                    f"{str(option.get('id', ''))}={str(option.get('text', ''))[:60]}"
                )
    line = (
        f"{prefix} dispatch={dispatch_id} ask={ask_id} harness={harness} tier={tier} "
        f"deadline_s={deadline} default={default} | {question} | options: "
        f"{'; '.join(rendered_options)}"
    )
    if tier == "4":
        return f"{line} | paused: answer, then pitwall agents runs resume {dispatch_id}"
    return (
        f'{line} | reply: answer_ask(dispatch_id="{dispatch_id}", '
        f'ask_id="{ask_id}", choice=<option id>)'
    )


def open_ask_events(env: Mapping[str, str], session_id: str | None) -> list[dict[str, Any]]:
    """Build ask events for unresolved questions already present at watcher startup."""
    inbox = read_channel_inbox(env, None)
    events: list[dict[str, Any]] = []
    for ask in inbox.get("asks", []):
        dispatch_id = str(ask.get("dispatchId", ""))
        try:
            run_dir = find_run(env, dispatch_id)
            config = load_channel_config(run_dir)
        except OSError, ValueError:
            continue
        event = {
            "schemaVersion": 1,
            "event": "ask.created",
            "dispatchId": dispatch_id,
            "data": {
                "askId": str(ask.get("askId", "")),
                "question": str(ask.get("question", "")),
                "options": ask.get("options", []),
                "default": ask.get("default"),
                "deadlineS": ask.get("deadlineRemainingS"),
                "blockedOn": ask.get("blockedOn"),
                "tier": config.tier if config is not None else "4",
                "orchestratorSession": (
                    config.orchestrator_session if config is not None else None
                ),
                "harness": config.harness if config is not None else None,
            },
        }
        if matches_session(event, session_id):
            events.append(event)
    return events


def _write_heartbeat(path: Path, session_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "schemaVersion": 1,
            "session": session_id,
            "pid": os.getpid(),
            "updatedEpoch": time.time(),
        }
    )
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def _tail_offset(path: Path) -> int:
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0


def _report_error(exc: Exception) -> None:
    print(f"pitwall agents inbox --watch: {exc}", file=sys.stderr)


def watch(
    events_path: Path,
    env: Mapping[str, str],
    session_id: str | None,
    *,
    out: TextIO,
    stop: threading.Event,
    poll_s: float = 0.5,
) -> int:
    """Tail new lifecycle events and maintain a session listener heartbeat until stopped."""
    heartbeat = channel.watcher_heartbeat_path(env, session_id) if session_id is not None else None
    seen: set[tuple[str, str, str]] = set()
    try:
        offset = _tail_offset(events_path)
    except Exception as exc:  # reason: an unreadable log at start must not stop the watcher
        _report_error(exc)
        offset = 0
    last_heartbeat = 0.0

    def announce(event: Mapping[str, Any]) -> None:
        try:
            if not matches_session(event, session_id):
                return
            kind = str(event.get("event", ""))
            if kind not in {"ask.created", "ask.escalated"}:
                return
            data = event.get("data")
            if not isinstance(data, Mapping):
                return
            key = (str(event.get("dispatchId", "")), str(data.get("askId", "")), kind)
            if key in seen:
                return
            seen.add(key)
            print(format_ask_line(event), file=out, flush=True)
        except Exception as exc:  # reason: one bad event must not stop the watcher
            _report_error(exc)

    if heartbeat is not None:
        try:
            _write_heartbeat(heartbeat, session_id or "")
            last_heartbeat = time.monotonic()
        except Exception as exc:  # reason: a failed heartbeat write is retried next interval
            _report_error(exc)

    try:
        for event in open_ask_events(env, session_id):
            announce(event)
    except Exception as exc:  # reason: an unreadable backlog must not stop the watcher
        _report_error(exc)

    while not stop.is_set():
        try:
            now = time.monotonic()
            if heartbeat is not None and now - last_heartbeat >= channel.WATCHER_HEARTBEAT_S:
                _write_heartbeat(heartbeat, session_id or "")
                last_heartbeat = now
            try:
                size = events_path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size < offset:
                offset = 0
            if size > offset:
                with events_path.open("rb") as stream:
                    stream.seek(offset)
                    chunk = stream.read()
                complete = chunk.rfind(b"\n") + 1
                if complete:
                    lines = chunk[:complete].splitlines()
                    offset += complete
                    for raw_line in lines:
                        try:
                            parsed = json.loads(raw_line)
                            if isinstance(parsed, dict):
                                announce(parsed)
                        except (
                            Exception
                        ) as exc:  # reason: a malformed log line is skipped, not fatal
                            _report_error(exc)
            stop.wait(poll_s)
        except Exception as exc:  # reason: the watcher ends only on stop; any poll error is retried
            _report_error(exc)
            stop.wait(poll_s)

    if heartbeat is not None:
        try:
            heartbeat.unlink(missing_ok=True)
        except OSError as exc:
            _report_error(exc)
    return 0
