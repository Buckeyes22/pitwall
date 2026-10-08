"""Shell-command and tool-input recognition for the launch guard."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Mapping
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Claude's supported Unix hosts provide fcntl.
    fcntl = None


SHIM_BASENAMES = frozenset(
    {
        "agy-shim.sh",
        "claude-shim.sh",
        "cline-shim.sh",
        "codex-shim.sh",
        "dsh-shim.sh",
        "goose-shim.sh",
        "grok-shim.sh",
        "hermes-shim.sh",
        "kimi-shim.sh",
        "muse-shim.sh",
        "opencode-shim.sh",
        "pi-shim.sh",
        "qwen-shim.sh",
        "route-shim.sh",
    }
)
SHIM_AGENT_TYPES = frozenset(
    {name.removesuffix(".sh") for name in SHIM_BASENAMES}
    | {f"pitwall:{name.removesuffix('.sh')}" for name in SHIM_BASENAMES}
)
HARNESS_BASENAMES = frozenset(
    {
        "agy",
        "antigravity",
        "claude",
        "cline",
        "codex",
        "dsh",
        "gemini",
        "goose",
        "grok",
        "hermes",
        "kimi",
        "muse",
        "opencode",
        "pi",
        "qwen",
    }
)
SHELL_WRAPPERS = frozenset(
    {
        "bash",
        "command",
        "env",
        "exec",
        "nohup",
        "setsid",
        "sh",
        "timeout",
        "time",
        "zsh",
    }
)
ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")
HEREDOC_RE = re.compile(
    r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1[^\n]*\n(?:.*?\n)*?\s*\2[ \t]*(?:\n|$)",
    re.DOTALL,
)
PROMPT_SHIM_RE = re.compile(
    r"(?:~/?|/|\./|\.\./|scripts/|[\s`'\"(])[^\s`'\")]*"
    r"(?:agy|claude|cline|codex|dsh|goose|grok|hermes|kimi|muse|opencode|pi|qwen|route)-shim\.sh"
    r"(?=\s|$|[`'\")])",
    re.IGNORECASE,
)
SHIM_HELPER_RE = re.compile(
    r"\b(?:codex|agy|grok|kimi|qwen|pi|hermes|cline|muse|goose|dsh|route|glm|minimax)\s*\("
)
ROUTING_SKILL_NAMES = frozenset(
    {
        "subagent-model-routing",
        "pitwall:subagent-model-routing",
    }
)
ROUTING_EXPANSION_NAMES = frozenset(
    {
        "dag-routing",
        "pitwall:dag-routing",
        "subagent-model-routing",
        "pitwall:subagent-model-routing",
    }
)


def _token_basename(token: str) -> str:
    return os.path.basename(token.strip("\"'")).lower()


def _command_segments(command: str):
    """Yield shell command segments without treating quoted prose as syntax."""

    segment: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        char = command[index]
        next_char = command[index + 1] if index + 1 < len(command) else ""
        if escaped:
            segment.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\":
            segment.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            segment.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            segment.append(char)
            index += 1
            continue
        if char == "$" and next_char == "(":
            yield "".join(segment)
            segment = []
            index += 2
            continue
        if char in {";", "|", "\n", "`", ")"}:
            yield "".join(segment)
            segment = []
            index += 2 if char == "|" and next_char == "|" else 1
            continue
        if char == "&":
            yield "".join(segment)
            segment = []
            index += 2 if next_char == "&" else 1
            continue
        segment.append(char)
        index += 1
    yield "".join(segment)


def _nested_shell_command(tokens: list[str], index: int) -> str | None:
    if index >= len(tokens):
        return None
    while index < len(tokens) and tokens[index].startswith("-"):
        if "c" in tokens[index].lstrip("-"):
            return tokens[index + 1] if index + 1 < len(tokens) else None
        index += 1
    return None


def _segment_launch(tokens: list[str]) -> str | None:
    """Return the recognizable launch kind for one shell segment, if any."""

    index = 0
    while index < len(tokens) and ASSIGNMENT_RE.match(tokens[index]):
        index += 1
    while index < len(tokens):
        base = _token_basename(tokens[index])
        if base in {"command", "exec", "nohup", "setsid", "time"}:
            index += 1
            continue
        if base == "env":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            while index < len(tokens) and ASSIGNMENT_RE.match(tokens[index]):
                index += 1
            continue
        if base == "timeout":
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            if index < len(tokens):
                index += 1  # duration
            continue
        if base in {"bash", "sh", "zsh"}:
            nested = _nested_shell_command(tokens, index + 1)
            return command_launch(nested) if nested is not None else None
        if base in SHIM_BASENAMES:
            return "shim"
        if base in HARNESS_BASENAMES:
            return "provider"
        if base == "pitwall" and index + 2 < len(tokens) and tokens[index + 1] == "agents":
            if tokens[index + 2] == "dispatch":
                return "dispatcher"
            if (
                index + 3 < len(tokens)
                and tokens[index + 2] == "workflow"
                and tokens[index + 3] in {"run", "resume"}
            ):
                return "dispatcher"
        return None
    return None


def command_launch(command: str | None) -> str | None:
    if not command:
        return None
    for segment in _command_segments(HEREDOC_RE.sub(" ", command)):
        if not segment.strip():
            continue
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            # An incomplete shell string is still worth blocking when it has an
            # unmistakable shim basename at the executable boundary.
            if re.search(r"(?:^|[ /])(?:[A-Za-z0-9_.-]+/)?[a-z]+-shim\.sh(?:\s|$)", segment, re.I):
                return "shim"
            continue
        found = _segment_launch(tokens)
        if found:
            return found
    return None


def managed_channel_tool(tool_name: str) -> bool:
    lowered = tool_name.lower()
    return lowered.startswith("mcp__pitwall-channel__")


def routing_skill_tool(tool_name: str, tool_input: Mapping[str, Any]) -> bool:
    return (
        tool_name.lower() == "skill"
        and str(tool_input.get("skill") or "").lower() in ROUTING_SKILL_NAMES
    )


def routing_command_expansion(payload: Mapping[str, Any]) -> bool:
    if payload.get("expansion_type") != "slash_command":
        return False
    command_name = str(payload.get("command_name") or "").lstrip("/").lower()
    return command_name in ROUTING_EXPANSION_NAMES
