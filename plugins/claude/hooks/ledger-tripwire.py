#!/usr/bin/env python3
"""ledger-tripwire.py — Stop-hook nudge for the pitwall hot-tier ledger.

Fires a one-time checkpoint (decision:block) when, in the CURRENT turn:
  1. >= 1 shim was dispatched (Bash `*-shim.sh` non-pong, or Agent subagent_type in the
     namespaced plugin shims / legacy bare shim names), AND
  2. NO Bash command in the turn mentioned `observations.jsonl` (the orchestrator's
     qualitative append target), AND
  3. a `finished` shim record written since the turn began reports a non-ok outcome.

Only direct shim launches count; a managed `dispatch_and_wait` call does not.

The shim already logged the quantitative record (`scripts/*-shim.sh` →
`~/.local/state/pitwall/agents/ledger/observations.jsonl`). This hook's job is
only the qualitative half: reminding the orchestrator to drop a one-line note when a
dispatch's outcome was NOTABLE. It does NOT require the subagent-model-routing skill to be active
— any shim dispatch counts, flat or DAG.
Ledger-write detection is textual (command text, not execution) by design for a fail-open nudge.

Fail-safe: any parse problem -> exit 0 (never blocks spuriously).
Loop-safe: if stop_hook_active is set, exit 0.
Fires at most once per turn (a single block decision emitted, then the hook returns).
Disable: disable/remove the pitwall plugin, or delete its hooks/hooks.json "Stop" entry.
"""

import json
import os
import re
import shlex
import sys

SHIM_TYPES = {
    "agy-shim",
    "claude-shim",
    "codex-shim",
    "cline-shim",
    "dsh-shim",
    "opencode-shim",
    "grok-shim",
    "goose-shim",
    "hermes-shim",
    "kimi-shim",
    "muse-shim",
    "pi-shim",
    "qwen-shim",
    "zcode-shim",
    "route-shim",
    "pitwall:codex-shim",
    "pitwall:cline-shim",
    "pitwall:dsh-shim",
    "pitwall:opencode-shim",
    "pitwall:grok-shim",
    "pitwall:goose-shim",
    "pitwall:hermes-shim",
    "pitwall:kimi-shim",
    "pitwall:muse-shim",
    "pitwall:pi-shim",
    "pitwall:qwen-shim",
    "pitwall:zcode-shim",
    "pitwall:route-shim",
    "pitwall:agy-shim",
    "pitwall:claude-shim",
}
SHIM_INVOCATION_RE = re.compile(
    r"(?:^|[;&|(`]|\n)\s*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*=\S*\s+)*"
    r"(?:(?:\S*/)?(?:bash|sh)\s+(?:-\w+\s+)*|env\s+(?:\S+\s+)*|timeout\s+\S+\s+)?"
    r"[\"']?(?:\S*/)?(?:codex|claude|cline|dsh|opencode|grok|goose|hermes|kimi|muse|pi|qwen|zcode|route|agy)-shim\.sh[\"']?(?:\s|$)",
    re.IGNORECASE,
)
SHIM_BASENAME_RE = re.compile(
    r"^(codex|claude|cline|dsh|opencode|grok|goose|hermes|kimi|muse|pi|qwen|zcode|route|agy)-shim\.sh$",
    re.IGNORECASE,
)
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")


def _shim_basename(token):
    token = token.strip("\"'")
    return bool(SHIM_BASENAME_RE.match(os.path.basename(token)))


def _is_assignment(token):
    return bool(ASSIGNMENT_RE.match(token))


def _token_basename(token):
    return os.path.basename(token.strip("\"'")).lower()


def _segment_invokes_shim(tokens):
    i = 0
    while i < len(tokens) and _is_assignment(tokens[i]):
        i += 1

    while i < len(tokens):
        base = _token_basename(tokens[i])

        if base in {
            "exec",
            "command",
            "nohup",
            "then",
            "do",
            "else",
            "elif",
            "if",
            "while",
            "until",
            "time",
            "!",
            "{",
            "(",
        }:
            i += 1
            continue

        if base == "env":
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
            while i < len(tokens) and _is_assignment(tokens[i]):
                i += 1
            continue

        if base == "timeout":
            i += 1
            while i < len(tokens) and tokens[i].startswith("-"):
                i += 1
            if i < len(tokens):
                i += 1
            continue

        if base in {"bash", "sh", "zsh"}:
            i += 1
            command_string = False
            while i < len(tokens) and tokens[i].startswith("-") and tokens[i] != "-":
                if "c" in tokens[i].lstrip("-"):
                    command_string = True
                i += 1
            if command_string and i < len(tokens):
                return shim_invoked(tokens[i])
            break

        break

    return i < len(tokens) and _shim_basename(tokens[i])


def _command_segments(cmd):
    segment = []
    quote = None
    escaped = False
    i = 0
    while i < len(cmd):
        ch = cmd[i]
        nxt = cmd[i + 1] if i + 1 < len(cmd) else ""

        if escaped:
            segment.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            segment.append(ch)
            escaped = True
            i += 1
            continue
        if quote:
            segment.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            segment.append(ch)
            i += 1
            continue
        if ch == "$" and nxt == "(":
            yield "".join(segment)
            segment = []
            i += 2
            continue
        if ch in {";", "|", "\n", "`", ")"}:
            yield "".join(segment)
            segment = []
            if ch == "|" and nxt == "|":
                i += 2
            else:
                i += 1
            continue
        if ch == "&":
            yield "".join(segment)
            segment = []
            i += 2 if nxt == "&" else 1
            continue
        segment.append(ch)
        i += 1
    yield "".join(segment)


HEREDOC_RE = re.compile(
    r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(?:.*?\n)*?\s*\2[ \t]*(?:\n|$)", re.DOTALL
)


def _strip_heredocs(cmd):
    """Drop heredoc bodies so prose mentioning a shim (docs, notes, reports) is not read as an invocation."""
    return HEREDOC_RE.sub(" ", cmd)


def _ledger_path():
    configured = os.environ.get("PITWALL_AGENTS_LEDGER")
    if configured:
        return os.path.expanduser(configured)
    if os.environ.get("PITWALL_AGENTS_STATE_HOME"):
        root = os.path.expanduser(os.environ["PITWALL_AGENTS_STATE_HOME"])
    else:
        base = os.environ.get("XDG_STATE_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "state"
        )
        root = os.path.join(base, "pitwall", "agents")
    return os.path.join(root, "ledger", "observations.jsonl")


def notable_shim_outcomes(turn_start_ts):
    """Return (records_seen, non_ok) for shim 'finished' records written at/after turn_start_ts (ISO prefix compare)."""
    try:
        with open(_ledger_path(), encoding="utf-8") as handle:
            lines = handle.readlines()[-200:]
    except (
        Exception
    ):  # reason: a hook must never fail the host; an unreadable ledger means no signal
        return 0, []
    seen, bad = 0, []
    for line in lines:
        try:
            r = json.loads(line)
        except (
            Exception
        ):  # reason: a hook must never fail the host; unparseable input means no signal
            continue
        if r.get("source") != "shim" or r.get("event") != "finished":
            continue
        ts = str(r.get("ts", ""))
        if turn_start_ts and ts and ts[:19] < turn_start_ts[:19]:
            continue
        seen += 1
        if r.get("outcome") != "ok":
            bad.append(r)
    return seen, bad


def shim_invoked(cmd):
    for segment in _command_segments(_strip_heredocs(str(cmd))):
        if not segment.strip():
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            if SHIM_INVOCATION_RE.search(segment):
                return True
            continue
        if _segment_invokes_shim(tokens):
            return True
    return False


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

    dispatched = False
    ledger_written = False
    for e in turn:
        for b in tool_uses(e):
            name = b.get("name")
            inp = b.get("input") or {}
            if name == "Agent" and inp.get("subagent_type") in SHIM_TYPES:
                dispatched = True
            elif name == "Bash":
                cmd = str(inp.get("command", ""))
                low = cmd.lower()
                if shim_invoked(cmd) and not (
                    re.search(r"\bpong\.md\b", low) or "reply with exactly" in low
                ):
                    dispatched = True
                if re.search(r">>\s*\S*observations\.jsonl", cmd) or re.search(
                    r">>\s*[\"']?\$\{?PITWALL_AGENTS_LEDGER", cmd
                ):
                    ledger_written = True
    if not dispatched or ledger_written:
        return 0
    # Only nudge when a shim actually reported a non-ok outcome this turn. Silent when every
    # finished record is ok, or when no shim record exists to judge.
    turn_start = ""
    for e in turn:
        ts = e.get("timestamp")
        if ts:
            turn_start = str(ts).replace("Z", "")
            break
    seen, bad = notable_shim_outcomes(turn_start)
    if not bad:
        return 0
    summary = "; ".join(
        f"{r.get('shim')}/{r.get('model')} -> {r.get('outcome')} (exit {r.get('exit')})"
        for r in bad[:4]
    )
    reason = (
        "pitwall LEDGER: a shim finished with a non-ok outcome this turn ("
        + summary
        + ") and no ledger note was written. "
        "The shim already logged the quantitative record (`scripts/*-shim.sh` → "
        "`~/.local/state/pitwall/agents/ledger/observations.jsonl`). If any outcome was NOTABLE "
        "(failure, surprise, tier-breaking quality, stall, clipped run), append one qualitative "
        "line to ~/.local/state/pitwall/agents/ledger/observations.jsonl per SKILL.md §The ledger. "
        "If nothing was notable, say so explicitly and continue."
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
