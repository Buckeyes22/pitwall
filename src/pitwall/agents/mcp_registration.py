"""Register the pitwall-channel MCP server in each harness's user-scope config (spec §6.3)."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import yaml_channel
from .capability_inventory import (
    CHANNEL_HARNESSES,
    CHANNEL_NESTED_SECTION,
    CHANNEL_SERVER_NAME,
    CHANNEL_YAML_SECTION,
    channel_config_path,
)
from .profiles_sync import SyncPlan

FORWARDED_ENV = (
    "PITWALL_AGENTS_CHANNEL_DISPATCH_ID",
    "PITWALL_AGENTS_CHANNEL_STATE_ROOT",
    "PITWALL_AGENTS_CHANNEL_ATTEMPT",
    "PITWALL_AGENTS_STATE_HOME",
    "XDG_STATE_HOME",
    "XDG_CONFIG_HOME",
)
TOOL_TIMEOUT_S = 3660
CHANNEL_COMMAND = "pitwall"
CHANNEL_ARGS = ("mcp", "serve", "channel")
_BEGIN = f"# >>> {CHANNEL_SERVER_NAME} (managed by pitwall agents install)"
_END = f"# <<< {CHANNEL_SERVER_NAME}"
_TOML_HARNESSES = ("codex", "grok")
_JSON_SECTION = {
    "claude": "mcpServers",
    "copilot": "mcpServers",
    "opencode": "mcp",
    "kimi": "mcpServers",
    "cline": "mcpServers",
    "qwen": "mcpServers",
    "agy": "mcpServers",
    "muse": "mcpServers",
}


def _section_keys(harness: str) -> tuple[str, ...]:
    """The key path from the config root to the harness's server map."""
    return CHANNEL_NESTED_SECTION.get(harness) or (_JSON_SECTION[harness],)


def _get_section(data: Mapping[str, Any], harness: str) -> Any:
    value: Any = data
    for key in _section_keys(harness):
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _set_section(data: dict[str, Any], harness: str, section: dict[str, Any]) -> None:
    *parents, last = _section_keys(harness)
    node = data
    for key in parents:
        child = node.get(key)
        child = dict(child) if isinstance(child, Mapping) else {}
        node[key] = child
        node = child
    node[last] = section


class RegistrationError(RuntimeError):
    """A harness config cannot be registered safely."""


@dataclass(frozen=True, slots=True)
class ChannelAdoption:
    """An older install's channel entries that a registration may replace.

    *is_entry* recognises a JSON or TOML entry the older install wrote; *marked_begin* is the
    begin-marker line of the block it wrote into a Codex or Grok TOML config.
    """

    is_entry: Callable[[Any], bool]
    marked_begin: str


_ADOPTION: ContextVar[ChannelAdoption | None] = ContextVar("channel_adoption", default=None)


@contextmanager
def adopting(adoption: ChannelAdoption) -> Iterator[None]:
    """Within the block, planned registrations replace the entries *adoption* recognises."""

    token = _ADOPTION.set(adoption)
    try:
        yield
    finally:
        _ADOPTION.reset(token)


def entry_argv(entry: Any) -> tuple[str, list[Any]] | None:
    """The ``(command, args)`` of a registered entry; OpenCode keeps argv in one list."""

    if not isinstance(entry, Mapping):
        return None
    command = entry.get("command")
    args = entry.get("args", [])
    if isinstance(command, list):
        command, args = (command[0] if command else ""), command[1:]
    return str(command), list(args or [])


def channel_server_command(env: Mapping[str, str]) -> str:
    """The absolute path of the ``pitwall`` command the channel server runs as."""

    found = shutil.which(CHANNEL_COMMAND, path=env.get("PATH"))
    if found is None:
        raise RegistrationError(
            "pitwall is not on PATH; install it "
            "(`uv tool install --python 3.14.7 https://github.com/Buckeyes22/pitwall/releases/download/v0.3.0a1/pitwall-0.3.0a1-py3-none-any.whl`) "  # noqa: E501  # reason: one-line URL so the release validator checks its version
            "before registering the channel server"
        )
    return str(Path(found).resolve())


def render_entry(harness: str, command: str) -> Any:
    if harness == "claude":
        return {
            "type": "stdio",
            "command": command,
            "args": list(CHANNEL_ARGS),
            "env": {name: "${" + name + ":-}" for name in FORWARDED_ENV},
        }
    if harness == "copilot":
        return {
            "type": "local",
            "command": command,
            "args": list(CHANNEL_ARGS),
            "env": {name: "${" + name + "}" for name in FORWARDED_ENV},
            "tools": ["*"],
        }
    if harness == "opencode":
        return {
            "type": "local",
            "command": [command, *CHANNEL_ARGS],
            "enabled": True,
            "environment": {name: "{env:" + name + "}" for name in FORWARDED_ENV},
        }
    if harness == "kimi":
        return {
            "command": command,
            "args": list(CHANNEL_ARGS),
            "toolTimeoutMs": TOOL_TIMEOUT_S * 1000,
        }
    if harness == "cline":
        return {
            "type": "stdio",
            "command": command,
            "args": list(CHANNEL_ARGS),
            "timeout": TOOL_TIMEOUT_S,
            "disabled": False,
        }
    if harness == "qwen":
        # Qwen Code starts stdio servers with its own environment, so the
        # dispatch-identity variables reach the server without an env block.
        # Its timeout is in milliseconds; a blocking ask may wait an hour.
        return {"command": command, "args": list(CHANNEL_ARGS), "timeout": TOOL_TIMEOUT_S * 1000}
    if harness == "zcode":
        # ZCode starts stdio servers with its own environment (verified with an
        # env-recording server), so no env block is needed; timeoutMs bounds a
        # blocking ask.
        return {
            "type": "stdio",
            "command": command,
            "args": list(CHANNEL_ARGS),
            "enabled": True,
            "timeoutMs": TOOL_TIMEOUT_S * 1000,
        }
    if harness == "agy":
        # The shape `agy mcp add` writes. No env block: an unexpanded `${VAR}` would mark the
        # server misconfigured, and Task 32 proves the parent environment reaches the server.
        return {"command": command, "args": list(CHANNEL_ARGS), "disabled": False}
    if harness == "muse":
        # Muse forwards named parent variables through env_vars, as Codex does.
        return {
            "command": command,
            "args": list(CHANNEL_ARGS),
            "env_vars": list(FORWARDED_ENV),
            "startup_timeout_sec": 30,
        }
    if harness == "hermes":
        # Hermes starts stdio servers with an allowlisted environment plus the entry's env,
        # expanding ${VAR} there (verified with `hermes mcp test`). timeout is per tool call.
        return {
            "command": command,
            "args": list(CHANNEL_ARGS),
            "env": {name: "${" + name + "}" for name in FORWARDED_ENV},
            "timeout": TOOL_TIMEOUT_S,
        }
    if harness == "goose":
        # goose starts stdio extensions with the parent environment (verified with
        # `goose mcp-probe`); env_keys stays empty so an unset variable never blocks loading.
        return {
            "enabled": True,
            "type": "stdio",
            "name": CHANNEL_SERVER_NAME,
            "cmd": command,
            "args": list(CHANNEL_ARGS),
            "envs": {},
            "env_keys": [],
            "timeout": TOOL_TIMEOUT_S,
            "bundled": None,
        }
    if harness == "grok":
        # Grok starts stdio servers with the parent environment (verified with an
        # env-recording server through `grok mcp doctor`), so no env table is needed.
        return (
            "\n".join(
                [
                    _BEGIN,
                    f"[mcp_servers.{CHANNEL_SERVER_NAME}]",
                    f"command = {json.dumps(command)}",
                    "args = " + json.dumps(list(CHANNEL_ARGS)),
                    "enabled = true",
                    _END,
                ]
            )
            + "\n"
        )
    if harness == "codex":
        return (
            "\n".join(
                [
                    _BEGIN,
                    f"[mcp_servers.{CHANNEL_SERVER_NAME}]",
                    f"command = {json.dumps(command)}",
                    "args = " + json.dumps(list(CHANNEL_ARGS)),
                    "env_vars = [" + ", ".join(json.dumps(name) for name in FORWARDED_ENV) + "]",
                    "startup_timeout_sec = 30",
                    f"tool_timeout_sec = {TOOL_TIMEOUT_S}",
                    _END,
                ]
            )
            + "\n"
        )
    raise KeyError(harness)


def _is_ours(entry: Any) -> bool:
    argv = entry_argv(entry)
    if argv is None:
        return False
    command, args = argv
    if Path(command).name == CHANNEL_COMMAND and args[:3] == list(CHANNEL_ARGS):
        return True
    adoption = _ADOPTION.get()
    return adoption is not None and adoption.is_entry(entry)


def _strip_marked_block(text: str) -> str:
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    inside = False
    foreign = False
    adoption = _ADOPTION.get()
    begins = {_BEGIN, adoption.marked_begin} if adoption else {_BEGIN}
    for line in lines:
        if line.rstrip("\n") in begins:
            # Drop the blank separator line directly before the managed block.
            while kept and kept[-1].strip() == "":
                kept.pop()
            inside, foreign = True, False
            continue
        if inside:
            if line.rstrip("\n") == _END:
                inside = False
                continue
            # Codex appends new tables at the end of the file, which can put them
            # before our end marker; keep every table that is not ours.
            header = line.strip()
            if header.startswith("[") and header != f"[mcp_servers.{CHANNEL_SERVER_NAME}]":
                if not foreign and kept and kept[-1].strip():
                    kept.append("\n")
                foreign = True
            if foreign:
                kept.append(line)
            continue
        kept.append(line)
    return "".join(kept)


def _plan_json(harness: str, path: Path, command: str, *, remove: bool) -> SyncPlan:
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    if before.strip():
        try:
            data = json.loads(before)
        except json.JSONDecodeError as exc:
            raise RegistrationError(
                f"{path} is not strict JSON ({exc}); fix or remove it before registering the channel server"
            ) from exc
    else:
        data = {}
    if not isinstance(data, dict):
        raise RegistrationError(f"{path} must hold a JSON object")
    section: dict[str, Any] = dict(_get_section(data, harness) or {})
    existing = section.get(CHANNEL_SERVER_NAME)
    if existing is not None and not _is_ours(existing):
        raise RegistrationError(
            f"{path}: an existing {CHANNEL_SERVER_NAME} entry is not managed by pitwall; "
            "rename or remove it"
        )
    if remove:
        section.pop(CHANNEL_SERVER_NAME, None)
        _set_section(data, harness, section)
        after = before if existing is None else json.dumps(data, indent=2) + "\n"
    else:
        if harness == "muse":
            # Muse rejects settings without a schema version.
            data.setdefault("schema_version", 1)
        section[CHANNEL_SERVER_NAME] = render_entry(harness, command)
        _set_section(data, harness, section)
        after = json.dumps(data, indent=2) + "\n"
    return SyncPlan(harness, path, before, after, (), ())


_KEY = r"""(?:[A-Za-z0-9_-]+|"(?:[^"\\]|\\.)*"|'[^']*')"""
# A table header line: bare or quoted dotted keys (any characters inside quotes) between brackets.
_TABLE_HEADER = re.compile(rf"^\s*\[\[?\s*({_KEY}(?:\s*\.\s*{_KEY})*)\s*\]\]?\s*(#.*)?$")


def _without_channel_server(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """*parsed* minus our server entry, with an emptied ``mcp_servers`` table dropped."""

    data = dict(parsed)
    servers = dict(data.get("mcp_servers", {}))
    servers.pop(CHANNEL_SERVER_NAME, None)
    data["mcp_servers"] = servers
    return _without_empty_servers(data)


def _without_empty_servers(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """*parsed* with an empty ``mcp_servers`` table dropped, so both sides compare alike."""

    data = dict(parsed)
    if data.get("mcp_servers") == {}:
        del data["mcp_servers"]
    return data


def _strip_unmarked_channel_table(text: str, path: Path, parsed: Mapping[str, Any]) -> str:
    """Drop our ``[mcp_servers.pitwall-channel]`` table when a harness rewrote it without markers.

    Removes the header through the line before the next unrelated table header (or EOF) plus
    the blank separator that follows. The scan is textual, so a layout it cannot follow (a quoted
    key, a multi-line string) would leave part of our table behind or take another table with
    it; the result is checked against the parsed document and refused when it differs.
    """
    own = f"mcp_servers.{CHANNEL_SERVER_NAME}"
    lines = text.splitlines(keepends=True)
    kept: list[str] = []
    skipping = False
    for line in lines:
        header = _TABLE_HEADER.match(line)
        if header:
            name = re.sub(r"\s+", "", header.group(1))
            if name == own or name.startswith(own + "."):
                skipping = True
                continue
            skipping = False
        if skipping:
            continue
        kept.append(line)
    while kept and kept[-1].strip() == "" and len(kept) < len(lines):
        kept.pop()
    stripped = "".join(kept)
    try:
        remaining = _without_empty_servers(tomllib.loads(stripped) if stripped.strip() else {})
        intact = remaining == _without_channel_server(parsed)
    except tomllib.TOMLDecodeError:
        intact = False
    if not intact:
        raise RegistrationError(
            f"{path}: pitwall cannot isolate the {CHANNEL_SERVER_NAME} table safely (for example "
            "a quoted table name or a multi-line string); remove the table by hand, then "
            "register again"
        )
    return stripped


def _plan_toml(harness: str, path: Path, command: str, *, remove: bool) -> SyncPlan:
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    base = _strip_marked_block(before)
    try:
        parsed = tomllib.loads(base) if base.strip() else {}
    except tomllib.TOMLDecodeError as exc:
        # tomllib's own text can quote keys from the file; report the position only.
        raise RegistrationError(
            f"{path} is invalid TOML at line {exc.lineno}, column {exc.colno}; "
            "fix it before registering the channel server"
        ) from None
    existing = parsed.get("mcp_servers", {}).get(CHANNEL_SERVER_NAME)
    if existing is not None and _is_ours(existing):
        # A harness (for example `grok mcp add`) rewrote the file without our comment markers.
        base = _strip_unmarked_channel_table(base, path, parsed)
        existing = None
    if existing is not None:
        raise RegistrationError(
            f"{path}: an existing {CHANNEL_SERVER_NAME} entry is not managed by pitwall; "
            "rename or remove it"
        )
    if remove:
        after = base
    else:
        separator = "\n\n" if base.strip() else ""
        after = (
            base.rstrip("\n")
            + (separator if base.rstrip("\n") else "")
            + render_entry(harness, command)
        )
    try:
        tomllib.loads(after)
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - generated content is valid TOML
        raise RegistrationError(f"{path}: the generated block is not valid TOML: {exc}") from exc
    return SyncPlan(harness, path, before, after, (), ())


def _with_command(entry: Mapping[str, Any]) -> dict[str, Any]:
    """goose names the executable ``cmd``; expose it as ``command`` like every other harness."""
    normalized = dict(entry)
    if "command" not in normalized and "cmd" in normalized:
        normalized["command"] = normalized["cmd"]
    return normalized


def _plan_yaml(harness: str, path: Path, command: str, *, remove: bool) -> SyncPlan:
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    try:
        after = yaml_channel.plan_text(
            before,
            CHANNEL_YAML_SECTION[harness],
            CHANNEL_SERVER_NAME,
            None if remove else render_entry(harness, command),
            lambda existing: _is_ours(_with_command(existing)),
        )
    except yaml_channel.YamlChannelError as exc:
        raise RegistrationError(f"{path}: {exc}") from exc
    return SyncPlan(harness, path, before, after, (), ())


def _plan_claude(path: Path, command: str, *, remove: bool) -> SyncPlan:
    # Claude Code rewrites ~/.claude.json while running; registration goes
    # through its own CLI and this tool never edits the file directly.
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    existing: Any = None
    if before.strip():
        try:
            existing = json.loads(before).get("mcpServers", {}).get(CHANNEL_SERVER_NAME)
        except json.JSONDecodeError, AttributeError:
            existing = None
    desired = render_entry("claude", command)
    add_json = (
        "claude",
        "mcp",
        "add-json",
        "--scope",
        "user",
        CHANNEL_SERVER_NAME,
        json.dumps(desired, sort_keys=True),
    )
    remove_command = ("claude", "mcp", "remove", "--scope", "user", CHANNEL_SERVER_NAME)
    if remove:
        commands: tuple[tuple[str, ...], ...] = (remove_command,) if existing is not None else ()
    elif existing == desired:
        commands = ()
    elif existing is not None and _is_ours(existing):
        commands = (remove_command, add_json)
    elif existing is not None:
        raise RegistrationError(
            f"{path}: an existing {CHANNEL_SERVER_NAME} entry is not managed by pitwall; "
            "rename or remove it"
        )
    else:
        commands = (add_json,)
    return SyncPlan("claude", path, before, before, (), commands)


def plan_registration(
    harness: str,
    env: Mapping[str, str],
    home: Path,
    *,
    command: str,
    remove: bool = False,
) -> SyncPlan:
    if harness not in CHANNEL_HARNESSES:
        raise KeyError(harness)
    if harness == "claude":
        return _plan_claude(channel_config_path(harness, env, home), command, remove=remove)
    if harness in _TOML_HARNESSES:
        return _plan_toml(harness, channel_config_path(harness, env, home), command, remove=remove)
    if harness in CHANNEL_YAML_SECTION:
        return _plan_yaml(harness, channel_config_path(harness, env, home), command, remove=remove)
    return _plan_json(harness, channel_config_path(harness, env, home), command, remove=remove)


#: Harnesses whose channel entry can live in a project file, and that file under the project root.
PROJECT_CHANNEL_FILES = {"claude": ".mcp.json", "opencode": "opencode.json"}


def plan_project_registration(
    harness: str, project_root: Path, command: str, *, remove: bool = False
) -> SyncPlan:
    """Plan the channel entry in *project_root*'s project-scope file for *harness*."""
    if harness not in PROJECT_CHANNEL_FILES:
        raise RegistrationError(
            f"{harness} has no project-scope channel registration; it registers at user scope"
        )
    return _plan_json(
        harness, project_root / PROJECT_CHANNEL_FILES[harness], command, remove=remove
    )


def current_entry(harness: str, env: Mapping[str, str], home: Path) -> Mapping[str, Any] | None:
    """The harness's registered pitwall-channel entry, or None."""
    if harness not in CHANNEL_HARNESSES:
        return None
    path = channel_config_path(harness, env, home)
    if not path.is_file():
        return None
    try:
        entry: Mapping[str, Any] | None
        if harness in _TOML_HARNESSES:
            entry = (
                tomllib.loads(path.read_text(encoding="utf-8"))
                .get("mcp_servers", {})
                .get(CHANNEL_SERVER_NAME)
            )
            return entry
        if harness in CHANNEL_YAML_SECTION:
            found = yaml_channel.read_entry(
                path.read_text(encoding="utf-8"), CHANNEL_YAML_SECTION[harness], CHANNEL_SERVER_NAME
            )
            return _with_command(found) if found is not None else None
        data = json.loads(path.read_text(encoding="utf-8"))
        section = _get_section(data, harness)
        entry = section.get(CHANNEL_SERVER_NAME) if isinstance(section, Mapping) else None
        return entry
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
        yaml_channel.YamlChannelError,
        AttributeError,
    ):
        return None
