"""Tier-2 steering gate: deny tool calls while a blocking steer waits for acknowledgement (spec §8.2)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TextIO

from .channel import CHANNEL_DISPATCH_ENV, CHANNEL_STATE_ROOT_ENV, load_channel_config
from .mailbox import Mailbox
from .mcp_server import SERVER_NAME
from .run_store import state_root

BLOCKING_KINDS = frozenset({"scope", "priority", "stop"})
CHANNEL_TOOLS = ("read_steering", "ack_steer", "ask_orchestrator")


def is_channel_tool(tool_name: str) -> bool:
    # Claude keeps the server name (`mcp__pitwall-channel__read_steering`); Codex turns its
    # hyphen into an underscore (`mcp__pitwall_channel__read_steering`). Match both, and only
    # the channel server's own tools: a server whose name merely contains it is not exempt.
    parts = tool_name.lower().split("__")
    return (
        len(parts) == 3
        and parts[0] == "mcp"
        and parts[1].replace("-", "_") == SERVER_NAME.replace("-", "_")
        and parts[2] in CHANNEL_TOOLS
    )


def decide(hook_input: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any] | None:
    dispatch_id = env.get(CHANNEL_DISPATCH_ENV, "")
    try:
        uuid.UUID(dispatch_id)
    except ValueError:
        return None
    if is_channel_tool(str(hook_input.get("tool_name", ""))):
        return None
    root = Path(env[CHANNEL_STATE_ROOT_ENV]) if env.get(CHANNEL_STATE_ROOT_ENV) else state_root(env)
    run_dir = root / "runs" / dispatch_id
    config = load_channel_config(run_dir)
    if config is None or config.tier != "1":
        return None
    blocking = [
        s for s in Mailbox(run_dir, dispatch_id).unacked_steers() if s["kind"] in BLOCKING_KINDS
    ]
    if not blocking:
        return None
    directives = " | ".join(f"[{s['kind']} {s['steer_id']}] {s['message']}" for s in blocking)
    reason = (
        "Orchestrator steering needs acknowledgement before any other tool call: "
        f"{directives}. Call read_steering, apply the direction, then ack_steer with steer_id {blocking[0]['steer_id']}."
    )
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def run_stdin(raw: bytes, env: Mapping[str, str], stdout: TextIO, stderr: TextIO) -> int:
    """Evaluate one hook payload for ``pitwall agents _steer-gate``.

    Exit 0 prints the decision (or nothing when the gate does not apply). Any
    failure to evaluate exits 2 so the hook wrapper blocks the tool call: a broken
    gate must never let a blocking steer go unenforced.
    """

    try:
        decision = decide(json.loads(raw), env)
    except Exception as exc:  # reason: the gate fails closed; the wrapper turns exit 2 into a block
        print(f"pitwall: steering gate could not be evaluated: {exc}", file=stderr)
        return 2
    if decision is not None:
        print(json.dumps(decision), file=stdout)
    return 0
