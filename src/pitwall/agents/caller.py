"""Which session launched a dispatch: inherited key, host session id, or host process."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .channel import CHANNEL_DISPATCH_ENV
from .pids import process_identity

SESSION_ENV = "PITWALL_AGENTS_SESSION"
# Host CLIs that export their own session id to the processes they start (verified:
# Claude Code sets it for Bash children and for stdio MCP servers).
HOST_SESSION_ENVS: tuple[tuple[str, str], ...] = (("CLAUDE_CODE_SESSION_ID", "claude"),)
# Process names that mark the host session when no variable names it.
HOST_PROCESS_NAMES = frozenset(
    {
        "claude",
        "codex",
        "copilot",
        "opencode",
        "kimi",
        "qwen",
        "grok",
        "agy",
        "pi",
        "hermes",
        "cline",
        "goose",
        "muse",
        "zcode",
        "mcode",
        "dsh",
    }
)
_MAX_DEPTH = 64
_SCRIPT_SUFFIXES = (".js", ".mjs", ".cjs")


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    ppid: int
    names: frozenset[str]


@dataclass(frozen=True, slots=True)
class Caller:
    session: str
    source: str
    host: str | None
    parent_dispatch_id: str | None

    def to_public_dict(self) -> dict[str, str | None]:
        return {
            "session": self.session,
            "source": self.source,
            "host": self.host,
            "parentDispatchId": self.parent_dispatch_id,
        }


def _name(value: str) -> str:
    base = Path(value).name
    for suffix in _SCRIPT_SUFFIXES:
        base = base.removesuffix(suffix)
    return base


def read_process(pid: int) -> ProcessInfo | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        comm = stat[stat.index("(") + 1 : stat.rindex(")")]
        ppid = int(stat.rsplit(") ", 1)[1].split()[1])
        argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError, ValueError, IndexError, UnicodeDecodeError:
        return _read_process_ps(pid)
    names = {comm, *(_name(arg.decode("utf-8", "replace")) for arg in argv[:2] if arg)}
    return ProcessInfo(ppid, frozenset(names))


def _read_process_ps(pid: int) -> ProcessInfo | None:
    # macOS has no procfs; ps reports the parent and the full command line.
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "ppid=,command="],
            capture_output=True,
            text=True,
            timeout=0.5,
            check=False,
        ).stdout.split()
        return ProcessInfo(int(out[0]), frozenset(_name(arg) for arg in out[1:3]))
    except OSError, subprocess.SubprocessError, ValueError, IndexError:
        return None


def resolve_caller(
    env: Mapping[str, str],
    *,
    start_pid: int,
    read_process: Callable[[int], ProcessInfo | None] = read_process,
) -> Caller:
    parent = env.get(CHANNEL_DISPATCH_ENV) or None
    inherited = env.get(SESSION_ENV, "").strip()
    if inherited:
        host = inherited.split(":", 1)[0] if ":" in inherited else None
        return Caller(inherited, "inherited", host, parent)
    for variable, host in HOST_SESSION_ENVS:
        value = env.get(variable, "").strip()
        if value:
            return Caller(f"{host}:{value}", "host-env", host, parent)
    pid, seen = start_pid, set()
    for _ in range(_MAX_DEPTH):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)
        info = read_process(pid)
        if info is None:
            break
        match = sorted(info.names & HOST_PROCESS_NAMES)
        if match:
            identity = process_identity(pid) or "unknown-start"
            return Caller(f"{match[0]}:pid:{pid}:{identity}", "host-process", match[0], parent)
        pid = info.ppid
    return Caller("unknown", "unknown", None, parent)
