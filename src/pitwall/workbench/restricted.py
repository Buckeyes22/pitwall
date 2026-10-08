"""Restricted mode: bubblewrap filesystem containment plus a seccomp network boundary.

Port of ``packages/pi-workbench/src/restricted.ts``. Restricted mode never falls back to an
unrestricted launch: a missing prerequisite raises ``RestrictedModeError``.
"""

from __future__ import annotations

import os
import platform
import re
import signal
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pitwall.workbench.seccomp import seccomp_program, supported_architecture

BWRAP = "/usr/bin/bwrap"
SETPRIV = "/usr/bin/setpriv"
_FORBIDDEN_MOUNTS = frozenset({"/", "/home", "/root"})
_CREDENTIAL_NAME = re.compile(
    r"(?:^|_)(?:API[_-]?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH(?:ORIZATION)?)(?:_|$)",
    re.IGNORECASE,
)
# Mount targets inside the sandbox, not host temp files.
_HIDDEN_MOUNTS = ("/home", "/root", "/run", "/tmp")  # noqa: S108  # reason: bubblewrap tmpfs targets inside the sandbox


class RestrictedModeError(RuntimeError):
    """Restricted mode cannot be enforced; the caller must not fall back to unrestricted mode."""


@dataclass(frozen=True)
class RestrictedPolicy:
    """Owned directories for one restricted Pi process tree."""

    agent_dir: Path
    runtime_root: Path
    admission_dir: Path
    runtime_dependencies: Sequence[Path] = ()
    account_budget_dir: Path | None = None
    credential_env: str | None = None


@dataclass(frozen=True)
class RestrictedHost:
    """Host facts, injectable so prerequisite checks are testable everywhere."""

    system: str = field(default_factory=lambda: platform.system().lower())
    machine: str = field(default_factory=platform.machine)
    path_exists: Callable[[str], bool] = os.path.exists
    setpriv_supports_seccomp_filter: Callable[[], bool] | None = None


def setpriv_supports_seccomp_filter(
    path: str = SETPRIV,
    read: Callable[[str], bytes] | None = None,
) -> bool:
    """Whether this setpriv accepts ``--seccomp-filter`` (util-linux 2.41+), read without running it."""

    def read_file(name: str) -> bytes:
        return Path(name).read_bytes()

    try:
        return b"seccomp-filter" in (read or read_file)(path)
    except OSError:
        return False


def assert_restricted_prerequisites(host: RestrictedHost | None = None) -> None:
    """Fail before launch when the restricted tool boundary cannot be enforced."""
    host = host or RestrictedHost()
    if host.system != "linux":
        raise RestrictedModeError(
            "restricted mode requires Linux; refusing an unrestricted fallback"
        )
    if not host.path_exists(BWRAP):
        raise RestrictedModeError(
            "restricted mode requires Linux bubblewrap; refusing an unrestricted fallback"
        )
    if not host.path_exists(SETPRIV):
        raise RestrictedModeError(
            "restricted mode requires /usr/bin/setpriv for the tool network boundary"
        )
    if supported_architecture(host.machine) is None:
        raise RestrictedModeError(
            f"restricted tool network boundary is unsupported on {host.machine}"
        )
    supports = host.setpriv_supports_seccomp_filter or setpriv_supports_seccomp_filter
    if not supports():
        raise RestrictedModeError(
            "restricted mode requires util-linux 2.41 or later: /usr/bin/setpriv lacks --seccomp-filter"
        )


def build_restricted_command(
    argv: list[str],
    workdir: Path,
    policy: RestrictedPolicy,
    *,
    host: RestrictedHost | None = None,
) -> list[str]:
    """Wrap ``argv`` in bubblewrap; home, root, run, and tmp are hidden behind tmpfs."""
    if not argv:
        raise ValueError("restricted command requires a non-empty argv")
    assert_restricted_prerequisites(host)
    cwd = workdir.resolve(strict=True)
    agent_dir = policy.agent_dir.resolve(strict=True)
    runtime = policy.runtime_root.resolve(strict=True)
    dependencies = [path.resolve(strict=True) for path in policy.runtime_dependencies]
    admission = policy.admission_dir.resolve(strict=True)
    budget = policy.account_budget_dir.resolve(strict=True) if policy.account_budget_dir else None
    owned = [cwd, agent_dir, runtime, *dependencies, admission, *([budget] if budget else [])]
    if any(str(path) in _FORBIDDEN_MOUNTS for path in owned):
        raise RestrictedModeError("restricted mounts must name specific owned directories")
    args = [
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--ro-bind",
        "/",
        "/",
    ]
    for hidden in _HIDDEN_MOUNTS:
        args += ["--tmpfs", hidden]
    args += ["--proc", "/proc", "--dev", "/dev"]
    # The package runtime and hoisted dependency roots are read-only; owned state is writable.
    writable = dict.fromkeys([cwd, agent_dir, admission, *([budget] if budget else [])])
    read_only = dict.fromkeys([runtime, *dependencies])
    for path in sorted(dict.fromkeys([*read_only, *writable]), key=lambda p: len(str(p))):
        bind = "--ro-bind" if path in read_only and path not in writable else "--bind"
        args += ["--dir", str(path.parent), bind, str(path), str(path)]
    args += ["--chdir", str(cwd), "--", os.path.abspath(argv[0]), *argv[1:]]
    return [BWRAP, *args]


def restricted_tool_environment(
    env: Mapping[str, str], credential_env: str | None = None
) -> dict[str, str]:
    """Drop the selected credential and conventional credential variables from a tool child's env."""
    selected = (credential_env or "").strip()
    return {
        name: value
        for name, value in env.items()
        if name != selected and not _CREDENTIAL_NAME.search(name)
    }


@dataclass(frozen=True)
class ToolResult:
    exit_code: int | None
    output: bytes


def run_restricted_tool(
    command: str,
    cwd: Path,
    *,
    env: Mapping[str, str] | None = None,
    credential_env: str | None = None,
    timeout: float | None = None,
    host: RestrictedHost | None = None,
) -> ToolResult:
    """Run a shell tool under setpriv with the network-denying seccomp filter.

    The filter reaches setpriv through an unlinked in-memory file descriptor, so a tool
    cannot tamper with it between sequential calls.
    """
    assert_restricted_prerequisites(host)
    program = seccomp_program(platform.machine())
    fd = os.memfd_create("pitwall-deny-network")
    try:
        os.write(fd, program)
        os.lseek(fd, 0, os.SEEK_SET)
        process = subprocess.Popen(  # noqa: S603  # reason: fixed argv; the command runs only inside the seccomp-confined bash
            [
                SETPRIV,
                "--no-new-privs",
                "--seccomp-filter",
                f"/proc/self/fd/{fd}",
                "--",
                "/bin/bash",
                "-c",
                command,
            ],
            cwd=cwd,
            env=restricted_tool_environment(env if env is not None else os.environ, credential_env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            pass_fds=[fd],
            start_new_session=True,
        )
    finally:
        os.close(fd)
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError, PermissionError:
            process.kill()
        process.communicate()
        raise
    return ToolResult(process.returncode, output)
