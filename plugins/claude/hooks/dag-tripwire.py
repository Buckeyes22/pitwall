#!/usr/bin/env python3
"""dag-tripwire.py — Stop-hook backstop for dag-routing host and model boundaries.

Fires a one-time checkpoint (decision:block) when, in the CURRENT turn, the /dag-routing
slash command was invoked OR the subagent-model-routing skill is active (including
plugin-namespaced skill attribution), AND one of these boundaries was crossed:
  - `pitwall agents workflow run|resume --host <non-claude>` (shared-runner boundary),
  - a Claude model routed through `route-shim.sh` (Claude stays native),
  - a Claude model passed to `agy-shim.sh` (agy is for Gemini models).

A `pitwall:<harness>-shim` Agent or a `*-shim.sh` Bash launch is a connected transport
(the shim joins the orchestrator channel itself), so direct shim dispatch is not blocked.
It does NOT fire on dev sessions that merely mention the skill (keys on command marker /
skill attribution, not mentions).

Fail-safe: any parse problem -> exit 0 (never blocks spuriously).
Loop-safe: if stop_hook_active is set, exit 0.
Disable: disable/remove the pitwall plugin, or delete its hooks/hooks.json "Stop" entry.
"""

import json
import sys

from dag_tripwire_shims import (
    CMD_MARKERS,
    SHIM_TYPES,
    SKILL_ATTRIBUTIONS,
    agy_models,
    native_claude_route,
    route_specs,
    workflow_runner_calls,
)


def is_real_user_prompt(e):
    """A genuine human prompt, not a tool-result user message and not a subagent sidechain."""
    if e.get("type") != "user" or e.get("isSidechain"):
        return False
    if "toolUseResult" in e:
        return False
    c = (e.get("message") or {}).get("content")
    if isinstance(c, str):
        return True
    if isinstance(c, list):
        return not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
    return False


def tool_uses(e):
    if e.get("type") != "assistant" or e.get("isSidechain"):
        return
    c = (e.get("message") or {}).get("content")
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                yield b


def dag_context_invoked(turn):
    """Was the command or subagent-model-routing skill active in this turn?"""
    for e in turn:
        if e.get("attributionSkill") in SKILL_ATTRIBUTIONS:
            return True
        if is_real_user_prompt(e):
            dumped = json.dumps(e)
            if any(marker in dumped for marker in CMD_MARKERS):
                return True
    return False


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:  # reason: a hook must never fail the host; unparseable input means no signal
        return 0
    if data.get("stop_hook_active"):
        return 0
    tp = data.get("transcript_path")
    if not tp:
        return 0
    try:
        with open(tp, encoding="utf-8") as handle:
            entries = [json.loads(line) for line in handle if line.strip()]
    except (
        Exception
    ):  # reason: a hook must never fail the host; an unreadable transcript means no signal
        return 0

    # Scope to the current turn: from the last genuine user prompt to end-of-file.
    start = None
    for i in range(len(entries) - 1, -1, -1):
        if is_real_user_prompt(entries[i]):
            start = i
            break
    if start is None:
        return 0
    turn = entries[start:]

    # 1) Was /dag-routing invoked, or was the subagent-model-routing skill active this turn?
    invoked = dag_context_invoked(turn)
    if not invoked:
        return 0

    # 2) Tally boundary crossings among main-loop tool calls this turn.
    runner_host_violation = None
    claude_route = None
    agy_claude_model = None
    for e in turn:
        for b in tool_uses(e):
            name = b.get("name")
            inp = b.get("input") or {}
            if name == "Agent" and inp.get("subagent_type") in SHIM_TYPES:
                for spec in route_specs(str(inp.get("prompt", ""))):
                    if native_claude_route(spec):
                        claude_route = spec
                for model in agy_models(str(inp.get("prompt", ""))):
                    if model.lower().startswith("claude"):
                        agy_claude_model = model
            elif name == "Bash":
                cmd = str(inp.get("command", ""))
                for action, host in workflow_runner_calls(cmd):
                    if host != "claude":
                        runner_host_violation = (action, host)
                for spec in route_specs(cmd):
                    if native_claude_route(spec):
                        claude_route = spec
                for model in agy_models(cmd):
                    if model.lower().startswith("claude"):
                        agy_claude_model = model

    if runner_host_violation is not None:
        action, host = runner_host_violation
        reason = (
            "dag-routing HOST BOUNDARY: Claude observed `pitwall agents workflow "
            f"{action}` with --host {host!r}. Shared-runner execution from Claude must "
            "declare `--host claude`; the runner then rejects Claude-harness tasks. "
            "Use native Claude Workflow for graphs containing Claude work."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    if claude_route is not None:
        reason = (
            f"dag-routing ROUTE BOUNDARY: Claude observed `route-shim.sh {claude_route}`. Claude models "
            "stay native in this host (Agent/Workflow); route-shim is for non-Claude harness "
            "routes, and for a saved Claude route that sets CLAUDE_CONFIG_DIR to reach a second "
            "account. Re-dispatch the Claude work natively and continue."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    if agy_claude_model is not None:
        reason = (
            f"dag-routing AGY BOUNDARY: Claude observed `agy-shim.sh --model {agy_claude_model}`. "
            "Claude models stay native in this host (Agent/Workflow); agy-shim is for Gemini models. "
            "Re-dispatch the Claude work natively and continue."
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
