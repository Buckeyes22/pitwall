"""Register the Pitwall MCP server with coding-agent harnesses (``pitwall mcp install``).

Every registration is named ``pitwall``, launches ``pitwall mcp serve broker`` over stdio,
and forwards the server's required variables by reference, never by value.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pitwall.agents.paths import codex_home

SERVER_NAME = "pitwall"
HARNESSES = ("claude-code", "codex", "opencode")
FORWARDED_ENV = ("RUNPOD_API_KEY", "DATABASE_URL", "REDIS_URL", "PITWALL_CONFIG_FILE")
Scope = Literal["user", "project"]
_BEGIN = "# >>> pitwall mcp (managed by `pitwall mcp install`; edits here are overwritten)"
_END = "# <<< pitwall mcp"
_BINARY = {"claude-code": "claude", "codex": "codex", "opencode": "opencode"}
#: Scopes each harness actually loads MCP servers from. Codex ignores
#: ``[mcp_servers]`` in a project's ``.codex/config.toml`` (verified live with
#: codex-cli 0.153.4: ``codex mcp list`` omits it, even for a trusted project),
#: so Codex registrations are user scope only.
SCOPES: dict[str, tuple[str, ...]] = {
    "claude-code": ("user", "project"),
    "codex": ("user",),
    "opencode": ("user", "project"),
}
VERIFY_HINT: dict[tuple[str, str], str] = {
    ("claude-code", "user"): "claude mcp list",
    (
        "claude-code",
        "project",
    ): "restart Claude Code in this project and approve the pitwall server",
    ("codex", "user"): "codex mcp list",
    ("opencode", "user"): "opencode mcp list",
    ("opencode", "project"): "opencode mcp list, run from this project",
}


class McpInstallError(RuntimeError):
    """A harness config cannot be changed safely; nothing was written for that harness."""


@dataclass(frozen=True)
class InstallPlan:
    harness: str
    scope: Scope
    path: Path
    before: str
    after: str
    commands: tuple[tuple[str, ...], ...] = ()

    @property
    def changed(self) -> bool:
        return self.before != self.after or bool(self.commands)


_SERVE_ARGS = ["mcp", "serve", "broker"]


def server_command(executable: str | None = None) -> list[str]:
    """``pitwall mcp serve broker`` via the console script beside the interpreter when present,
    else ``<python> -m pitwall mcp serve broker``."""
    python = Path(executable or sys.executable)
    script = python.with_name("pitwall")
    if script.is_file() and os.access(script, os.X_OK):
        return [str(script), *_SERVE_ARGS]
    return [str(python), "-m", "pitwall", *_SERVE_ARGS]


def _require_scope(harness: str, scope: str) -> None:
    if harness in SCOPES and scope not in SCOPES[harness]:
        raise McpInstallError(
            f"{harness} does not load MCP servers from {scope}-scope config; "
            f"rerun with --scope {SCOPES[harness][0]}"
        )


def config_path(
    harness: str, scope: Scope, *, environ: Mapping[str, str], home: Path, project_root: Path
) -> Path:
    _require_scope(harness, scope)
    if harness == "claude-code":
        if scope == "project":
            return project_root / ".mcp.json"
        root = environ.get("CLAUDE_CONFIG_DIR")
        return (Path(root).expanduser() if root else home) / ".claude.json"
    if harness == "codex":
        return codex_home(environ, home) / "config.toml"
    if harness == "opencode":
        if scope == "project":
            return project_root / "opencode.json"
        config_home = environ.get("XDG_CONFIG_HOME")
        return (
            (Path(config_home).expanduser() if config_home else home / ".config")
            / "opencode"
            / "opencode.json"
        )
    raise McpInstallError(f"unknown harness {harness!r}; expected one of {', '.join(HARNESSES)}")


def render_entry(harness: str, command: Sequence[str]) -> dict[str, Any] | str:
    if harness == "claude-code":
        return {
            "type": "stdio",
            "command": command[0],
            "args": list(command[1:]),
            "env": {name: "${" + name + ":-}" for name in FORWARDED_ENV},
        }
    if harness == "opencode":
        return {
            "type": "local",
            "command": list(command),
            "enabled": True,
            "environment": {name: "{env:" + name + "}" for name in FORWARDED_ENV},
        }
    if harness == "codex":
        return (
            "\n".join(
                [
                    _BEGIN,
                    f"[mcp_servers.{SERVER_NAME}]",
                    f"command = {json.dumps(command[0])}",
                    "args = [" + ", ".join(json.dumps(arg) for arg in command[1:]) + "]",
                    "env_vars = [" + ", ".join(json.dumps(name) for name in FORWARDED_ENV) + "]",
                    _END,
                ]
            )
            + "\n"
        )
    raise McpInstallError(f"unknown harness {harness!r}; expected one of {', '.join(HARNESSES)}")


def render_snippet(harness: str, scope: Scope, command: Sequence[str]) -> str:
    """The exact text ``docs/agents`` shows for one harness and scope."""
    _require_scope(harness, scope)
    entry = render_entry(harness, command)
    if harness == "claude-code" and scope == "user":
        return f"claude mcp add-json --scope user {SERVER_NAME} {shlex.quote(json.dumps(entry))}"
    if harness == "codex":
        assert isinstance(entry, str)
        return entry.rstrip("\n")
    section = "mcpServers" if harness == "claude-code" else "mcp"
    return json.dumps({section: {SERVER_NAME: entry}}, indent=2)


def _is_ours(entry: Any) -> bool:
    if not isinstance(entry, Mapping):
        return False
    raw_command = entry.get("command")
    args: list[Any]
    if isinstance(raw_command, list):
        command = raw_command[0] if raw_command else ""
        args = list(raw_command[1:])
    else:
        command = raw_command
        args = list(entry.get("args") or [])
    tokens = [str(command), *(str(arg) for arg in args)]
    if Path(tokens[0]).name == "pitwall":
        return tokens[1:] == _SERVE_ARGS
    return tokens[1:3] == ["-m", "pitwall"] and tokens[3:] == _SERVE_ARGS


def _read_json(path: Path) -> tuple[str, dict[str, Any]]:
    """Return ``(raw text, parsed object)``; raise :class:`McpInstallError` on anything else."""
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    if not before.strip():
        return before, {}
    try:
        data = json.loads(before)
    except json.JSONDecodeError as exc:
        raise McpInstallError(
            f"{path} is not strict JSON ({exc.msg} at line {exc.lineno}); fix or convert it, then rerun"
        ) from exc
    if not isinstance(data, dict):
        raise McpInstallError(
            f"{path} does not contain a top-level JSON object; fix it, then rerun"
        )
    return before, data


def _plan_json(
    harness: str,
    scope: Scope,
    path: Path,
    section: str,
    entry: dict[str, Any],
    *,
    remove: bool,
    force: bool,
) -> InstallPlan:
    before, data = _read_json(path)
    if not before.strip() and harness == "opencode":
        data = {"$schema": "https://opencode.ai/config.json"}
    raw_section = data.get(section, {})
    if not isinstance(raw_section, dict):
        raise McpInstallError(f"{path} has a non-object {section!r} section; fix it, then rerun")
    existing = raw_section.get(SERVER_NAME)
    if existing is not None and not _is_ours(existing) and not force:
        raise McpInstallError(
            f"{path} already has a {SERVER_NAME!r} server that pitwall did not write; rerun with --force to replace it"
        )
    if remove:
        if existing is None:
            after = before
        else:
            raw_section = dict(raw_section)
            del raw_section[SERVER_NAME]
            data = dict(data)
            data[section] = raw_section
            after = json.dumps(data, indent=2) + "\n"
    elif existing == entry:
        after = before
    else:
        raw_section = dict(raw_section)
        raw_section[SERVER_NAME] = entry
        data = dict(data)
        data[section] = raw_section
        after = json.dumps(data, indent=2) + "\n"
    return InstallPlan(harness, scope, path, before, after)


def _strip_block(text: str) -> str:
    lines = text.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.rstrip("\n") == _BEGIN)
        end = next(i for i in range(start, len(lines)) if lines[i].rstrip("\n") == _END)
    except StopIteration:
        return text
    if start > 0 and lines[start - 1].strip() == "":
        start -= 1
    return "".join(lines[:start] + lines[end + 1 :])


def _plan_codex(scope: Scope, path: Path, command: Sequence[str], *, remove: bool) -> InstallPlan:
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    base = _strip_block(before)
    try:
        parsed = tomllib.loads(base)
    except tomllib.TOMLDecodeError as exc:
        # tomllib's own text can quote keys from the file; report the position only.
        raise McpInstallError(
            f"{path} is invalid TOML at line {exc.lineno}, column {exc.colno}; fix it, then rerun"
        ) from None
    servers = parsed.get("mcp_servers", {})
    if isinstance(servers, dict) and SERVER_NAME in servers:
        raise McpInstallError(
            f"{path} defines [mcp_servers.{SERVER_NAME}] outside the pitwall-managed block; "
            "remove it by hand, then rerun"
        )
    if remove:
        after = base
    else:
        block = render_entry("codex", command)
        assert isinstance(block, str)
        after = (base.rstrip("\n") + "\n\n" if base.strip() else "") + block
    tomllib.loads(after)  # the rendered file must still parse
    return InstallPlan("codex", scope, path, before, after)


def _plan_claude_user(
    path: Path, command: Sequence[str], *, remove: bool, force: bool
) -> InstallPlan:
    before = path.read_text(encoding="utf-8") if path.exists() else ""
    existing: Any = None
    if before.strip():
        _before, data = _read_json(path)
        servers = data.get("mcpServers")
        if isinstance(servers, dict):
            existing = servers.get(SERVER_NAME)
    if existing is not None and not _is_ours(existing) and not force:
        raise McpInstallError(
            f"{path} already has a {SERVER_NAME!r} server that pitwall did not write; rerun with --force to replace it"
        )
    removal = ("claude", "mcp", "remove", "--scope", "user", SERVER_NAME)
    entry = render_entry("claude-code", command)
    commands: tuple[tuple[str, ...], ...]
    if remove:
        commands = (removal,) if existing is not None else ()
    elif existing == entry:
        commands = ()
    else:
        add = ("claude", "mcp", "add-json", "--scope", "user", SERVER_NAME, json.dumps(entry))
        commands = (removal, add) if existing is not None else (add,)
    return InstallPlan("claude-code", "user", path, before, before, commands)


def plan_registration(
    harness: str,
    scope: Scope,
    *,
    environ: Mapping[str, str],
    home: Path,
    project_root: Path,
    command: Sequence[str],
    remove: bool = False,
    force: bool = False,
) -> InstallPlan:
    path = config_path(harness, scope, environ=environ, home=home, project_root=project_root)
    if harness == "claude-code" and scope == "user":
        return _plan_claude_user(path, command, remove=remove, force=force)
    if harness == "codex":
        return _plan_codex(scope, path, command, remove=remove)
    section = "mcpServers" if harness == "claude-code" else "mcp"
    entry = render_entry(harness, command)
    assert isinstance(entry, dict)
    return _plan_json(harness, scope, path, section, entry, remove=remove, force=force)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def apply_plan(plan: InstallPlan, *, run: Runner = subprocess.run) -> Path | None:
    """Write the new file atomically (backing up an existing one), then run harness commands."""
    backup: Path | None = None
    if plan.before != plan.after:
        if plan.path.exists():
            stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
            backup = plan.path.with_name(f"{plan.path.name}.bak.{stamp}")
            shutil.copy2(plan.path, backup)
            mode = plan.path.stat().st_mode & 0o777
        else:
            mode = 0o600 if plan.scope == "user" else 0o644
        plan.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{plan.path.name}.", dir=plan.path.parent)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(plan.after)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, plan.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    for command in plan.commands:
        completed = run(list(command), check=False, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise McpInstallError(
                f"`{' '.join(command[:6])}` exited {completed.returncode}: {completed.stderr.strip()[:300]}"
            )
    return backup


def detect_harnesses(*, environ: Mapping[str, str], home: Path, project_root: Path) -> list[str]:
    """Harnesses with a binary on PATH or an existing user config; filesystem only."""
    found: list[str] = []
    for harness in HARNESSES:
        user_config = config_path(
            harness, "user", environ=environ, home=home, project_root=project_root
        )
        if shutil.which(_BINARY[harness], path=environ.get("PATH")) or user_config.exists():
            found.append(harness)
    return found
