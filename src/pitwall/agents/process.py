"""Process-group supervision with streamed and retained output."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

from .run_store import FILE_MODE

OutputCallback = Callable[[str, int], None]


@dataclass(slots=True)
class ProcessResult:
    exit_code: int
    signal: int | None
    timed_out: bool
    cancelled: bool
    wall_ms: int
    stdout_bytes: int
    stderr_bytes: int
    aborted: bool = False
    killed: bool = False


@dataclass(slots=True)
class BoundedCaptureResult:
    returncode: int
    stdout: bytes
    stderr: bytes
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        try:
            written = os.write(descriptor, view)
            view = view[written:]
        except BrokenPipeError, OSError:
            return


def _pump(
    pipe: object,
    log_path: Path,
    terminal_fd: int | None,
    channel: str,
    callback: OutputCallback | None,
    counts: dict[str, int],
) -> None:
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        while True:
            # Buffered read(n) waits for n bytes or EOF; read1 forwards available
            # progress immediately while still draining large output in chunks.
            chunk = pipe.read1(65536)  # type: ignore[attr-defined]  # reason: the pipe is typed as object; callers pass binary file objects
            if not chunk:
                break
            _write_all(descriptor, chunk)
            if terminal_fd is not None:
                _write_all(terminal_fd, chunk)
            counts[channel] += len(chunk)
            if callback is not None:
                # Output events are additive observability and must never
                # stop pipe drainage or deadlock the harness process.
                with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
                    callback(channel, len(chunk))
    finally:
        os.close(descriptor)
        pipe.close()  # type: ignore[attr-defined]  # reason: the pipe is typed as object; callers pass binary file objects


def _feed(pipe: object, content: bytes) -> None:
    try:
        pipe.write(content)  # type: ignore[attr-defined]  # reason: the pipe is typed as object; callers pass binary file objects
        pipe.flush()  # type: ignore[attr-defined]  # reason: the pipe is typed as object; callers pass binary file objects
    except BrokenPipeError, OSError:
        pass
    finally:
        with contextlib.suppress(BrokenPipeError, OSError):
            pipe.close()  # type: ignore[attr-defined]  # reason: pipe is a file-like object typed as object by the caller


def _capture_bounded(
    pipe: object,
    buffer: bytearray,
    maximum: int,
    counts: dict[str, int],
    channel: str,
) -> None:
    try:
        while True:
            chunk = pipe.read(65536)  # type: ignore[attr-defined]  # reason: the pipe is typed as object; callers pass binary file objects
            if not chunk:
                break
            counts[channel] += len(chunk)
            remaining = maximum - len(buffer)
            if remaining > 0:
                buffer.extend(chunk[:remaining])
    finally:
        pipe.close()  # type: ignore[attr-defined]  # reason: the pipe is typed as object; callers pass binary file objects


def _signal_group(process_group: int, signum: int) -> bool:
    """Send *signum* to a process group; False when there is nothing left to signal.

    ``ProcessLookupError`` means the group is gone. macOS also answers ``EPERM``
    (``PermissionError``) for a group whose members are exiting or already zombies,
    which is the same situation for our purposes: the group cannot be signalled
    any further, and treating it as an execution failure would misreport a run
    that completed normally as exit 127.
    """
    try:
        os.killpg(process_group, signum)
    except ProcessLookupError, PermissionError:
        return False
    return True


def _terminate_group(
    process: subprocess.Popen[bytes],
    grace_seconds: float = 2.0,
    *,
    stop: threading.Event | None = None,
) -> bool:
    """SIGTERM the group, wait up to *grace_seconds*, then SIGKILL; True when SIGKILL was sent.

    When *stop* is set during the grace (a Ctrl+C), the wait ends at once and SIGKILL follows.
    """
    if process.poll() is not None:
        return False
    if not _signal_group(process.pid, signal.SIGTERM):
        return False
    deadline = time.monotonic() + grace_seconds
    while process.poll() is None and time.monotonic() < deadline:
        if stop is not None and stop.is_set():
            break
        time.sleep(0.02)
    if process.poll() is None:
        return _signal_group(process.pid, signal.SIGKILL)
    return False


def _terminate_remaining_group(process_group: int, grace_seconds: float = 2.0) -> None:
    """Reap grandchildren that outlive a normally exiting group leader."""
    if not _signal_group(process_group, signal.SIGTERM):
        return
    deadline = time.monotonic() + grace_seconds
    while time.monotonic() < deadline:
        if not _signal_group(process_group, 0):
            return
        time.sleep(0.02)
    _signal_group(process_group, signal.SIGKILL)


def _reap_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate the child's group (SIGTERM, grace, SIGKILL), reap it, then its stragglers."""
    _terminate_group(process)
    process.wait()
    _terminate_remaining_group(process.pid)


class _SigintDeferral:
    """The SIGINT handler installed while a deferral is active; it only records the signal.

    The recorded state lives on the installed handler itself, so ``interrupt_pending`` can
    read it from anywhere in the process without module-level state.
    """

    def __init__(self, event: threading.Event) -> None:
        self.event = event

    def __call__(self, _signum: int, _frame: object) -> None:
        self.event.set()


def interrupt_pending() -> bool:
    """True when an active deferral has recorded a Ctrl+C its supervisor has not acted on.

    Work that runs inside a supervised run (``watch`` callbacks such as user hooks) checks
    this between steps so the supervisor can act on the Ctrl+C as soon as the current
    step ends. Outside a deferral, and on any thread but the main one, it is always False.
    """

    if threading.current_thread() is not threading.main_thread():
        # A deferral only ever belongs to the main thread's run (see below); work on
        # another thread must not act on that run's Ctrl+C.
        return False
    handler = signal.getsignal(signal.SIGINT)
    return isinstance(handler, _SigintDeferral) and handler.event.is_set()


def _defer_interactive_sigint(deferred: threading.Event) -> Callable[[], None] | None:
    """Record SIGINT in *deferred* instead of raising, and return the undo callable.

    Only the main thread with Python's default handler is changed: KeyboardInterrupt
    never reaches another thread, and an inherited SIG_IGN or a caller's own handler
    is left alone. Python handlers reset to SIG_DFL across exec, so the child is unaffected.
    """

    if threading.current_thread() is not threading.main_thread():
        return None
    if signal.getsignal(signal.SIGINT) is not signal.default_int_handler:
        return None
    previous = signal.signal(signal.SIGINT, _SigintDeferral(deferred))

    def restore() -> None:
        signal.signal(signal.SIGINT, previous)

    return restore


@contextlib.contextmanager
def _sigint_deferred() -> Iterator[threading.Event]:
    """While active, an interactive Ctrl+C only sets the yielded event (see above)."""
    deferred = threading.Event()
    restore = _defer_interactive_sigint(deferred)
    try:
        yield deferred
    finally:
        if restore is not None:
            restore()


def _start_supervised(
    argv: list[str],
    *,
    env: Mapping[str, str],
    cwd: Path | None,
    stdin_pipe: bool,
    make_threads: Callable[[subprocess.Popen[bytes]], list[threading.Thread]],
) -> tuple[subprocess.Popen[bytes], list[threading.Thread]]:
    """Start the child in its own process group, then its I/O threads.

    Callers hold ``_sigint_deferred`` around this, because a Ctrl+C between fork and
    exec would otherwise escape Popen with the child already running. If anything
    fails once the child exists (Thread.start can raise RuntimeError when threads
    run out), the child's group is reaped before the error propagates.
    """

    process = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE if stdin_pipe else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        cwd=cwd,
        # A dedicated process group is the cancellation boundary.  A new
        # session is unnecessary and macOS can reject setsid() with EPERM;
        # setpgid(0, 0) preserves group supervision without that constraint.
        process_group=0,
    )
    try:
        threads = make_threads(process)
        for thread in threads:
            thread.start()
    except BaseException:  # reason: any failure after the child exists must not orphan it
        _reap_group(process)
        raise
    return process, threads


def run_bounded_capture(
    argv: list[str],
    *,
    env: Mapping[str, str],
    timeout_seconds: float,
    max_bytes: int,
    cwd: Path | None = None,
    stdin: bytes | None = None,
) -> BoundedCaptureResult:
    """Run a command while draining both streams and retaining at most ``max_bytes`` each."""

    if max_bytes < 0:
        raise ValueError("max_bytes must be non-negative")
    stdout = bytearray()
    stderr = bytearray()
    counts = {"stdout": 0, "stderr": 0}

    def make_threads(process: subprocess.Popen[bytes]) -> list[threading.Thread]:
        assert process.stdout is not None and process.stderr is not None
        threads = [
            threading.Thread(
                target=_capture_bounded,
                args=(process.stdout, stdout, max_bytes, counts, "stdout"),
                daemon=True,
            ),
            threading.Thread(
                target=_capture_bounded,
                args=(process.stderr, stderr, max_bytes, counts, "stderr"),
                daemon=True,
            ),
        ]
        if stdin is not None:
            assert process.stdin is not None
            threads.append(threading.Thread(target=_feed, args=(process.stdin, stdin), daemon=True))
        return threads

    process: subprocess.Popen[bytes] | None = None
    timed_out = False
    try:
        with _sigint_deferred() as deferred:
            process, threads = _start_supervised(
                argv, env=env, cwd=cwd, stdin_pipe=stdin is not None, make_threads=make_threads
            )
        if deferred.is_set():
            raise KeyboardInterrupt
        try:
            process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_group(process)
            process.wait()
    except KeyboardInterrupt:
        if process is None:
            raise
        # The child runs in its own group, so the terminal's SIGINT never reaches it.
        # Reap it before the interrupt continues; these callers have no 130 contract.
        _reap_group(process)
        raise
    _terminate_remaining_group(process.pid)
    for thread in threads:
        thread.join(timeout=5)
    return BoundedCaptureResult(
        returncode=process.returncode,
        stdout=bytes(stdout),
        stderr=bytes(stderr),
        stdout_bytes=counts["stdout"],
        stderr_bytes=counts["stderr"],
        stdout_truncated=counts["stdout"] > len(stdout),
        stderr_truncated=counts["stderr"] > len(stderr),
        timed_out=timed_out,
    )


def run_process(
    argv: list[str],
    *,
    env: Mapping[str, str],
    stdin: bytes | None,
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
    cwd: Path,
    output_callback: OutputCallback | None = None,
    terminal_stdout_fd: int | None = 1,
    terminal_stderr_fd: int | None = 2,
    abort_event: threading.Event | None = None,
    abort_grace_seconds: float = 10.0,
    watch: Callable[[], None] | None = None,
    on_start: Callable[[int], None] | None = None,
) -> ProcessResult:
    """Run *argv* in its own process group until it exits, times out, or is aborted.

    *on_start*, when given, receives the child's pid (also its process-group id) once it runs.
    """
    started = time.monotonic()
    counts = {"stdout": 0, "stderr": 0}
    timed_out = False
    cancelled = False
    aborted = False
    killed = False

    def make_threads(process: subprocess.Popen[bytes]) -> list[threading.Thread]:
        assert process.stdout is not None and process.stderr is not None
        threads = [
            threading.Thread(
                target=_pump,
                args=(
                    process.stdout,
                    stdout_path,
                    terminal_stdout_fd,
                    "stdout",
                    output_callback,
                    counts,
                ),
                daemon=True,
            ),
            threading.Thread(
                target=_pump,
                args=(
                    process.stderr,
                    stderr_path,
                    terminal_stderr_fd,
                    "stderr",
                    output_callback,
                    counts,
                ),
                daemon=True,
            ),
        ]
        if stdin is not None:
            assert process.stdin is not None
            threads.append(threading.Thread(target=_feed, args=(process.stdin, stdin), daemon=True))
        return threads

    process: subprocess.Popen[bytes] | None = None
    # For the whole run, including the shutdown grace after a cancel, an interactive
    # Ctrl+C only sets *deferred*: the loop below turns it into a cancel, and a second
    # (held-key) Ctrl+C cannot cut the SIGTERM-grace-SIGKILL sequence short and orphan
    # the harness. A caller's own SIGINT handler can still raise KeyboardInterrupt.
    with _sigint_deferred() as deferred:
        try:
            process, threads = _start_supervised(
                argv, env=env, cwd=cwd, stdin_pipe=stdin is not None, make_threads=make_threads
            )
            if on_start is not None:
                try:
                    on_start(process.pid)
                except BaseException:  # reason: a failed record must not orphan the running child
                    _reap_group(process)
                    raise
            next_watch = time.monotonic()
            while process.poll() is None:
                if deferred.is_set():
                    raise KeyboardInterrupt
                now = time.monotonic()
                if now - started >= timeout_seconds:
                    timed_out = True
                    _terminate_group(process)
                    break
                if abort_event is not None and abort_event.is_set():
                    aborted = True
                    # A Ctrl+C during the abort's grace ends it at once (SIGKILL) and
                    # the run reports a cancel, not the abort's 143/137.
                    killed = _terminate_group(process, abort_grace_seconds, stop=deferred)
                    cancelled = deferred.is_set()
                    break
                if watch is not None and now >= next_watch:
                    watch()
                    next_watch = now + 1.0
                time.sleep(0.02)
        except KeyboardInterrupt:
            if process is None:
                raise
            cancelled = True
            _terminate_group(process)
        return_code = process.wait()
        _terminate_remaining_group(process.pid)
        for thread in threads:
            thread.join(timeout=5)
    # A Ctrl+C after the loop's last check (the child exited in the same tick, or it
    # landed during the final wait, the straggler reap or the joins) still cancels the
    # run, unless the run had already ended as a timeout or an abort.
    if deferred.is_set() and not (timed_out or aborted):
        cancelled = True
    child_signal = -return_code if return_code < 0 else None
    exit_code = (
        124
        if timed_out
        else 130
        if cancelled
        else 128 + child_signal
        if child_signal
        # A child that exits 0 when told to stop (Codex does) was still cancelled.
        else 128 + int(signal.SIGTERM)
        if aborted and return_code == 0
        else return_code
    )
    return ProcessResult(
        exit_code=max(0, min(255, exit_code)),
        signal=child_signal,
        timed_out=timed_out,
        cancelled=cancelled,
        wall_ms=max(0, int((time.monotonic() - started) * 1000)),
        stdout_bytes=counts["stdout"],
        stderr_bytes=counts["stderr"],
        aborted=aborted,
        killed=killed,
    )
