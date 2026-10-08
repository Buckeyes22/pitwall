"""Portable hooks receive metadata on stdin and cannot pollute shim output."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.hooks import (
    MAX_HOOK_OUTPUT_BYTES,
    HookRunner,
)
from pitwall.agents.run_store import (
    RunStore,
)
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]


class HookTests(unittest.TestCase):
    def test_hook_receives_event_and_output_is_captured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            capture = root / "captured.json"
            env = {
                "HOME": directory,
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "HOOK_CAPTURE": str(capture),
            }
            config = root / "config/pitwall/agents/hooks.json"
            config.parent.mkdir(parents=True)
            command = [
                sys.executable,
                "-c",
                "import os,sys,pathlib; pathlib.Path(os.environ['HOOK_CAPTURE']).write_bytes(sys.stdin.buffer.read()); print('hook-out')",
            ]
            config.write_text(
                json.dumps(
                    {"dispatch.failed": [{"command": command, "timeoutSeconds": HANG_GUARD_SECS}]}
                ),
                encoding="utf-8",
            )
            store = RunStore.create(env, "dispatch-one")
            event = {
                "event": "dispatch.failed",
                "dispatchId": "dispatch-one",
                "provider": "codex",
                "model": "gpt-test",
            }
            HookRunner(env)(event, store)
            self.assertEqual(event, json.loads(capture.read_text(encoding="utf-8")))
            hook_stdout = list(store.artifact("hooks").glob("*.stdout.log"))
            self.assertEqual(1, len(hook_stdout))
            self.assertEqual(b"hook-out\n", hook_stdout[0].read_bytes())

    def test_ctrl_c_during_a_supervised_hook_skips_the_remaining_hooks(self) -> None:
        # The first hook sends this process a SIGINT and then finishes its write; the
        # supervisor defers the Ctrl+C, so that hook completes, the second never starts,
        # and the run is cancelled as soon as the first hook ends. No timing is involved.
        import os
        import signal
        import threading

        from pitwall.agents.process import run_process

        if threading.current_thread() is not threading.main_thread():
            self.skipTest("SIGINT is delivered to the main thread only")
        self.addCleanup(signal.signal, signal.SIGINT, signal.getsignal(signal.SIGINT))
        signal.signal(signal.SIGINT, signal.default_int_handler)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first-hook-done", root / "second-hook-ran"
            env = {
                "HOME": directory,
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "SUPERVISOR_PID": str(os.getpid()),
            }
            config = root / "config/pitwall/agents/hooks.json"
            config.parent.mkdir(parents=True)
            interrupting = (
                "import os, pathlib, signal\n"
                "os.kill(int(os.environ['SUPERVISOR_PID']), signal.SIGINT)\n"
                f"pathlib.Path({str(first)!r}).write_text('whole')\n"
            )
            hooks = [
                {
                    "command": [sys.executable, "-c", interrupting],
                    "timeoutSeconds": HANG_GUARD_SECS,
                },
                {
                    "command": [sys.executable, "-c", f"open({str(second)!r}, 'w').close()"],
                    "timeoutSeconds": HANG_GUARD_SECS,
                },
            ]
            config.write_text(json.dumps({"steer.unacked": hooks}), encoding="utf-8")
            store = RunStore.create(env, "dispatch-one")
            event = {
                "event": "steer.unacked",
                "dispatchId": "dispatch-one",
                "provider": "codex",
                "model": "gpt-test",
            }
            runner = HookRunner(env)
            fired: list[bool] = []

            def watch() -> None:
                if not fired:
                    fired.append(True)
                    runner(event, store)

            captured = io.StringIO()
            with contextlib.redirect_stderr(captured):
                result = run_process(
                    [sys.executable, "-c", f"import time; time.sleep({2 * HANG_GUARD_SECS!r})"],
                    env=dict(os.environ),
                    stdin=None,
                    stdout_path=root / "o",
                    stderr_path=root / "e",
                    timeout_seconds=HANG_GUARD_SECS,
                    cwd=root,
                    watch=watch,
                )
            self.assertEqual((True, 130), (result.cancelled, result.exit_code))
            self.assertEqual("whole", first.read_text())
            self.assertFalse(second.exists(), "a hook ran after the Ctrl+C")
            # The skipped hook leaves a status file beside the run hook's own, and the
            # runner says on stderr how many hooks it skipped.
            statuses = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in store.artifact("hooks").glob("steer.unacked-*.json")
            ]
            self.assertEqual(2, len(statuses))
            self.assertEqual([{"skipped": "interrupted"}], [s for s in statuses if "skipped" in s])
            self.assertIn("skipped 1 steer.unacked hook(s) after Ctrl+C", captured.getvalue())

    def test_invalid_hook_config_is_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config/pitwall/agents/hooks.json"
            config.parent.mkdir(parents=True)
            config.write_text("not-json", encoding="utf-8")
            env = {
                "HOME": directory,
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            store = RunStore.create(env, "dispatch-one")
            HookRunner(env)(
                {
                    "event": "dispatch.failed",
                    "dispatchId": "dispatch-one",
                    "provider": "x",
                    "model": "x",
                },
                store,
            )

    def test_hook_output_is_memory_bounded_and_reports_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = {
                "HOME": directory,
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
            }
            config = root / "config/pitwall/agents/hooks.json"
            config.parent.mkdir(parents=True)
            command = [
                sys.executable,
                "-c",
                "import os; os.write(1, b'x' * (2 * 1024 * 1024))",
            ]
            config.write_text(
                json.dumps({"dispatch.failed": [{"command": command}]}),
                encoding="utf-8",
            )
            store = RunStore.create(env, "dispatch-one")
            HookRunner(env)(
                {
                    "event": "dispatch.failed",
                    "dispatchId": "dispatch-one",
                    "provider": "codex",
                    "model": "gpt-test",
                },
                store,
            )
            stdout_path = next(store.artifact("hooks").glob("*.stdout.log"))
            status_path = next(store.artifact("hooks").glob("*.json"))
            self.assertEqual(MAX_HOOK_OUTPUT_BYTES, stdout_path.stat().st_size)
            status = json.loads(status_path.read_text(encoding="utf-8"))
            self.assertEqual(2 * 1024 * 1024, status["stdoutBytes"])
            self.assertTrue(status["stdoutTruncated"])

    def test_invalid_hook_depth_is_fail_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config/pitwall/agents/hooks.json"
            config.parent.mkdir(parents=True)
            config.write_text(
                json.dumps({"dispatch.created": [{"command": ["/bin/true"]}]}), encoding="utf-8"
            )
            env = {
                "HOME": directory,
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "PITWALL_AGENTS_HOOK_DEPTH": "not-a-number",
            }
            store = RunStore.create(env, "dispatch-one")
            HookRunner(env)(
                {
                    "event": "dispatch.created",
                    "dispatchId": "dispatch-one",
                    "provider": "x",
                    "model": "x",
                },
                store,
            )


if __name__ == "__main__":
    unittest.main()
