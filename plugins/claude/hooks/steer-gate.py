#!/usr/bin/env python3
"""PreToolUse tier-2 steering gate: defer to `pitwall agents _steer-gate`; fail closed.

When the gate cannot be evaluated (the CLI is missing, it times out, or it errors) this
hook blocks the tool call instead of letting a blocking steer go unenforced.

The decision itself lives in the CLI: while a priority, scope, or stop steer that requires acknowledgement
is unacknowledged it denies every tool call except the pitwall-channel server's own read_steering, ack_steer, and
ask_orchestrator tools, which it matches by exact server name (Codex spells it pitwall_channel).
"""

import json
import os
import shutil
import subprocess
import sys

TIMEOUT_SECONDS = 10
RECOVERY = (
    "Do not try to fix this with a tool call. Type "
    "`! uv tool install --python 3.14 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl` "  # noqa: E501  # reason: one-line URL so the release validator checks its version
    "at the prompt to (re)install the CLI into ~/.local/bin, make sure ~/.local/bin is on the "
    "PATH of the agent that runs this hook (`uv tool update-shell`), or disable the Pitwall "
    "plugin with /plugin. "
    "Check the install afterwards with `pitwall doctor`."
)


def block(cause):
    reason = (
        "The Pitwall steering gate could not be evaluated (" + cause + "), "
        "so this tool call is blocked. " + RECOVERY
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


def find_pitwall():
    """The CLI on PATH, else where `uv tool install` puts it (hook PATHs are often minimal)."""
    command = shutil.which("pitwall")
    if command is not None:
        return command
    local = os.path.join(os.path.expanduser("~"), ".local", "bin", "pitwall")
    if os.path.isfile(local) and os.access(local, os.X_OK):
        return local
    return None


def main():
    if not os.environ.get("PITWALL_AGENTS_CHANNEL_DISPATCH_ID"):
        return 0  # not a dispatched harness: there is no steer to enforce
    command = find_pitwall()
    if command is None:
        return block("the `pitwall` command was not found on PATH or in ~/.local/bin")
    payload = sys.stdin.buffer.read(1024 * 1024)
    try:
        completed = subprocess.run(
            [command, "agents", "_steer-gate"],
            input=payload,
            capture_output=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return block(f"`pitwall agents _steer-gate` timed out after {TIMEOUT_SECONDS} s")
    except OSError as exc:
        return block(f"`pitwall agents _steer-gate` could not run: {exc}")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = detail[-1][:200] if detail else "no error output"
        return block(f"`pitwall agents _steer-gate` exited {completed.returncode}: {tail}")
    output = completed.stdout
    if output.strip():
        try:
            json.loads(output)
        except ValueError:
            return block("`pitwall agents _steer-gate` printed output that is not JSON")
        sys.stdout.buffer.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
