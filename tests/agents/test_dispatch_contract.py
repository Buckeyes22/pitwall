"""Additive runtime behavior above the frozen v0.2 shim contract."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path

from pitwall.agents.dispatch import (
    Lifecycle,
    _ledger_record,
)
from pitwall.agents.events import (
    EventEmitter,
)
from pitwall.agents.pids import pid_alive
from pitwall.agents.result import (
    validate_result,
)
from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.shim_test_support import PITWALL, SHIMS, ShimSandbox
from tests.hang_guard import HANG_GUARD_SECS, reap

ROOT = Path(__file__).resolve().parents[2]


class DispatchContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandboxes: list[ShimSandbox] = []

    def tearDown(self) -> None:
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def sandbox(self) -> ShimSandbox:
        value = ShimSandbox()
        self.sandboxes.append(value)
        return value

    def test_ledger_rows_carry_route_and_schema_version_4(self) -> None:
        sandbox = self.sandbox()
        env = sandbox.environment()
        plain = _ledger_record(
            env,
            dispatch_id="00000000-0000-4000-8000-000000000001",
            harness="qwen",
            model="m",
            event="started",
        )
        routed = _ledger_record(
            env,
            dispatch_id="00000000-0000-4000-8000-000000000002",
            harness="qwen",
            model="m",
            event="started",
            route="glimmer@qwen",
        )
        self.assertEqual((4, None), (plain["schema_version"], plain["route"]))
        self.assertEqual("glimmer@qwen", routed["route"])
        self.assertEqual([plain, routed], sandbox.ledger_records())

    def test_prompt_retention_is_explicit_and_runs_cli_can_inspect_it(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt("retained prompt\n")
        env = sandbox.environment()
        result = sandbox.run("codex", [str(prompt), "--routing-retain-prompt"], env=env)
        self.assertEqual(0, result.returncode)
        run = sandbox.run_directories()[0]
        self.assertEqual(b"retained prompt\n", (run / "prompt.md").read_bytes())

        listed = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "list",
            ],
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(0, listed.returncode)
        self.assertIn(run.name, listed.stdout.decode())
        shown = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "show",
                run.name[:8],
            ],
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual("succeeded", json.loads(shown.stdout)["status"])
        logs = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "logs",
                run.name[:8],
                "--channel",
                "both",
            ],
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(0, logs.returncode)
        self.assertEqual(b"fake-harness\n", logs.stdout)
        cleaned = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "cleanup",
                "--all",
            ],
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(0, cleaned.returncode)
        self.assertIn(run.name, cleaned.stdout.decode())
        self.assertEqual([], sandbox.run_directories())

    def test_file_delivery_passes_a_private_path_and_cleans_up(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("qwen")
        # stdin source: the runtime must materialize a delivery file
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, 'runtime'); from tests.agents.dispatch_file_support import run; raise SystemExit(run())",
            ],
            input=b"stdin body\n",
            env={**sandbox.environment(), "PYTHONPATH": str(ROOT)},
            cwd=ROOT,
            capture_output=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        run = sandbox.run_directories()[0]
        self.assertFalse((run / "prompt.deliver.md").exists())
        request = json.loads((run / "request.json").read_text(encoding="utf-8"))
        self.assertEqual("file", request["promptSource"]["delivery"])
        args = sandbox.captured_args()
        self.assertTrue(args[-1].endswith("prompt.deliver.md"), args)
        self.assertEqual(b"", sandbox.captured_stdin())

    def test_direct_dispatch_command_uses_the_same_contract(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        result = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(prompt),
            ],
            capture_output=True,
            env=sandbox.environment(),
            check=False,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"fake-harness\n\nSHIM-DONE exit=0\n", result.stdout)

    def test_isolated_dispatch_retains_changes_until_explicit_apply(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        repository = sandbox.root / "repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.com"], cwd=repository, check=True
        )
        subprocess.run(["git", "config", "user.name", "Dispatch Tests"], cwd=repository, check=True)
        (repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repository, check=True)
        env = sandbox.environment(
            FAKE_WRITE_RELATIVE="generated.txt", FAKE_WRITE_CONTENT="isolated\n"
        )

        dispatched = subprocess.run(
            [
                "/bin/bash",
                str(SHIMS["codex"]),
                str(prompt),
                "--routing-workspace",
                "isolated",
                "--routing-task-mode",
                "write",
            ],
            cwd=repository,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(0, dispatched.returncode, dispatched.stderr.decode(errors="replace"))
        self.assertFalse((repository / "generated.txt").exists())
        run = sandbox.run_directories()[0]
        result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("isolated", result["workspace"]["mode"])
        self.assertTrue((run / "changes.patch").is_file())
        self.assertEqual(
            "isolated\n",
            (Path(result["workspace"]["path"]) / "generated.txt").read_text(encoding="utf-8"),
        )

        applied = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "apply",
                run.name,
                "--target",
                str(repository),
            ],
            cwd=repository,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(0, applied.returncode, applied.stderr.decode(errors="replace"))
        self.assertEqual("isolated\n", (repository / "generated.txt").read_text(encoding="utf-8"))
        applied_result = json.loads((run / "result.json").read_text(encoding="utf-8"))
        validate_result(applied_result)
        self.assertEqual("succeeded", applied_result["status"])
        self.assertEqual("applied", applied_result["integration"]["status"])

    def test_agy_receives_isolated_workspace_and_records_default_effort(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("agy")
        prompt = sandbox.prompt()
        repository = sandbox.root / "agy-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.com"], cwd=repository, check=True
        )
        subprocess.run(["git", "config", "user.name", "Dispatch Tests"], cwd=repository, check=True)
        (repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repository, check=True)

        dispatched = subprocess.run(
            [
                "/bin/bash",
                str(SHIMS["agy"]),
                str(prompt),
                "--routing-workspace",
                "isolated",
                "--routing-task-mode",
                "write",
            ],
            cwd=repository,
            capture_output=True,
            env=sandbox.environment(),
            check=False,
        )
        self.assertEqual(0, dispatched.returncode, dispatched.stderr.decode(errors="replace"))
        result = json.loads(
            (sandbox.run_directories()[0] / "result.json").read_text(encoding="utf-8")
        )
        args = sandbox.captured_args()
        self.assertEqual(result["workspace"]["path"], args[args.index("--add-dir") + 1])
        self.assertEqual("medium", result["effort"])

    def test_auto_workspace_without_task_mode_is_a_usage_error(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        result = sandbox.run("codex", [str(prompt), "--routing-workspace", "auto"])
        self.assertEqual(64, result.returncode)
        self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
        self.assertEqual([], sandbox.ledger_records())
        self.assertEqual([], sandbox.run_directories())

    def test_failed_isolated_harness_still_captures_useful_changes(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        repository = sandbox.root / "failed-repository"
        repository.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
        subprocess.run(
            ["git", "config", "user.email", "tests@example.com"], cwd=repository, check=True
        )
        subprocess.run(["git", "config", "user.name", "Dispatch Tests"], cwd=repository, check=True)
        (repository / "base.txt").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "base.txt"], cwd=repository, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repository, check=True)
        env = sandbox.environment(FAKE_WRITE_RELATIVE="partial.txt", FAKE_EXIT="3")
        result = subprocess.run(
            [
                "/bin/bash",
                str(SHIMS["codex"]),
                str(prompt),
                "--routing-workspace=isolated",
                "--routing-task-mode=write",
            ],
            cwd=repository,
            capture_output=True,
            env=env,
            check=False,
        )
        self.assertEqual(3, result.returncode)
        run = sandbox.run_directories()[0]
        terminal = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", terminal["status"])
        self.assertIn(b"partial.txt", (run / "changes.patch").read_bytes())
        self.assertTrue((Path(terminal["workspace"]["path"]) / "partial.txt").is_file())

    def test_terminal_state_cannot_transition_back_to_running(self) -> None:
        sandbox = self.sandbox()
        store = RunStore.create(sandbox.environment(), "00000000-0000-4000-8000-000000000001")
        emitter = EventEmitter(store, harness="codex", model="gpt-test")
        lifecycle = Lifecycle(store, "codex", "gpt-test", emitter)
        for state in ("preflighting", "ready", "running", "succeeded"):
            lifecycle.transition(state)
        with self.assertRaisesRegex(RuntimeError, "invalid dispatch transition|terminal"):
            lifecycle.transition("running")

    def test_hook_output_is_captured_before_an_unchanged_final_sentinel(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        config = sandbox.config / "pitwall/agents/hooks.json"
        config.parent.mkdir(parents=True)
        config.write_text(
            json.dumps(
                {
                    "dispatch.succeeded": [
                        {
                            "command": [
                                sys.executable,
                                "-c",
                                "import sys; print('hook-stdout'); print('hook-stderr', file=sys.stderr)",
                            ],
                            "timeoutSeconds": HANG_GUARD_SECS,
                            "failurePolicy": "ignore",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        result = sandbox.run("codex", [str(prompt)])
        self.assertEqual(0, result.returncode)
        self.assertEqual(b"fake-harness\n\nSHIM-DONE exit=0\n", result.stdout)
        self.assertNotIn(b"hook", result.stderr)
        hook_logs = list((sandbox.run_directories()[0] / "hooks").glob("*.stdout.log"))
        self.assertEqual(1, len(hook_logs))
        self.assertEqual(b"hook-stdout\n", hook_logs[0].read_bytes())

    def test_workflow_lineage_is_persisted_in_results_events_and_run_state(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt("workflow task\n")
        env = sandbox.environment()
        dispatch_id = "10000000-0000-4000-8000-000000000001"
        env.update(
            {
                "PITWALL_AGENTS_DISPATCH_ID": dispatch_id,
                "PITWALL_AGENTS_WORKFLOW_ID": "20000000-0000-4000-8000-000000000002",
                "PITWALL_AGENTS_TASK_ID": "review",
                "PITWALL_AGENTS_ATTEMPT": "2",
                "PITWALL_AGENTS_EFFORT": "high",
            }
        )
        result = sandbox.run("codex", [str(prompt)], env=env)
        self.assertEqual(0, result.returncode)
        run = sandbox.run_directories()[0]
        self.assertEqual(dispatch_id, run.name)
        document = json.loads((run / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(env["PITWALL_AGENTS_WORKFLOW_ID"], document["workflowId"])
        self.assertEqual("review", document["taskId"])
        self.assertEqual("high", document["effort"])
        run_state = json.loads((run / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(2, run_state["attempt"])
        events = [
            json.loads(line)
            for line in (run / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        self.assertTrue(
            all(
                event["workflowId"] == document["workflowId"] and event["taskId"] == "review"
                for event in events
            )
        )

    def test_preassigned_dispatch_id_cannot_overwrite_an_existing_run(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt("first\n")
        env = sandbox.environment()
        env["PITWALL_AGENTS_DISPATCH_ID"] = "30000000-0000-4000-8000-000000000003"
        self.assertEqual(0, sandbox.run("codex", [str(prompt)], env=env).returncode)
        second = sandbox.run("codex", [str(prompt)], env=env)
        self.assertEqual(64, second.returncode)
        self.assertIn(b"dispatch ID already exists", second.stderr)

    def test_ctrl_c_cancels_harness_process_group_and_records_cancelled_result(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        child_pid_file = sandbox.root / "child.pid"
        env = sandbox.environment(
            FAKE_SLEEP_SECS="30",
            FAKE_SPAWN_CHILD_SECS="30",
            FAKE_CHILD_PID_FILE=str(child_pid_file),
        )
        process = sandbox.popen(
            ["/bin/bash", str(SHIMS["codex"]), str(prompt)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            deadline = time.monotonic() + HANG_GUARD_SECS
            child_pid: int | None = None
            while time.monotonic() < deadline:
                try:
                    child_pid = int(child_pid_file.read_text(encoding="ascii"))
                except FileNotFoundError, ValueError:
                    time.sleep(0.02)
                    continue
                break
            self.assertIsNotNone(child_pid, "fake harness did not record its child PID")
            assert child_pid is not None
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=HANG_GUARD_SECS)
            self.assertEqual(130, process.returncode, stderr.decode(errors="replace"))
            self.assertTrue(stdout.endswith(b"\nSHIM-DONE exit=130\n"))
            result = json.loads(
                (sandbox.run_directories()[0] / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual("cancelled", result["status"])
            deadline = time.monotonic() + HANG_GUARD_SECS
            while time.monotonic() < deadline:
                stat = Path(f"/proc/{child_pid}/stat")
                if not stat.exists() or stat.read_text(encoding="utf-8").split()[2] == "Z":
                    break
                time.sleep(0.02)
            else:
                self.fail("harness grandchild survived Ctrl+C cancellation")
        finally:
            reap(process, group=True)

    def test_ctrl_c_still_cancels_when_the_wrapper_inherited_an_ignored_sigint(self) -> None:
        """The bash wrappers can leave SIGINT ignored in the exec'd image (a
        command-substitution wait before ``exec`` does it under load), and
        CPython preserves an inherited SIG_IGN for SIGINT. The CLI must take
        SIGINT back, so the supervisor stays Ctrl-C interruptible regardless
        of what the wrapper inherited."""
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        harness_pid_file = sandbox.root / "harness.pid"
        env = sandbox.environment(FAKE_SLEEP_SECS="30", FAKE_PID_FILE=str(harness_pid_file))
        process = sandbox.popen(
            ["/bin/bash", str(SHIMS["codex"]), str(prompt)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
        )
        try:
            deadline = time.monotonic() + HANG_GUARD_SECS
            while time.monotonic() < deadline:
                if harness_pid_file.exists():
                    break
                time.sleep(0.02)
            self.assertTrue(harness_pid_file.exists(), "fake harness never started")
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=HANG_GUARD_SECS)
            self.assertEqual(130, process.returncode, stderr.decode(errors="replace"))
            self.assertTrue(stdout.endswith(b"\nSHIM-DONE exit=130\n"), stdout[-200:])
            result = json.loads(
                (sandbox.run_directories()[0] / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual("cancelled", result["status"])
        finally:
            reap(process, group=True)

    def test_a_killed_supervisors_harness_is_terminated_when_the_run_is_reconciled(self) -> None:
        """SIGKILL the supervisor (as an OOM kill would): the harness it started lives in its
        own process group, so it survives. Reading the run must record ``failed`` and end that
        harness, not leave it running behind a terminal record."""
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        harness_pid_file = sandbox.root / "harness.pid"
        env = sandbox.environment(FAKE_SLEEP_SECS="30", FAKE_PID_FILE=str(harness_pid_file))
        process = sandbox.popen(
            ["/bin/bash", str(SHIMS["codex"]), str(prompt)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        harness_pid: int | None = None
        try:
            deadline = time.monotonic() + HANG_GUARD_SECS
            while time.monotonic() < deadline:
                try:
                    harness_pid = int(harness_pid_file.read_text(encoding="ascii"))
                    run_doc = json.loads(
                        (sandbox.run_directories()[0] / "run.json").read_text(encoding="utf-8")
                    )
                except FileNotFoundError, IndexError, ValueError:
                    time.sleep(0.02)
                    continue
                if "harnessProcess" in run_doc:
                    break
                time.sleep(0.02)
            self.assertIsNotNone(harness_pid, "fake harness never started")
            assert harness_pid is not None
            run_dir = sandbox.run_directories()[0]
            run_doc = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            self.assertEqual(harness_pid, run_doc["harnessProcess"]["pgid"])
            os.kill(run_doc["supervisor"]["pid"], signal.SIGKILL)
            process.communicate(timeout=HANG_GUARD_SECS)
            show = subprocess.run(
                [str(PITWALL), "agents", "runs", "show", run_dir.name],
                capture_output=True,
                env=env,
                timeout=HANG_GUARD_SECS,
                check=False,
            )
            self.assertEqual(0, show.returncode, show.stderr)
            state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["state"]
            self.assertEqual("failed", state)
            deadline = time.monotonic() + HANG_GUARD_SECS
            while time.monotonic() < deadline:
                # pid_alive treats a zombie as dead and works on macOS (ps) as well as Linux.
                if not pid_alive(harness_pid, None):
                    break
                time.sleep(0.02)
            else:
                self.fail("the harness of a killed supervisor survived reconciliation")
        finally:
            reap(process, group=True)
            if harness_pid is not None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(harness_pid, signal.SIGKILL)

    def test_normal_harness_exit_reaps_background_grandchild_before_sentinel(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        child_pid_file = sandbox.root / "background-child.pid"
        # The grandchild outlives the hang guard, so a shim that waited for it (instead of
        # reaping it) times out here; the bound itself is never a latency check.
        result = sandbox.run(
            "codex",
            [str(prompt)],
            env=sandbox.environment(
                FAKE_SPAWN_CHILD_SECS=str(2 * HANG_GUARD_SECS),
                FAKE_CHILD_PID_FILE=str(child_pid_file),
            ),
        )
        # Carry both streams: a preflight refusal (exit 127) names its cause only on stderr.
        self.assertEqual(
            0,
            result.returncode,
            f"stdout={result.stdout!r} stderr={result.stderr.decode(errors='replace')}",
        )
        self.assertEqual(b"fake-harness\n\nSHIM-DONE exit=0\n", result.stdout)
        child_pid = int(child_pid_file.read_text(encoding="ascii"))
        deadline = time.monotonic() + HANG_GUARD_SECS
        while time.monotonic() < deadline:
            stat = Path(f"/proc/{child_pid}/stat")
            if not stat.exists() or stat.read_text(encoding="utf-8").split()[2] == "Z":
                break
            time.sleep(0.02)
        else:
            self.fail("background harness grandchild survived normal parent exit")


if __name__ == "__main__":
    unittest.main()
