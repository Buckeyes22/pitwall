"""Process liveness by pid plus start identity, shared by the run store and the managed launcher."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

#: ``ps`` can be slow on a loaded host (macOS has no procfs); a probe that merely timed out
#: must not be mistaken for "no such process", so wait generously and retry once.
PS_TIMEOUT_SECONDS = 5.0
PS_ATTEMPTS = 2


class ProcessProbeTimeout(OSError):
    """``ps`` did not answer in time: the process may well exist, its state is unknown."""


def _ps_field(pid: int, column: str) -> str | None:
    """One ``ps`` column for *pid*, or None when ps is missing or reports no such process.

    Raises ProcessProbeTimeout when ps itself timed out, so callers can tell that apart.
    """

    for _attempt in range(PS_ATTEMPTS):
        try:
            result = subprocess.run(
                ["ps", "-p", str(pid), "-o", f"{column}="],
                capture_output=True,
                text=True,
                timeout=PS_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired:
            continue
        except OSError, subprocess.SubprocessError:
            return None
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None
    raise ProcessProbeTimeout(f"ps did not answer within {PS_TIMEOUT_SECONDS:g} s for pid {pid}")


def process_identity(pid: int) -> str | None:
    """Return an OS process start identity where the host exposes one.

    None means the process is gone or the host exposes no identity; ProcessProbeTimeout means
    the probe timed out and nothing is known.
    """

    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        raw = proc_stat.read_text(encoding="utf-8")
        # The executable name can contain spaces and parentheses.  The final
        # ``) `` before the state is the stable delimiter for Linux procfs.
        tail = raw.rsplit(") ", 1)[-1].split()
        if len(tail) > 19:
            return f"linux:{tail[19]}"
    except OSError, UnicodeDecodeError, IndexError:
        pass
    # macOS does not expose procfs.  ``ps lstart`` is stable for the lifetime
    # of a process and is available on the supported Darwin hosts.
    started = _ps_field(pid, "lstart")
    return f"ps:{started}" if started else None


def _process_state(pid: int) -> str | None:
    proc_stat = Path(f"/proc/{pid}/stat")
    try:
        raw = proc_stat.read_text(encoding="utf-8")
        return raw.rsplit(") ", 1)[-1].split()[0]
    except OSError, UnicodeDecodeError, IndexError:
        pass
    try:
        state = _ps_field(pid, "stat")
    except ProcessProbeTimeout:
        return None
    return state[:1] if state else None


def pid_alive(pid: int, identity: str | None) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # EPERM means the kernel found the pid and refused the signal
        # because another user owns it -- the process exists, so fall
        # through to the zombie/identity checks below instead of treating
        # it as dead.
        pass
    except OSError:
        return False
    if _process_state(pid) == "Z":
        return False
    try:
        current = process_identity(pid)
    except ProcessProbeTimeout:
        return True  # the pid exists and its identity is unknown: treat it as live
    return current == identity if identity is not None and current is not None else True


def terminate_process_group(pgid: int, identity: str | None, grace_seconds: float = 2.0) -> bool:
    """End the process group led by *pgid*: SIGTERM, up to *grace_seconds*, then SIGKILL.

    Acts only when the leader is alive with the recorded start *identity* and still leads
    its own group, so a reused pid is never signalled, and never on the caller's own group
    (a harness reading its own run record must not kill itself). True when the group was
    signalled.
    """

    if identity is None or pgid <= 1 or pgid == os.getpgrp() or not pid_alive(pgid, identity):
        return False
    try:
        if process_identity(pgid) != identity:
            return False
    except ProcessProbeTimeout:
        return False  # cannot prove this is the recorded process, so never signal it
    try:
        if os.getpgid(pgid) != pgid:
            return False
    except OSError:
        return False
    from .process import _terminate_remaining_group

    _terminate_remaining_group(pgid, grace_seconds)
    return True
