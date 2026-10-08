"""Provider request admission (port of ``admission.ts`` and ``shared-admission.ts``).

``RequestAdmission`` is an in-process asyncio permit with strict FIFO hand-off.
``SharedRequestAdmission`` is a host-wide permit backed by ``flock`` on a private lock file, so
independent Pi processes that share a resource group take turns. The kernel owns the lock: a
crashed holder releases it when its file descriptor closes, with no stale-lock deletion and no
lease stealing. The lock file layout matches the packaged Pi extension, so both sides interoperate.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import platform
import stat
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

_POLL_SECONDS = 0.02


def default_admission_dir() -> Path:
    return Path.home() / ".local/state/pitwall/pi-workbench/admission"


class RequestCancelled(RuntimeError):
    """The caller cancelled a request while it was still waiting for admission."""


class RequestAdmission:
    """One shared in-process permit per active provider request, never per agent lifetime."""

    def __init__(self) -> None:
        self._busy = False
        self._waiters: deque[asyncio.Future[None]] = deque()

    async def acquire(self) -> Callable[[], None]:
        """Wait for the permit; cancelling the awaiting task withdraws the request."""
        if not self._busy:
            self._busy = True
            return self._release_once()
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                self._hand_off()  # the permit arrived as we were cancelled; pass it on
            elif waiter in self._waiters:
                self._waiters.remove(waiter)
            raise
        return self._release_once()

    def _hand_off(self) -> None:
        # Give the existing permit directly to the next live waiter so a new request cannot
        # overtake it.
        while self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                return
        self._busy = False

    def _release_once(self) -> Callable[[], None]:
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self._hand_off()

        return release


def prepare_private_directory(directory: Path | str, label: str) -> Path:
    """Create (mode 0700) and verify a directory that only this user may use."""
    path = Path(os.path.abspath(directory))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_mode & 0o077
        or info.st_uid != os.getuid()
    ):
        raise PermissionError(f"{label} directory must be private and owned by this user")
    return path


def open_private_lock(path: Path, label: str) -> int:
    """Open (creating 0600) a lock file, refusing symlinks and files others can touch."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise PermissionError(f"{label} lock must be a private regular file")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def lock_exclusive(
    descriptor: int, cancel: threading.Event | None = None, poll_seconds: float = _POLL_SECONDS
) -> None:
    """Take an exclusive ``flock``, polling so a cancel event can withdraw the wait."""
    while True:
        if cancel is not None and cancel.is_set():
            raise RequestCancelled("Request cancelled")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            time.sleep(poll_seconds)


def require_linux(what: str) -> None:
    if platform.system() != "Linux":
        raise RuntimeError(f"{what} requires Linux flock; no process-local fallback")


class SharedRequestAdmission:
    """Host-local, kernel-owned request lease. Never held while a tool or child is awaited."""

    def __init__(self, resource_group: str, directory: Path | str | None = None) -> None:
        if not resource_group.strip() or len(resource_group) > 200:
            raise ValueError("invalid admission resource group")
        self.resource_group = resource_group
        self.directory = Path(
            directory or os.environ.get("PITWALL_WORKBENCH_RESOURCE_DIR") or default_admission_dir()
        )

    def lock_path(self) -> Path:
        digest = hashlib.sha256(self.resource_group.encode()).hexdigest()
        return Path(os.path.abspath(self.directory)) / f"{digest}.lock"

    def acquire(
        self, cancel: threading.Event | None = None, poll_seconds: float = _POLL_SECONDS
    ) -> Callable[[], None]:
        """Block until this host grants the permit and return an idempotent release callable.

        Setting ``cancel`` while waiting raises ``RequestCancelled`` without taking the lock.
        Once granted, the permit belongs to the caller until it calls the release function:
        cancelling afterwards must not free the host lock before the transport settles.
        """
        require_linux("shared request admission")
        if cancel is not None and cancel.is_set():
            raise RequestCancelled("Request cancelled")
        prepare_private_directory(self.directory, "admission")
        descriptor = open_private_lock(self.lock_path(), "admission")
        try:
            lock_exclusive(descriptor, cancel, poll_seconds)
        except BaseException:
            os.close(descriptor)
            raise
        guard = threading.Lock()
        released = False

        def release() -> None:
            nonlocal released
            with guard:
                if released:
                    return
                released = True
                os.close(descriptor)  # closing the descriptor drops the flock

        return release
