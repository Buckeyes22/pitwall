"""Process supervision streams output and terminates process groups."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pitwall.agents.process as process_module
from pitwall.agents.process import (
    ProcessResult,
    run_bounded_capture,
    run_process,
)
from tests.hang_guard import HANG_GUARD_SECS, BackgroundAction, reap

ROOT = Path(__file__).resolve().parents[2]


class ProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_output_reaches_only_the_logs_without_terminal_fds(self) -> None:
        result = run_process(
            [sys.executable, "-c", "import sys; sys.stdout.write('out'); sys.stderr.write('err')"],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=HANG_GUARD_SECS,
            cwd=self.root,
            terminal_stdout_fd=None,
            terminal_stderr_fd=None,
        )
        self.assertEqual(0, result.exit_code)
        self.assertEqual(
            (b"out", b"err"), ((self.root / "o").read_bytes(), (self.root / "e").read_bytes())
        )

    def test_abort_event_terminates_the_group_gracefully(self) -> None:
        import threading

        abort = threading.Event()
        threading.Timer(0.3, abort.set).start()
        calls: list[float] = []
        result = run_process(
            [sys.executable, "-c", f"import time; time.sleep({2 * HANG_GUARD_SECS!r})"],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=HANG_GUARD_SECS,
            cwd=self.root,
            abort_event=abort,
            abort_grace_seconds=HANG_GUARD_SECS,
            watch=lambda: calls.append(1),
        )
        self.assertEqual((True, False, 143), (result.aborted, result.killed, result.exit_code))

    def test_abort_escalates_to_sigkill_after_the_grace(self) -> None:
        import threading

        # SIG_IGN must already be installed when the abort fires, or the child
        # dies from SIGTERM before it can ignore it; wait for a readiness file
        # the child writes right after installing the handler.
        ready = self.root / "ready"
        code = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"open({str(ready)!r}, 'w').close()\n"
            f"time.sleep({2 * HANG_GUARD_SECS!r})\n"
        )
        abort = threading.Event()
        result = run_process(
            [sys.executable, "-c", code],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=HANG_GUARD_SECS,
            cwd=self.root,
            abort_event=abort,
            abort_grace_seconds=0.5,
            watch=lambda: abort.set() if ready.exists() else None,
        )
        self.assertEqual((True, True, 137), (result.aborted, result.killed, result.exit_code))

    def test_small_output_reaches_logs_and_terminal_before_child_can_continue(self) -> None:
        # Each stream must be observable while the producer is still alive, even
        # without a newline. The child cannot exit until callbacks acknowledge it.
        code = (
            "import os, pathlib, time\n"
            "for fd, name, data in [(1, 'stdout', b'progress'), (2, 'stderr', b'warning')]:\n"
            "    os.write(fd, data)\n"
            "    while not pathlib.Path(name + '.ack').exists(): time.sleep(0.01)\n"
            "os.write(1, b'x' * 200000)\n"
            "os.write(2, b'y' * 200000)\n"
        )
        observed: dict[str, bool] = {}
        with tempfile.TemporaryFile() as terminal_out, tempfile.TemporaryFile() as terminal_err:
            terminals = {"stdout": terminal_out, "stderr": terminal_err}

            def acknowledge(channel: str, _count: int) -> None:
                if channel in observed:
                    return
                expected = b"progress" if channel == "stdout" else b"warning"
                retained = (self.root / (channel + ".log")).read_bytes()
                forwarded = os.pread(terminals[channel].fileno(), len(expected), 0)
                observed[channel] = retained == forwarded == expected
                if observed[channel]:
                    (self.root / (channel + ".ack")).touch()

            result = run_process(
                [sys.executable, "-c", code],
                env=os.environ,
                stdin=None,
                stdout_path=self.root / "stdout.log",
                stderr_path=self.root / "stderr.log",
                timeout_seconds=HANG_GUARD_SECS,
                cwd=self.root,
                output_callback=acknowledge,
                terminal_stdout_fd=terminal_out.fileno(),
                terminal_stderr_fd=terminal_err.fileno(),
            )
            self.assertEqual(0, result.exit_code)
            self.assertEqual({"stdout": True, "stderr": True}, observed)
            for channel, expected in (
                ("stdout", b"progress" + b"x" * 200000),
                ("stderr", b"warning" + b"y" * 200000),
            ):
                self.assertEqual(expected, (self.root / (channel + ".log")).read_bytes())
                self.assertEqual(expected, os.pread(terminals[channel].fileno(), len(expected), 0))
            self.assertEqual(200008, result.stdout_bytes)
            self.assertEqual(200007, result.stderr_bytes)

    def test_bounded_capture_drains_but_retains_only_the_declared_limit(self) -> None:
        payload_size = 512 * 1024
        limit = 4096
        result = run_bounded_capture(
            [
                sys.executable,
                "-c",
                (
                    "import os; "
                    f"os.write(1, b'x' * {payload_size}); "
                    f"os.write(2, b'y' * {payload_size})"
                ),
            ],
            env=os.environ,
            timeout_seconds=HANG_GUARD_SECS,
            max_bytes=limit,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(limit, len(result.stdout))
        self.assertEqual(limit, len(result.stderr))
        self.assertEqual(payload_size, result.stdout_bytes)
        self.assertEqual(payload_size, result.stderr_bytes)
        self.assertTrue(result.stdout_truncated)
        self.assertTrue(result.stderr_truncated)

    def test_bounded_capture_reaps_its_child_when_interrupted_and_re_raises(self) -> None:
        # The interrupt is injected inside Thread.start, so no timing is involved.
        import threading

        spawned: list[subprocess.Popen[bytes]] = []
        real_popen = process_module.subprocess.Popen
        real_start = threading.Thread.start

        def popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(*args, **kwargs)  # type: ignore[call-overload]  # reason: forwards run_bounded_capture's own Popen arguments
            spawned.append(process)
            return process

        def start(thread: threading.Thread) -> None:
            real_start(thread)
            raise KeyboardInterrupt

        self.addCleanup(lambda: [reap(process, group=True) for process in spawned])
        with (
            mock.patch.object(process_module.subprocess, "Popen", popen),
            mock.patch.object(threading.Thread, "start", start),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_bounded_capture(
                [sys.executable, "-c", f"import time; time.sleep({2 * HANG_GUARD_SECS!r})"],
                env=os.environ,
                timeout_seconds=HANG_GUARD_SECS,
                max_bytes=64,
                cwd=self.root,
            )
        self.assertEqual(1, len(spawned))
        self.assertIsNotNone(spawned[0].returncode)
        with self.assertRaises(ProcessLookupError):
            os.killpg(spawned[0].pid, 0)

    def test_timeout_returns_124_and_retains_both_streams(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = (root / "stdout.log", root / "stderr.log")
            frozen = time.monotonic()

            def clock() -> float:
                # The timeout clock runs only once the child has written both streams, so the
                # kill always follows the output it must retain, however slowly the child starts.
                if all(path.is_file() and path.stat().st_size for path in logs):
                    return time.monotonic()
                return frozen

            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                with mock.patch.object(
                    process_module, "time", SimpleNamespace(monotonic=clock, sleep=time.sleep)
                ):
                    result = run_process(
                        [
                            sys.executable,
                            "-c",
                            f"import sys,time; print('out', flush=True); print('err', file=sys.stderr, flush=True); time.sleep({2 * HANG_GUARD_SECS!r})",
                        ],
                        env=os.environ,
                        stdin=None,
                        stdout_path=logs[0],
                        stderr_path=logs[1],
                        timeout_seconds=0.2,
                        cwd=root,
                        terminal_stdout_fd=devnull,
                        terminal_stderr_fd=devnull,
                    )
            finally:
                os.close(devnull)
            self.assertEqual(124, result.exit_code)
            self.assertTrue(result.timed_out)
            self.assertEqual(b"out\n", (root / "stdout.log").read_bytes())
            self.assertEqual(b"err\n", (root / "stderr.log").read_bytes())

    def test_signaled_child_exit_is_capped_at_255(self) -> None:
        fake_process = mock.Mock()
        fake_process.pid = 2_147_483_647
        fake_process.stdout = tempfile.TemporaryFile()  # noqa: SIM115  # reason: the process runner under test closes the pipes
        fake_process.stderr = tempfile.TemporaryFile()  # noqa: SIM115  # reason: the process runner under test closes the pipes
        fake_process.stdin = None
        fake_process.poll.return_value = -200
        fake_process.wait.return_value = -200
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                process_module.subprocess,
                "Popen",
                return_value=fake_process,
            ),
            mock.patch.object(process_module, "_terminate_remaining_group"),
        ):
            root = Path(directory)
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                result = run_process(
                    ["synthetic-signaled-child"],
                    env={},
                    stdin=None,
                    stdout_path=root / "stdout.log",
                    stderr_path=root / "stderr.log",
                    timeout_seconds=1,
                    cwd=root,
                    terminal_stdout_fd=devnull,
                    terminal_stderr_fd=devnull,
                )
            finally:
                os.close(devnull)
        self.assertEqual(255, result.exit_code)
        self.assertEqual(200, result.signal)
        self.assertFalse(result.timed_out)
        self.assertFalse(result.cancelled)

    def test_abort_of_a_child_that_exits_zero_on_sigterm_reports_143(self) -> None:
        import threading

        abort = threading.Event()
        ready = self.root / "ready"

        def abort_once_ready() -> bool:
            # Abort only after the child's SIGTERM handler is installed, so the child exits 0
            # and the result comes from the aborted-zero branch, not from a SIGTERM death.
            if not ready.exists():
                return False
            abort.set()
            return True

        helper = BackgroundAction(abort_once_ready)
        self.addCleanup(helper.stop)
        script = (
            "import pathlib, signal, sys, time\n"
            "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))\n"
            f"pathlib.Path({str(ready)!r}).touch()\n"
            f"time.sleep({2 * HANG_GUARD_SECS!r})\n"
        )
        result = run_process(
            [sys.executable, "-c", script],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=HANG_GUARD_SECS,
            cwd=self.root,
            abort_event=abort,
            abort_grace_seconds=HANG_GUARD_SECS,
        )
        helper.finish()
        self.assertEqual((True, False, 143), (result.aborted, result.killed, result.exit_code))
        self.assertIsNone(result.signal)

    def _run_interrupted(self, *, during: str) -> tuple[ProcessResult, subprocess.Popen[bytes]]:
        """Deliver a real SIGINT at one exact point inside run_process, with no timing involved.

        ``during="popen"`` signals before Popen returns (the fork-to-exec window);
        ``during="thread_start"`` signals inside Thread.start (it blocks until the new thread
        is scheduled). The default SIGINT handler is installed, as in an interactive run.
        """
        import threading

        if threading.current_thread() is not threading.main_thread():
            self.skipTest("SIGINT is delivered to the main thread only")
        self.addCleanup(signal.signal, signal.SIGINT, signal.getsignal(signal.SIGINT))
        signal.signal(signal.SIGINT, signal.default_int_handler)
        spawned: list[subprocess.Popen[bytes]] = []
        real_popen = process_module.subprocess.Popen
        real_start = threading.Thread.start

        def popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(*args, **kwargs)  # type: ignore[call-overload]  # reason: forwards run_process's own Popen arguments
            spawned.append(process)
            if during == "popen":
                signal.raise_signal(signal.SIGINT)
            return process

        def start(thread: threading.Thread) -> None:
            real_start(thread)
            if during == "thread_start":
                signal.raise_signal(signal.SIGINT)

        self.addCleanup(lambda: [reap(process, group=True) for process in spawned])
        escaped = False
        result: ProcessResult | None = None
        with (
            mock.patch.object(process_module.subprocess, "Popen", popen),
            mock.patch.object(threading.Thread, "start", start),
        ):
            try:
                result = run_process(
                    [sys.executable, "-c", f"import time; time.sleep({2 * HANG_GUARD_SECS!r})"],
                    env=dict(os.environ),
                    stdin=None,
                    stdout_path=self.root / "o",
                    stderr_path=self.root / "e",
                    timeout_seconds=HANG_GUARD_SECS,
                    cwd=self.root,
                )
            except KeyboardInterrupt:
                escaped = True
        self.assertFalse(escaped, "KeyboardInterrupt escaped run_process")
        assert result is not None
        self.assertEqual(1, len(spawned))
        return result, spawned[0]

    def _assert_cancelled_and_reaped(
        self, result: ProcessResult, harness: subprocess.Popen[bytes]
    ) -> None:
        self.assertEqual((True, 130), (result.cancelled, result.exit_code))
        self.assertIsNotNone(harness.returncode)
        with self.assertRaises(ProcessLookupError):
            os.killpg(harness.pid, 0)

    def test_ctrl_c_inside_popen_cancels_and_reaps_the_harness(self) -> None:
        self._assert_cancelled_and_reaped(*self._run_interrupted(during="popen"))

    def test_ctrl_c_while_output_threads_start_cancels_and_reaps_the_harness(self) -> None:
        self._assert_cancelled_and_reaped(*self._run_interrupted(during="thread_start"))

    def _interactive_sigint(self) -> None:
        import threading

        if threading.current_thread() is not threading.main_thread():
            self.skipTest("SIGINT is delivered to the main thread only")
        self.addCleanup(signal.signal, signal.SIGINT, signal.getsignal(signal.SIGINT))
        signal.signal(signal.SIGINT, signal.default_int_handler)

    def _recording_popen(
        self, after: Callable[[], None] | None = None
    ) -> tuple[list[subprocess.Popen[bytes]], Callable[..., subprocess.Popen[bytes]]]:
        spawned: list[subprocess.Popen[bytes]] = []
        real_popen = process_module.subprocess.Popen

        def popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(*args, **kwargs)  # type: ignore[call-overload]  # reason: forwards the runner's own Popen arguments
            spawned.append(process)
            if after is not None:
                after()
            return process

        self.addCleanup(lambda: [reap(process, group=True) for process in spawned])
        return spawned, popen

    def _assert_group_gone(self, harness: subprocess.Popen[bytes]) -> None:
        self.assertIsNotNone(harness.returncode)
        with self.assertRaises(ProcessLookupError):
            os.killpg(harness.pid, 0)

    def test_second_ctrl_c_during_the_shutdown_grace_still_force_kills_and_reports_130(
        self,
    ) -> None:
        # The harness ignores SIGTERM, so cancelling it runs the whole grace and then needs
        # SIGKILL. The first SIGINT comes from the supervisor's own watch once the harness
        # is ready; the second is raised by the grace wait itself. No timing is involved.
        self._interactive_sigint()
        ready = self.root / "ready"
        code = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"open({str(ready)!r}, 'w').close()\n"
            f"time.sleep({2 * HANG_GUARD_SECS!r})\n"
        )
        spawned, popen = self._recording_popen()
        sent: list[str] = []
        real_terminate = process_module._terminate_group
        real_time = process_module.time

        def watch() -> None:
            if ready.exists() and not sent:
                sent.append("first")
                signal.raise_signal(signal.SIGINT)

        def terminate(process: subprocess.Popen[bytes], grace_seconds: float = 2.0) -> bool:
            sent.append("grace")
            return real_terminate(process, grace_seconds)

        def sleep(seconds: float) -> None:
            if sent[-1:] == ["grace"]:
                sent.append("second")
                signal.raise_signal(signal.SIGINT)
            real_time.sleep(seconds)

        escaped = False
        result: ProcessResult | None = None
        with (
            mock.patch.object(process_module.subprocess, "Popen", popen),
            mock.patch.object(process_module, "_terminate_group", terminate),
            mock.patch.object(
                process_module, "time", SimpleNamespace(monotonic=real_time.monotonic, sleep=sleep)
            ),
        ):
            try:
                result = run_process(
                    [sys.executable, "-c", code],
                    env=dict(os.environ),
                    stdin=None,
                    stdout_path=self.root / "o",
                    stderr_path=self.root / "e",
                    timeout_seconds=HANG_GUARD_SECS,
                    cwd=self.root,
                    watch=watch,
                )
            except KeyboardInterrupt:
                escaped = True
        self.assertFalse(escaped, "the second Ctrl+C escaped run_process")
        self.assertEqual(["first", "grace", "second"], sent)
        assert result is not None
        self.assertEqual((True, 130), (result.cancelled, result.exit_code))
        self.assertEqual(-signal.SIGKILL, spawned[0].returncode)
        self._assert_group_gone(spawned[0])

    def test_ctrl_c_during_cleanup_after_the_child_exits_still_reports_130(self) -> None:
        # The child exits on its own; the SIGINT is raised from the leftover-group cleanup,
        # after the wait loop's last check, so only a post-run check can see it.
        self._interactive_sigint()
        real_cleanup = process_module._terminate_remaining_group

        def cleanup(process_group: int, grace_seconds: float = 2.0) -> None:
            signal.raise_signal(signal.SIGINT)
            real_cleanup(process_group, grace_seconds)

        with mock.patch.object(process_module, "_terminate_remaining_group", cleanup):
            result = run_process(
                [sys.executable, "-c", "pass"],
                env=dict(os.environ),
                stdin=None,
                stdout_path=self.root / "o",
                stderr_path=self.root / "e",
                timeout_seconds=HANG_GUARD_SECS,
                cwd=self.root,
            )
        self.assertEqual((True, 130), (result.cancelled, result.exit_code))

    def test_ctrl_c_during_an_abort_grace_ends_it_at_once_and_reports_130(self) -> None:
        # The harness ignores SIGTERM and the abort grace is far longer than the hang guard,
        # so only a Ctrl+C that cuts the grace short lets the run return in time.
        import threading

        self._interactive_sigint()
        ready = self.root / "ready"
        code = (
            "import signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"open({str(ready)!r}, 'w').close()\n"
            f"time.sleep({4 * HANG_GUARD_SECS!r})\n"
        )
        long_grace = 2 * HANG_GUARD_SECS
        spawned, popen = self._recording_popen()
        abort = threading.Event()
        grace_started: list[float] = []
        real_terminate = process_module._terminate_group
        real_time = process_module.time

        def terminate(
            process: subprocess.Popen[bytes], grace_seconds: float = 2.0, **kwargs: object
        ) -> bool:
            if grace_seconds == long_grace:
                grace_started.append(real_time.monotonic())
            return real_terminate(process, grace_seconds, **kwargs)  # type: ignore[arg-type]  # reason: forwards the runner's own keyword arguments

        def sleep(seconds: float) -> None:
            if len(grace_started) == 1:
                grace_started.append(-1.0)
                signal.raise_signal(signal.SIGINT)
            real_time.sleep(seconds)

        with (
            mock.patch.object(process_module.subprocess, "Popen", popen),
            mock.patch.object(process_module, "_terminate_group", terminate),
            mock.patch.object(
                process_module, "time", SimpleNamespace(monotonic=real_time.monotonic, sleep=sleep)
            ),
        ):
            result = run_process(
                [sys.executable, "-c", code],
                env=dict(os.environ),
                stdin=None,
                stdout_path=self.root / "o",
                stderr_path=self.root / "e",
                timeout_seconds=4 * HANG_GUARD_SECS,
                cwd=self.root,
                abort_event=abort,
                abort_grace_seconds=long_grace,
                watch=lambda: abort.set() if ready.exists() else None,
            )
        returned = real_time.monotonic()
        self.assertEqual(2, len(grace_started), "the abort grace never began")
        self.assertLess(returned - grace_started[0], HANG_GUARD_SECS)
        self.assertEqual((True, True, 130), (result.aborted, result.cancelled, result.exit_code))
        self._assert_group_gone(spawned[0])

    def test_interrupt_pending_is_false_off_the_main_thread(self) -> None:
        # A pending Ctrl+C belongs to the main thread's run; another thread never sees it.
        import threading

        self._interactive_sigint()
        seen: dict[str, bool] = {}
        with process_module._sigint_deferred() as deferred:
            signal.raise_signal(signal.SIGINT)
            self.assertTrue(deferred.is_set())
            seen["main"] = process_module.interrupt_pending()
            worker = threading.Thread(
                target=lambda: seen.__setitem__("worker", process_module.interrupt_pending())
            )
            worker.start()
            worker.join(HANG_GUARD_SECS)
            self.assertFalse(worker.is_alive())
        self.assertEqual({"main": True, "worker": False}, seen)
        self.assertFalse(process_module.interrupt_pending())

    def _start_fails(self) -> Callable[[object], None]:
        def start(_thread: object) -> None:
            raise RuntimeError("can't start new thread")

        return start

    def test_a_thread_start_failure_reaps_the_harness_before_propagating(self) -> None:
        import threading

        spawned, popen = self._recording_popen()
        with (
            mock.patch.object(process_module.subprocess, "Popen", popen),
            mock.patch.object(threading.Thread, "start", self._start_fails()),
            self.assertRaisesRegex(RuntimeError, "can't start new thread"),
        ):
            run_process(
                [sys.executable, "-c", f"import time; time.sleep({2 * HANG_GUARD_SECS!r})"],
                env=dict(os.environ),
                stdin=None,
                stdout_path=self.root / "o",
                stderr_path=self.root / "e",
                timeout_seconds=HANG_GUARD_SECS,
                cwd=self.root,
            )
        self._assert_group_gone(spawned[0])

    def test_bounded_capture_ctrl_c_inside_popen_reaps_its_child_and_re_raises(self) -> None:
        self._interactive_sigint()
        spawned, popen = self._recording_popen(after=lambda: signal.raise_signal(signal.SIGINT))
        with (
            mock.patch.object(process_module.subprocess, "Popen", popen),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_bounded_capture(
                [sys.executable, "-c", f"import time; time.sleep({2 * HANG_GUARD_SECS!r})"],
                env=os.environ,
                timeout_seconds=HANG_GUARD_SECS,
                max_bytes=64,
                cwd=self.root,
            )
        self.assertEqual(1, len(spawned))
        self._assert_group_gone(spawned[0])

    def test_bounded_capture_thread_start_failure_reaps_its_child(self) -> None:
        import threading

        spawned, popen = self._recording_popen()
        with (
            mock.patch.object(process_module.subprocess, "Popen", popen),
            mock.patch.object(threading.Thread, "start", self._start_fails()),
            self.assertRaisesRegex(RuntimeError, "can't start new thread"),
        ):
            run_bounded_capture(
                [sys.executable, "-c", f"import time; time.sleep({2 * HANG_GUARD_SECS!r})"],
                env=os.environ,
                timeout_seconds=HANG_GUARD_SECS,
                max_bytes=64,
                cwd=self.root,
            )
        self._assert_group_gone(spawned[0])

    def test_sigint_handler_is_restored_after_the_run(self) -> None:
        self.addCleanup(signal.signal, signal.SIGINT, signal.getsignal(signal.SIGINT))
        signal.signal(signal.SIGINT, signal.default_int_handler)
        run_process(
            [sys.executable, "-c", "pass"],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=HANG_GUARD_SECS,
            cwd=self.root,
        )
        self.assertIs(signal.default_int_handler, signal.getsignal(signal.SIGINT))

    def test_unaborted_zero_exit_stays_zero(self) -> None:
        result = run_process(
            [sys.executable, "-c", "pass"],
            env=dict(os.environ),
            stdin=None,
            stdout_path=self.root / "o",
            stderr_path=self.root / "e",
            timeout_seconds=HANG_GUARD_SECS,
            cwd=self.root,
        )
        self.assertEqual((False, 0), (result.aborted, result.exit_code))


if __name__ == "__main__":
    unittest.main()


class GroupSignallingTests(unittest.TestCase):
    def test_eperm_from_killpg_means_nothing_left_to_signal(self) -> None:
        # macOS returns EPERM for a group whose members are exiting; that must not surface as
        # an OSError (which dispatch reports as exit 127) after a run that completed normally.
        calls: list[int] = []

        def killpg(_pgid: int, signum: int) -> None:
            calls.append(signum)
            raise PermissionError(1, "Operation not permitted")

        with mock.patch.object(process_module.os, "killpg", killpg):
            process_module._terminate_remaining_group(4242, grace_seconds=0.05)
        self.assertEqual([signal.SIGTERM], calls)

    def test_group_that_disappears_during_grace_is_not_killed(self) -> None:
        calls: list[int] = []
        alive = {"n": 2}

        def killpg(_pgid: int, signum: int) -> None:
            calls.append(signum)
            if signum == 0:
                alive["n"] -= 1
                if alive["n"] <= 0:
                    raise ProcessLookupError()

        with mock.patch.object(process_module.os, "killpg", killpg):
            process_module._terminate_remaining_group(4242, grace_seconds=1.0)
        self.assertEqual(signal.SIGTERM, calls[0])
        self.assertNotIn(signal.SIGKILL, calls)
