#!/usr/bin/env python3
"""Claude hook guard for recognizable disconnected Pitwall launches.

Claude's hook payload gives us the tool name and the complete tool input before the
tool starts.  An explicit ``Skill`` tool call for the routing skill activates a
session marker before the first child launch; a managed dispatch also activates it
for callers that use the MCP surface directly.  The marker is keyed by Claude's
stable ``session_id`` and keeps leases for concurrent dispatch calls.  This guard
denies recognizable external launches while routing is active; ordinary sessions
remain untouched.  Opaque wrappers are outside the guaranteed detection set.
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Claude's supported Unix hosts provide fcntl.
    fcntl = None
from launch_guard_leases import (
    activate_marker,
    clear_if_idle,
    deactivate_marker,
    finish_marker,
    pending_dispatches,
    response_is_error,
)
from launch_guard_markers import lease_live, marker_path, marker_root
from launch_guard_shell import (
    PROMPT_SHIM_RE,
    SHIM_AGENT_TYPES,
    SHIM_HELPER_RE,
    command_launch,
    managed_channel_tool,
    routing_command_expansion,
    routing_skill_tool,
)


def _workflow_launch(tool_input: Mapping[str, Any]) -> str | None:
    """Return a launch kind when a Workflow names an external model launch.

    A native Workflow whose script is inspectable and launches no shim or
    harness is allowed: its nodes run as this session's children, so a
    recognizable launch a node attempts is caught at its own tool call.
    A Workflow whose script cannot be read is not protected and is denied.
    """

    script = tool_input.get("script")
    if not isinstance(script, str):
        path = tool_input.get("scriptPath")
        try:
            script = Path(str(path)).expanduser().read_text(encoding="utf-8") if path else None
        except OSError:
            script = None
    if script is None:
        return "workflow-uninspectable"
    lowered = script.lower()
    if (
        any(agent_type in lowered for agent_type in SHIM_AGENT_TYPES)
        or SHIM_HELPER_RE.search(script)
        or PROMPT_SHIM_RE.search(script)
        or command_launch(script)
    ):
        return "workflow-shim"
    return None


def _decision(reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _marker_is_present(payload: Mapping[str, Any]) -> bool:
    """Return whether an explicit routing event activated this Claude session."""

    root = marker_root()
    session_id = str(payload.get("session_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_id):
        return False
    try:
        record = json.loads(marker_path(root, session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not (
        isinstance(record, dict)
        and record.get("active") is True
        and record.get("session_id") == session_id
    ):
        return False
    # A dispatch-only marker with no live lease no longer protects this
    # session; the guard must not outlive its in-flight dispatches.
    return not (
        record.get("routing_active") is not True
        and isinstance(record.get("pending_dispatches"), list)
        and not any(lease_live(lease, time.time()) for lease in pending_dispatches(record))
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:  # reason: a hook must never fail the host; unparseable input means no signal
        return 0
    if not isinstance(payload, dict):
        return 0
    if payload.get("hook_event_name") == "SessionEnd":
        deactivate_marker(payload)
        return 0
    if payload.get("hook_event_name") == "Stop":
        clear_if_idle(payload)
        return 0
    if payload.get("hook_event_name") == "UserPromptExpansion":
        if routing_command_expansion(payload):
            activate_marker(payload, skill=True)
        return 0
    if payload.get("hook_event_name") in {"PostToolUse", "PostToolUseFailure"}:
        if managed_channel_tool(str(payload.get("tool_name") or "")):
            failed = payload.get("hook_event_name") == "PostToolUseFailure" or response_is_error(
                payload
            )
            finish_marker(payload, failed=failed)
        return 0
    if payload.get("hook_event_name") != "PreToolUse":
        return 0
    tool_name = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, Mapping):
        return 0
    if routing_skill_tool(tool_name, tool_input):
        activate_marker(payload, skill=True)
        return 0
    if managed_channel_tool(tool_name):
        if tool_name.lower().endswith("__dispatch_and_wait"):
            activate_marker(payload)
        return 0

    # The marker is the activation boundary.  It is created when Claude
    # actually calls the managed dispatch tool; an absent marker means this
    # session is outside Pitwall routing and must retain normal Claude behavior.
    if not _marker_is_present(payload):
        return 0

    kind: str | None = None
    if tool_name.lower() == "bash":
        kind = command_launch(str(tool_input.get("command") or ""))
    elif tool_name.lower() == "agent":
        # Native research agents are allowed because the host runs PreToolUse
        # on a subagent's own tool calls under the parent session_id, so a
        # shim or harness launch the agent attempts is still denied at that
        # call. Only deny a native Agent whose own input names an external
        # launch.
        subagent_type = str(tool_input.get("subagent_type") or "").lower()
        if subagent_type in SHIM_AGENT_TYPES:
            kind = "shim-agent"
        else:
            prompt = str(tool_input.get("prompt") or "")
            kind = command_launch(prompt)
            if kind is None and PROMPT_SHIM_RE.search(prompt):
                kind = "shim-agent"
    elif tool_name.lower() == "workflow":
        # Native Workflow nodes run as this session's children, so an
        # inspectable script that launches no external model is allowed; an
        # uninspectable script is denied because its launches are not
        # protected.
        kind = _workflow_launch(tool_input)
    if kind is None:
        return 0

    return_code = _decision(
        f"Blocked disconnected external-model launch (recognized {kind}). Use the managed "
        "Pitwall MCP `dispatch_and_wait` tool, which returns answerable child events; "
        "do not use a direct shim/harness command or a backgrounded Agent/Bash call. "
        "The session-scoped routing marker is active. "
        "Opaque wrappers are outside the guard's detection boundary."
    )
    json.dump(return_code, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
