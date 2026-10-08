"""Routing-session marker storage for the launch guard."""

from __future__ import annotations

import errno
import json
import os
import re
import tempfile
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:  # pragma: no cover - Claude's supported Unix hosts provide fcntl.
    fcntl = None


def marker_root() -> Path:
    configured_state = os.environ.get("PITWALL_AGENTS_STATE_HOME")
    if configured_state:
        default_root = Path(configured_state).expanduser()
    else:
        base = Path(
            os.environ.get(
                "XDG_STATE_HOME",
                str(Path(os.environ.get("HOME", "~")).expanduser() / ".local" / "state"),
            )
        )
        default_root = base / "pitwall" / "agents"
    configured_marker = os.environ.get("PITWALL_AGENTS_ROUTING_MARKER_DIR")
    return (
        Path(configured_marker).expanduser()
        if configured_marker
        else default_root / "routing-sessions"
    )


PREFLIGHT_WINDOW_SECONDS = 120.0


def _state_root() -> Path:
    configured_state = os.environ.get("PITWALL_AGENTS_STATE_HOME")
    if configured_state:
        return Path(configured_state).expanduser()
    base = Path(
        os.environ.get(
            "XDG_STATE_HOME",
            str(Path(os.environ.get("HOME", "~")).expanduser() / ".local" / "state"),
        )
    )
    return base / "pitwall" / "agents"


def _pid_running(pid: object) -> bool:
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _dispatch_running(dispatch_id: str) -> bool:
    root = _state_root()
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", dispatch_id):
        return False
    if (root / "runs" / dispatch_id / "result.json").exists():
        return False
    if (root / "launches" / dispatch_id / "orphan.json").exists():
        return False
    try:
        launcher_path = root / "launches" / dispatch_id / "launcher.json"
        launcher = json.loads(launcher_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return isinstance(launcher, dict) and _pid_running(launcher.get("pid"))


def _any_managed_launch_live() -> bool:
    try:
        entries = list((_state_root() / "launches").iterdir())
    except OSError:
        return False
    return any(_dispatch_running(entry.name) for entry in entries if entry.is_dir())


def lease_live(lease: Mapping[str, Any], now: float) -> bool:
    """Whether a pending lease still has a live dispatch behind it.

    A lease with a returned dispatch ID is checked against durable runtime state.
    A preflight lease has no dispatch identity yet, so the guard only trusts it
    for the host's backgrounding window before it inspects every managed launch.
    """

    dispatch_id = lease.get("dispatch_id")
    if isinstance(dispatch_id, str) and dispatch_id:
        return _dispatch_running(dispatch_id)
    created = lease.get("created_at")
    if (
        isinstance(created, (int, float))
        and not isinstance(created, bool)
        and now - created < PREFLIGHT_WINDOW_SECONDS
    ):
        return True
    return _any_managed_launch_live()


def payload_session_id(payload: Mapping[str, Any]) -> str | None:
    value = str(payload.get("session_id") or "")
    return value if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) else None


def payload_tool_use_id(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("tool_use_id")
    if not isinstance(value, str) or not value or len(value) > 256:
        return None
    return value


def marker_path(root: Path, session_id: str) -> Path:
    return root / f"{session_id}.json"


def _same_file(fd: int, path: Path) -> bool:
    """True when ``path`` still names the inode open as ``fd``."""
    try:
        on_disk = os.stat(path)
    except FileNotFoundError:
        return False
    held = os.fstat(fd)
    return (held.st_dev, held.st_ino) == (on_disk.st_dev, on_disk.st_ino)


#: Written into a lock file by ``sweep_stale_locks`` just before it unlinks it, so a caller
#: that was waiting on that file can tell a sweep (retry) from session end (give up).
SWEPT_MARK = "swept"


@contextmanager
def marker_lock(root: Path, session_id: str):
    """Serialize marker updates from concurrent sibling MCP calls.

    The lock file can be unlinked while it is held, and a caller may already be waiting
    on it.  A holder therefore checks, after acquiring, that the path still names the
    inode it locked, so no two callers ever hold one session's lock through different
    inodes.  On a mismatch the old file says who removed it:

    * the stale sweep marks the file ``swept`` before unlinking it.  The session may be
      live (its marker is only absent between turns), so the caller retries on a fresh
      lock file;
    * session end (``deactivate_marker``) leaves it unmarked.  The session is over, so the
      caller raises FileNotFoundError instead of re-creating the lock file, and no lock
      outlives its session.  Every caller treats that error as a dropped best-effort update.
    """

    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    lock_path = root / f".{session_id}.lock"
    while True:
        with lock_path.open("a+", encoding="utf-8") as lock:
            fd = lock.fileno()
            os.fchmod(fd, 0o600)
            if fcntl is None:
                yield
                return
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                if not _same_file(fd, lock_path):
                    lock.seek(0)
                    if lock.read() == SWEPT_MARK:
                        continue
                    raise FileNotFoundError(
                        errno.ENOENT,
                        "the session lock was removed when its session ended",
                        str(lock_path),
                    )
                yield
                return
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)


#: A lock file whose session marker is gone and that was created this long ago is debris.
#: The age is time since the lock file was created: ``marker_lock`` never writes it, so
#: its mtime does not advance while the session uses it.  Safety comes from the flock
#: taken in ``sweep_stale_locks``, not from the age.
STALE_LOCK_SECONDS = 7 * 24 * 3600


def sweep_stale_locks(root: Path, now: float) -> None:
    """Remove idle lock files whose session marker is gone and that are over a week old.

    Each candidate is flocked without blocking, so a lock somebody holds is skipped; the
    checks are repeated and the file is marked ``swept`` and unlinked while holding it.  A
    caller that opened the file but has not locked it yet finds the mark and retries on a
    fresh lock file (see ``marker_lock``).
    """
    if fcntl is None:
        return
    for lock in root.glob(".*.lock"):
        session_id = lock.name[1 : -len(".lock")]
        try:
            fd = os.open(lock, os.O_RDWR)
        except OSError:
            continue
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if (
                _same_file(fd, lock)
                and not marker_path(root, session_id).exists()
                and now - os.fstat(fd).st_mtime >= STALE_LOCK_SECONDS
            ):
                # Tell a caller already waiting on this file to retry on a fresh one.
                os.write(fd, SWEPT_MARK.encode("ascii"))
                lock.unlink()
        except OSError:
            continue
        finally:
            os.close(fd)


def read_marker(path: Path) -> dict[str, Any]:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return record if isinstance(record, dict) else {}


def write_marker(path: Path, record: Mapping[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp", delete=False, encoding="utf-8"
    ) as temporary:
        temporary.write(json.dumps(record, sort_keys=True))
        temporary.flush()
        os.fchmod(temporary.fileno(), 0o600)
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)
