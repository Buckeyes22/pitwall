"""Dispatch leases recorded in the routing-session marker."""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Mapping
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Claude's supported Unix hosts provide fcntl.
    fcntl = None

from launch_guard_markers import (
    lease_live,
    marker_lock,
    marker_path,
    marker_root,
    payload_session_id,
    payload_tool_use_id,
    read_marker,
    sweep_stale_locks,
    write_marker,
)


def pending_dispatches(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    pending = record.get("pending_dispatches")
    if not isinstance(pending, list):
        return []
    normalized: list[dict[str, Any]] = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        tool_use_id = item.get("tool_use_id")
        if not isinstance(tool_use_id, str) or not tool_use_id or len(tool_use_id) > 256:
            continue
        entry: dict[str, Any] = {"tool_use_id": tool_use_id}
        dispatch_id = item.get("dispatch_id")
        if isinstance(dispatch_id, str) and dispatch_id and len(dispatch_id) <= 256:
            entry["dispatch_id"] = dispatch_id
        created_at = item.get("created_at")
        if isinstance(created_at, (int, float)) and not isinstance(created_at, bool):
            entry["created_at"] = created_at
        normalized.append(entry)
    return normalized


def activate_marker(payload: Mapping[str, Any], *, skill: bool = False) -> None:
    """Activate routing on an explicit Skill call or managed dispatch preflight.

    Skill is a host-level tool event, so this does not infer routing from prompt text.
    A managed dispatch also creates a marker for callers that use the MCP surface
    directly.  The marker keeps a small refcount of in-flight dispatch tool calls so
    one sibling's terminal result cannot clear another sibling's protection.
    """

    session_id = payload_session_id(payload)
    if session_id is None:
        return
    root = marker_root()
    with contextlib.suppress(OSError):
        sweep_stale_locks(root, time.time())
    path = marker_path(root, session_id)
    tool_use_id = payload_tool_use_id(payload)
    try:
        with marker_lock(root, session_id):
            record = read_marker(path)
            record.update({"active": True, "session_id": session_id})
            if skill:
                record["routing_active"] = True
            if not skill:
                pending = pending_dispatches(record)
                if tool_use_id is None:
                    tool_use_id = f"anonymous-{len(pending) + 1}"
                if not any(item["tool_use_id"] == tool_use_id for item in pending):
                    pending.append({"tool_use_id": tool_use_id, "created_at": time.time()})
                record["pending_dispatches"] = pending
            write_marker(path, record)
    except OSError:
        return


def _response_details(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """Read only structured event metadata from a PostToolUse result."""

    known_events = frozenset({"ask", "terminal", "still_running", "orphan", "steer_ack"})

    def visit(value: Any) -> tuple[str | None, str | None]:
        if isinstance(value, str):
            # Real hosts can deliver the MCP result as a serialized JSON string
            # rather than a structured mapping.
            if 0 < len(value) <= 1024 * 1024 and value.lstrip()[:1] in {"{", "["}:
                try:
                    return visit(json.loads(value))
                except ValueError:
                    return None, None
            return None, None
        if isinstance(value, Mapping):
            raw_event = value.get("event") or value.get("type")
            event = raw_event if isinstance(raw_event, str) and raw_event in known_events else None
            dispatch_id = value.get("dispatch_id") or value.get("dispatchId")
            if isinstance(event, str) or isinstance(dispatch_id, str):
                return (
                    event if isinstance(event, str) else None,
                    dispatch_id if isinstance(dispatch_id, str) else None,
                )
            for key in ("structuredContent", "result", "tool_response", "content"):
                found = visit(value.get(key))
                if found != (None, None):
                    return found
        elif isinstance(value, list):
            for item in value:
                found = visit(item)
                if found != (None, None):
                    return found
        return None, None

    return visit(payload.get("tool_response"))


def response_is_error(payload: Mapping[str, Any]) -> bool:
    """Recognize an MCP error result even when no managed event was returned."""

    tool_response = payload.get("tool_response")
    if isinstance(tool_response, Mapping) and any(
        tool_response.get(key) is True for key in ("isError", "is_error")
    ):
        return True
    # Keep this tolerant of hook adapters that flatten the MCP result metadata.
    return any(payload.get(key) is True for key in ("isError", "is_error"))


def _input_dispatch_id(payload: Mapping[str, Any]) -> str | None:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, Mapping):
        return None
    value = tool_input.get("dispatch_id")
    return value if isinstance(value, str) and value and len(value) <= 256 else None


def _associate_dispatch(
    pending: list[dict[str, str]],
    tool_use_id: str | None,
    dispatch_id: str | None,
) -> bool:
    """Attach a returned dispatch ID to its originating MCP tool lease."""

    if not dispatch_id or not tool_use_id:
        return False
    for item in pending:
        if item["tool_use_id"] == tool_use_id:
            item["dispatch_id"] = dispatch_id
            return True
    return False


def _lease_index(
    pending: list[dict[str, str]],
    tool_use_id: str | None,
    dispatch_id: str | None,
) -> int | None:
    """Find a lease by its own tool ID, then by a returned dispatch ID.

    The fallback is an explicit second identity, never an arbitrary sibling. This
    matters when answer/wait calls have a new tool-use ID from the dispatch call.
    """

    if tool_use_id:
        for index, item in enumerate(pending):
            if item["tool_use_id"] == tool_use_id:
                return index
    if dispatch_id:
        for index, item in enumerate(pending):
            if item.get("dispatch_id") == dispatch_id:
                return index
    return None


def finish_marker(payload: Mapping[str, Any], *, failed: bool = False) -> None:
    """Release one dispatch lease after a successful or failed terminal call."""

    session_id = payload_session_id(payload)
    if session_id is None:
        return
    event, dispatch_id = _response_details(payload)
    dispatch_id = dispatch_id or _input_dispatch_id(payload)
    if failed and str(payload.get("tool_name") or "").lower().endswith("__dispatch_and_wait"):
        # A failed preflight has no returned dispatch identity. Release only the
        # exact PreToolUse lease that Claude reports for this failed call.
        dispatch_id = None
    if not failed and event != "terminal":
        root = marker_root()
        path = marker_path(root, session_id)
        tool_use_id = payload_tool_use_id(payload)
        try:
            with marker_lock(root, session_id):
                record = read_marker(path)
                pending = pending_dispatches(record)
                if _associate_dispatch(pending, tool_use_id, dispatch_id):
                    record["pending_dispatches"] = pending
                    write_marker(path, record)
        except OSError:
            pass
        return
    root = marker_root()
    path = marker_path(root, session_id)
    tool_use_id = payload_tool_use_id(payload)
    try:
        with marker_lock(root, session_id):
            record = read_marker(path)
            pending = pending_dispatches(record)
            if not pending:
                return
            lease_index = _lease_index(pending, tool_use_id, dispatch_id)
            if lease_index is None:
                # Never release an arbitrary sibling when the host omitted both
                # identities. The session cleanup path will handle stale state.
                return
            remaining = pending[:lease_index] + pending[lease_index + 1 :]
            if remaining or record.get("routing_active"):
                record["pending_dispatches"] = remaining
                write_marker(path, record)
            else:
                path.unlink(missing_ok=True)
    except OSError:
        return


def clear_if_idle(payload: Mapping[str, Any]) -> None:
    """End a turn's routing lease while retaining protection for live siblings."""

    session_id = payload_session_id(payload)
    if session_id is None:
        return
    root = marker_root()
    path = marker_path(root, session_id)
    try:
        with marker_lock(root, session_id):
            record = read_marker(path)
            pending = pending_dispatches(record)
            live = [lease for lease in pending if lease_live(lease, time.time())]
            if live:
                record["pending_dispatches"] = live
                write_marker(path, record)
            else:
                path.unlink(missing_ok=True)
    except OSError:
        return


def deactivate_marker(payload: Mapping[str, Any]) -> None:
    """Clear the marker when Claude ends the owning session."""

    session_id = payload_session_id(payload)
    if session_id is None:
        return
    root = marker_root()
    try:
        with marker_lock(root, session_id):
            marker_path(root, session_id).unlink(missing_ok=True)
            # Unlinked while held; a caller waiting on this inode sees the path change and
            # gives up, because the session it waited on is over.
            (root / f".{session_id}.lock").unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except OSError:
        # Hook cleanup is best-effort; the managed runtime also clears terminal
        # dispatches and stale markers are ignored once inactive.
        pass
