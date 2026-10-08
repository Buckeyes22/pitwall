"""Shim, route, and workflow-runner recognition for the dag tripwire."""

from __future__ import annotations

import os
import re
import shlex
import tomllib

CMD_MARKERS = ("command-name>/dag-routing", "command-name>/pitwall:dag-routing")
SKILL_ATTRIBUTIONS = {"pitwall", "pitwall:subagent-model-routing"}
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
MODEL_ROUTING_BASENAME_RE = re.compile(r"^pitwall$", re.IGNORECASE)
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
ROUTE_SHIM_BASENAME_RE = re.compile(r"^route-shim\.sh$", re.IGNORECASE)
AGY_SHIM_BASENAME_RE = re.compile(r"^agy-shim\.sh$", re.IGNORECASE)
CLAUDE_ROUTE_SPEC_RE = re.compile(
    r"^(sonnet|opus|haiku|fable|claude-[^@\s]*)(@[a-z0-9-]+)?$", re.IGNORECASE
)


def routes_file():
    """The pitwall.toml holding [agents.profiles] (mirrors pitwall.agents.profiles.profiles_path)."""
    for name in ("PITWALL_AGENTS_PROFILES", "PITWALL_CONFIG_FILE"):
        configured = os.environ.get(name, "").strip()
        if configured:
            return os.path.expanduser(configured)
    local = os.path.join(os.getcwd(), "pitwall.toml")
    if os.path.isfile(local):
        return local
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "pitwall", "pitwall.toml")


def second_account_route(spec):
    """True when the spec names a saved route that sets its own Claude login directory.

    Such a route reaches a second Claude account, so it is not native work. Anything
    unreadable or unexpected answers False and the boundary applies as before.
    """
    name = str(spec).split("@", 1)[0]
    try:
        with open(routes_file(), "rb") as handle:
            value = tomllib.load(handle)["agents"]["profiles"]["models"][name]["env"][
                "CLAUDE_CONFIG_DIR"
            ]
    except OSError, ValueError, KeyError, TypeError:
        return False
    return isinstance(value, str) and bool(value.strip())


def native_claude_route(spec):
    return bool(CLAUDE_ROUTE_SPEC_RE.match(spec)) and not second_account_route(spec)


def route_specs(cmd):
    """Route specs passed to route-shim.sh anywhere in a command string."""
    specs = []
    for segment in _command_segments(str(cmd)):
        if not segment.strip():
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        for index, token in enumerate(tokens[:-1]):
            if ROUTE_SHIM_BASENAME_RE.match(os.path.basename(token.strip("\"'"))):
                specs.append(tokens[index + 1])
    return specs


def agy_models(cmd):
    """Model overrides passed to agy-shim.sh anywhere in a command string."""
    models = []
    for segment in _command_segments(str(cmd)):
        if not segment.strip():
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        for index, token in enumerate(tokens):
            if not AGY_SHIM_BASENAME_RE.match(os.path.basename(token.strip("\"'"))):
                continue
            for position, argument in enumerate(tokens[index + 1 :], start=index + 1):
                if argument.startswith("--model="):
                    models.append(argument.split("=", 1)[1])
                elif argument == "--model" and position + 1 < len(tokens):
                    models.append(tokens[position + 1])
    return models


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


def shim_invoked(cmd):
    for segment in _command_segments(str(cmd)):
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


def _workflow_runner_call(tokens):
    for index, token in enumerate(tokens):
        if not MODEL_ROUTING_BASENAME_RE.match(_token_basename(token)):
            continue
        if index > 0 and _token_basename(tokens[0]) not in {
            "env",
            "exec",
            "command",
            "nohup",
            "timeout",
            "python",
            "python3",
            "python3.11",
            "python3.12",
            "python3.13",
            "python3.14",
        }:
            continue
        tail = tokens[index + 1 :]
        if tail[:1] != ["agents"]:
            continue
        tail = tail[1:]
        if len(tail) < 2 or tail[0] != "workflow" or tail[1] not in {"run", "resume"}:
            continue
        host = None
        for position, argument in enumerate(tail[2:]):
            if argument.startswith("--host="):
                host = argument.split("=", 1)[1]
                break
            if argument == "--host" and position + 3 < len(tail):
                host = tail[position + 3]
                break
        return tail[1], host
    return None


def workflow_runner_calls(cmd):
    calls = []
    for segment in _command_segments(str(cmd)):
        if not segment.strip():
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            continue
        if tokens and _token_basename(tokens[0]) in {"bash", "sh", "zsh"}:
            for index, token in enumerate(tokens[1:], start=1):
                if "c" in token.lstrip("-") and index + 1 < len(tokens):
                    calls.extend(workflow_runner_calls(tokens[index + 1]))
                    break
        call = _workflow_runner_call(tokens)
        if call is not None:
            calls.append(call)
    return calls
