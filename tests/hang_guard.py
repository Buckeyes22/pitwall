"""Shared hang-guard bound and subprocess cleanup for tests.

A hang guard only stops a wedged test from blocking the suite; it is not a latency check, so it is
generous enough that machine load never trips it. A test that asserts something finishes or fires
within a bound (a timeout feature under test) keeps its own literal bound instead.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from collections.abc import Callable

HANG_GUARD_SECS = 120.0
_REAP_SECS = 10.0


def reap(process: subprocess.Popen[bytes] | subprocess.Popen[str], *, group: bool = False) -> None:
    """Kill a still-running child, then drain and close every pipe it owns.

    With ``group=True`` the whole process group is killed. Safe to call on an already-finished process and more than once. Register it with
    ``addCleanup``/``try...finally`` right after every ``Popen`` so a failing or timed-out test
    never leaves open pipe objects to be finalised (and warned about) during a later test.
    """

    if group:
        # The child was started with start_new_session=True, so its pid is its group id: this also
        # takes out a harness it forked, which would otherwise outlive it and keep writing.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
    elif process.poll() is None:
        process.kill()
    # A surviving grandchild may hold a pipe open; the streams are closed below regardless.
    with contextlib.suppress(subprocess.TimeoutExpired, ValueError, OSError):
        process.communicate(timeout=_REAP_SECS)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and not stream.closed:
            stream.close()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=_REAP_SECS)


class BackgroundAction:
    """Poll on a helper thread until *poll* acts (returns True) or the test calls ``finish``.

    The poll has no wall-clock deadline of its own: a helper that gave up on a timer while the
    code under test kept waiting (a stalled machine can outlast any timer) would leave the test
    waiting on the code's own, longer limit and then fail with a misleading result. ``finish``
    stops the poll, re-raises anything the helper raised, and fails if it never acted, so the
    real cause is reported.
    """

    def __init__(self, poll: Callable[[], bool], *, interval: float = 0.05) -> None:
        self._poll = poll
        self._interval = interval
        self._stop = threading.Event()
        self._error: BaseException | None = None
        self.acted = False
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                if self._poll():
                    self.acted = True
                    return
                self._stop.wait(self._interval)
        except BaseException as exc:  # reason: re-raised on the test thread by finish()
            self._error = exc

    def stop(self) -> None:
        """Stop polling without checking the outcome (a cleanup for a test that already failed)."""
        self._stop.set()

    def finish(self, *, require_action: bool = True) -> None:
        self._stop.set()
        self._thread.join(HANG_GUARD_SECS)
        if self._thread.is_alive():
            raise AssertionError("the background action is still running after the hang guard")
        if self._error is not None:
            raise AssertionError(f"the background action failed: {self._error!r}") from self._error
        if require_action and not self.acted:
            raise AssertionError("the background action never saw its condition")
