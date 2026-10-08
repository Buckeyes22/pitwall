"""Acceptance tests for persistent Phase 6 workflow scheduling."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents.harnesses import get_adapter
from pitwall.agents.profiles import save_profiles, validate_profiles
from pitwall.agents.registry import (
    load_registry,
)
from pitwall.agents.run_store import (
    atomic_write_json,
    ensure_private_directory,
    state_root,
)
from pitwall.agents.scheduler import (
    AttemptOutcome,
    WorkflowRunError,
    _StateController,
    cancel_workflow,
    list_workflows,
    resume_workflow,
    run_workflow,
    show_workflow,
)
from tests.agents.shim_test_support import PITWALL
from tests.agents.test_workflow import (
    make_registry,
)
from tests.hang_guard import HANG_GUARD_SECS, reap

ROOT = Path(__file__).resolve().parents[2]


class HarnessArgumentTests(unittest.TestCase):
    def test_pi_workflow_uses_prompt_file_model_and_thinking(self) -> None:
        prompt = Path("/tmp/pi-prompt.md")
        self.assertEqual(
            [str(prompt), "--model", "qwen-local", "--thinking", "low"],
            get_adapter("pi").workflow_args("qwen-local", "low", prompt),
        )

    def test_kimi_workflow_uses_prompt_file_and_model_override(self) -> None:
        prompt = Path("/tmp/kimi-prompt.md")
        self.assertEqual(
            [str(prompt), "--model", "kimi-code/kimi-for-coding"],
            get_adapter("kimi").workflow_args("kimi-code/kimi-for-coding", None, prompt),
        )


def task(
    *,
    depends: list[str] | None = None,
    context: list[dict] | None = None,
    verify: list[list[str]] | None = None,
    retry: dict | None = None,
    mode: str = "read",
) -> dict:
    value = {
        "route": {"provider": "opencode", "model": "test/model-1"},
        "mode": mode,
        "prompt": {"text": "do the task"},
        "dependsOn": depends or [],
    }
    if context is not None:
        value["contextFrom"] = context
    if verify is not None:
        value["verify"] = verify
    if retry is not None:
        value["retry"] = retry
    return value


class FakeRunner:
    def __init__(
        self,
        env: dict[str, str],
        repo: Path,
        behaviors: dict[str, list[tuple[str, int, bool]]] | None = None,
        delay: float = 0.0,
    ) -> None:
        self.env = env
        self.repo = repo
        self.behaviors = behaviors or {}
        self.delay = delay
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.active = 0
        self.max_active = 0

    def __call__(
        self,
        task_id,
        task_value,
        prompt,
        attempt,
        dispatch_id,
        workflow_id,
        workflow_dir,
        env,
        repo_root,
    ):
        started = time.monotonic()
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        if self.delay:
            time.sleep(self.delay)
        sequence = self.behaviors.get(task_id, [("succeeded", 0, False)])
        index = sum(1 for call in self.calls if call["task"] == task_id)
        status, exit_code, transport = sequence[min(index, len(sequence) - 1)]
        run_dir = state_root(env) / "runs" / dispatch_id
        ensure_private_directory(run_dir)
        (run_dir / "stdout.log").write_bytes((f"output-{task_id}-" + "x" * 100).encode())
        (run_dir / "stderr.log").write_bytes(b"")
        (run_dir / "changes.patch").write_bytes(b"patch")
        atomic_write_json(run_dir / "changeset.json", {"diffstat": {"files": 1}})
        workspace = repo_root
        if task_value["mode"] == "write":
            workspace = workflow_dir / "fake-worktrees" / dispatch_id
            ensure_private_directory(workspace)
        result_path = run_dir / "result.json"
        atomic_write_json(
            result_path,
            {
                "dispatchId": dispatch_id,
                "workflowId": workflow_id,
                "taskId": task_id,
                "status": status,
                "workspace": {
                    "path": str(workspace),
                    "mode": "isolated" if task_value["mode"] == "write" else "shared",
                },
            },
        )
        finished = time.monotonic()
        with self.lock:
            self.active -= 1
            self.calls.append(
                {
                    "task": task_id,
                    "attempt": attempt,
                    "dispatch": dispatch_id,
                    "prompt": bytes(prompt),
                    "started": started,
                    "finished": finished,
                }
            )
        return AttemptOutcome(
            dispatch_id,
            status,
            exit_code,
            result_path=str(result_path),
            workspace_path=str(workspace),
            transport_error=transport,
        )


class SchedulerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.com"], check=True
        )
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"], check=True)
        (self.repo / "seed.txt").write_text("seed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "seed.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "seed"], check=True)
        self.env = {
            "HOME": str(self.root / "home"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "PYTHONPYCACHEPREFIX": str(self.root / "pycache"),
            "PATH": os.environ.get("PATH", ""),
        }
        self.registry = make_registry()

    def tearDown(self) -> None:
        # A cancelled dispatch's grandchild can still be writing PYTHONPYCACHEPREFIX for a
        # moment after the runner exits; give it a few seconds before ignoring leftovers.
        deadline = time.monotonic() + 5
        while True:
            try:
                self.temporary.cleanup()
                return
            except OSError:
                if time.monotonic() >= deadline:
                    shutil.rmtree(self.temporary.name, ignore_errors=True)
                    return
                time.sleep(0.1)

    def workflow(self, tasks: dict, defaults: dict | None = None) -> Path:
        value = {"schemaVersion": 1, "name": "test-workflow", "tasks": tasks}
        if defaults is not None:
            value["defaults"] = defaults
        path = self.repo / f"workflow-{time.monotonic_ns()}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def execute(self, path: Path, runner: FakeRunner):
        return run_workflow(
            path,
            host="copilot",
            repo_root=self.repo,
            env=self.env,
            registry=self.registry,
            runner=runner,
        )


class SchedulingTests(SchedulerFixture):
    def test_independent_roots_overlap_and_dependent_waits(self) -> None:
        runner = FakeRunner(self.env, self.repo, delay=0.08)
        state = self.execute(
            self.workflow({"a": task(), "b": task(), "c": task(depends=["a", "b"])}), runner
        )
        self.assertEqual("succeeded", state["status"], state)
        self.assertGreaterEqual(runner.max_active, 2)
        by_task = {call["task"]: call for call in runner.calls}
        self.assertGreaterEqual(
            by_task["c"]["started"], max(by_task["a"]["finished"], by_task["b"]["finished"])
        )

    def test_harness_concurrency_serializes_same_harness(self) -> None:
        runner = FakeRunner(self.env, self.repo, delay=0.04)
        path = self.workflow(
            {"a": task(), "b": task()},
            {"maxConcurrency": 2, "providerConcurrency": {"opencode": 1}},
        )
        self.execute(path, runner)
        self.assertEqual(1, runner.max_active)

    def test_fail_fast_skips_unstarted_branch_and_continue_runs_it(self) -> None:
        tasks = {
            "bad": task(),
            "after": task(depends=["bad"]),
            "other": task(depends=["gate"]),
            "gate": task(),
        }
        fail_runner = FakeRunner(self.env, self.repo, {"bad": [("failed", 1, False)]}, delay=0.03)
        failed = self.execute(
            self.workflow(tasks, {"maxConcurrency": 1, "failurePolicy": "fail-fast"}), fail_runner
        )
        self.assertEqual("blocked", failed["tasks"]["after"]["state"])
        self.assertEqual("skipped", failed["tasks"]["gate"]["state"])
        self.assertEqual("blocked", failed["tasks"]["other"]["state"])
        continue_runner = FakeRunner(self.env, self.repo, {"bad": [("failed", 1, False)]})
        continued = self.execute(
            self.workflow(tasks, {"maxConcurrency": 2, "failurePolicy": "continue"}),
            continue_runner,
        )
        self.assertEqual("succeeded", continued["tasks"]["other"]["state"])
        self.assertEqual(1, sum(call["task"] == "other" for call in continue_runner.calls))

    def test_no_task_runs_twice(self) -> None:
        runner = FakeRunner(self.env, self.repo, delay=0.02)
        self.execute(self.workflow({"a": task(), "b": task(depends=["a"])}), runner)
        self.assertEqual(
            {"a": 1, "b": 1},
            {name: sum(call["task"] == name for call in runner.calls) for name in ("a", "b")},
        )


class ContextRetryVerificationTests(SchedulerFixture):
    def test_only_explicit_context_is_added_and_truncated(self) -> None:
        runner = FakeRunner(self.env, self.repo)
        path = self.workflow(
            {
                "source": task(),
                "plain": task(depends=["source"]),
                "selected": task(
                    depends=["source"],
                    context=[{"task": "source", "artifact": "stdout", "maxBytes": 12}],
                ),
            }
        )
        self.execute(path, runner)
        prompts = {call["task"]: call["prompt"] for call in runner.calls}
        self.assertNotIn(b"dependency context", prompts["plain"])
        self.assertIn(b"dependency context", prompts["selected"])
        self.assertIn(b"truncated: true", prompts["selected"])
        self.assertNotIn(b"x" * 20, prompts["selected"])

    def test_retry_policy_uses_fresh_dispatch_ids_and_usage_errors_never_retry(self) -> None:
        runner = FakeRunner(
            self.env,
            self.repo,
            {
                "flaky": [("timed_out", 124, False), ("succeeded", 0, False)],
                "usage": [("failed", 64, True)],
            },
        )
        retry = {"maxAttempts": 2, "backoffSeconds": 0, "on": ["timeout", "transport-error"]}
        state = self.execute(
            self.workflow(
                {"flaky": task(retry=retry), "usage": task(retry=retry)},
                {"failurePolicy": "continue"},
            ),
            runner,
        )
        flaky = [call for call in runner.calls if call["task"] == "flaky"]
        self.assertEqual(2, len(flaky))
        self.assertEqual(2, len({call["dispatch"] for call in flaky}))
        self.assertEqual(1, sum(call["task"] == "usage" for call in runner.calls))
        self.assertEqual("succeeded", state["tasks"]["flaky"]["state"])

    def test_verification_records_argv_and_distinguishes_failure(self) -> None:
        runner = FakeRunner(self.env, self.repo)
        state = self.execute(
            self.workflow(
                {
                    "good": task(verify=[[sys.executable, "-c", "print('ok')"]]),
                    "bad": task(verify=[[sys.executable, "-c", "raise SystemExit(3)"]]),
                    "large": task(
                        verify=[
                            [
                                sys.executable,
                                "-c",
                                "import os; os.write(1, b'x' * (2 * 1024 * 1024))",
                            ]
                        ]
                    ),
                },
                {"failurePolicy": "continue"},
            ),
            runner,
        )
        self.assertEqual("verified", state["tasks"]["good"]["state"])
        self.assertEqual("verification_failed", state["tasks"]["bad"]["state"])
        record = json.loads(
            Path(state["tasks"]["good"]["verificationPath"]).read_text(encoding="utf-8")
        )
        self.assertEqual([sys.executable, "-c", "print('ok')"], record["commands"][0]["argv"])
        large = json.loads(
            Path(state["tasks"]["large"]["verificationPath"]).read_text(encoding="utf-8")
        )["commands"][0]
        self.assertEqual(1024 * 1024, len(large["stdout"]))
        self.assertEqual(2 * 1024 * 1024, large["stdoutBytes"])
        self.assertTrue(large["stdoutTruncated"])


class ResumeAndStorageTests(SchedulerFixture):
    def test_resume_skips_completed_task_and_retries_incomplete_task(self) -> None:
        first_runner = FakeRunner(self.env, self.repo, {"b": [("failed", 1, False)]})
        first = self.execute(
            self.workflow({"a": task(), "b": task()}, {"failurePolicy": "continue"}), first_runner
        )
        second_runner = FakeRunner(self.env, self.repo)
        resumed = resume_workflow(
            first["workflowId"],
            repo_root=self.repo,
            env=self.env,
            registry=self.registry,
            runner=second_runner,
        )
        self.assertEqual("succeeded", resumed["status"])
        self.assertEqual(0, sum(call["task"] == "a" for call in second_runner.calls))
        self.assertEqual(1, sum(call["task"] == "b" for call in second_runner.calls))
        self.assertEqual(2, len(resumed["tasks"]["b"]["attempts"]))

    def test_resume_rejects_a_declared_host_that_differs_from_the_stored_host(self) -> None:
        runner = FakeRunner(self.env, self.repo)
        state = self.execute(self.workflow({"a": task()}), runner)
        with self.assertRaisesRegex(WorkflowRunError, "host mismatch"):
            resume_workflow(
                state["workflowId"],
                repo_root=self.repo,
                env=self.env,
                registry=self.registry,
                runner=runner,
                declared_host="claude",
            )

    def test_resume_rejects_digest_registry_repo_and_missing_worktree(self) -> None:
        runner = FakeRunner(self.env, self.repo)
        state = self.execute(self.workflow({"write": task(mode="write")}), runner)
        workflow_dir = state_root(self.env) / "workflows" / state["workflowId"]
        workflow_doc = json.loads((workflow_dir / "workflow.json").read_text(encoding="utf-8"))
        workflow_doc["name"] = "tampered"
        atomic_write_json(workflow_dir / "workflow.json", workflow_doc)
        with self.assertRaisesRegex(WorkflowRunError, "digest"):
            resume_workflow(
                state["workflowId"],
                repo_root=self.repo,
                env=self.env,
                registry=self.registry,
                runner=runner,
            )
        atomic_write_json(workflow_dir / "workflow.json", {**workflow_doc, "name": "test-workflow"})
        changed_registry = json.loads(json.dumps(self.registry))
        changed_registry["schemaVersion"] = 99
        with self.assertRaisesRegex(WorkflowRunError, "registry"):
            resume_workflow(
                state["workflowId"],
                repo_root=self.repo,
                env=self.env,
                registry=changed_registry,
                runner=runner,
            )
        other = self.root / "other"
        other.mkdir()
        with self.assertRaisesRegex(WorkflowRunError, "repository"):
            resume_workflow(
                state["workflowId"],
                repo_root=other,
                env=self.env,
                registry=self.registry,
                runner=runner,
            )
        workspace = Path(state["tasks"]["write"]["attempts"][-1]["workspacePath"])
        workspace.rmdir()
        with self.assertRaisesRegex(WorkflowRunError, "worktree"):
            resume_workflow(
                state["workflowId"],
                repo_root=self.repo,
                env=self.env,
                registry=self.registry,
                runner=runner,
            )

    def test_list_show_cancel_and_private_state(self) -> None:
        runner = FakeRunner(self.env, self.repo)
        state = self.execute(self.workflow({"a": task()}), runner)
        listed = list_workflows(self.env)
        self.assertEqual(state["workflowId"], listed[0]["workflowId"])
        self.assertEqual(state["status"], show_workflow(self.env, state["workflowId"])["status"])
        cancelled = cancel_workflow(self.env, state["workflowId"])
        self.assertFalse(cancelled["cancellationRequested"])
        self.assertEqual("succeeded", cancelled["status"])
        self.assertFalse(cancel_workflow(self.env, state["workflowId"])["cancellationRequested"])
        directory = state_root(self.env) / "workflows" / state["workflowId"]
        self.assertEqual(0o700, directory.stat().st_mode & 0o777)
        self.assertEqual(0o600, (directory / "state.json").stat().st_mode & 0o777)

    def test_cancel_never_signals_a_stale_or_reused_pid_without_runner_lease(self) -> None:
        runner = FakeRunner(self.env, self.repo)
        state = self.execute(self.workflow({"a": task()}), runner)
        directory = state_root(self.env) / "workflows" / state["workflowId"]
        stale = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        stale.update({"status": "running", "runnerPid": 424242})
        atomic_write_json(directory / "state.json", stale)
        with mock.patch("pitwall.agents.scheduler.os.kill") as kill:
            cancelled = cancel_workflow(self.env, state["workflowId"])
        kill.assert_not_called()
        self.assertTrue(cancelled["cancellationRequested"])

    def test_controller_preserves_external_cancellation_request_on_next_write(self) -> None:
        runner = FakeRunner(self.env, self.repo)
        state = self.execute(self.workflow({"a": task()}), runner)
        directory = state_root(self.env) / "workflows" / state["workflowId"]
        persisted = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        persisted.update({"status": "running", "runnerPid": None})
        atomic_write_json(directory / "state.json", persisted)
        stale_controller_state = json.loads(json.dumps(persisted))
        cancel_workflow(self.env, state["workflowId"])
        self.assertFalse(stale_controller_state["cancellationRequested"])
        controller = _StateController(directory, stale_controller_state)
        controller.write()
        merged = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        self.assertTrue(merged["cancellationRequested"])


class ProductionEnvMixin:
    """Builds a sandbox environment with an installed `pitwall` and a fake opencode."""

    def production_env(self: Any, **updates: str) -> dict[str, str]:
        fake_binary = self.root / "fake-opencode"
        fake_binary.write_bytes(
            (ROOT / "tests/agents" / "fixtures" / "fake_harness.py").read_bytes()
        )
        fake_binary.chmod(0o755)
        env = dict(self.env)
        env.update(
            {
                "OPENCODE_BIN": str(fake_binary),
                "PITWALL_AGENTS_UNRESTRICTED": "0",
                "PYTHONPATH": str(ROOT / "src"),
            }
        )
        env.update(updates)
        return env


class ProductionRunnerTests(ProductionEnvMixin, SchedulerFixture):
    def test_named_pi_route_propagates_endpoint_model_and_route_identity(self) -> None:
        fake_pi = self.root / "fake-pi"
        fake_pi.write_bytes((ROOT / "tests/agents" / "fixtures" / "fake_harness.py").read_bytes())
        fake_pi.chmod(0o755)
        agent_dir = self.root / "pi-agent"
        models_path = agent_dir / "models.json"
        models_path.parent.mkdir(parents=True)
        models_path.write_text(
            json.dumps(
                {
                    "providers": {
                        "route-a": {
                            "baseUrl": "http://endpoint-a/v1",
                            "api": "openai-completions",
                            "apiKey": "$PI_KEY",
                            "models": [
                                {"id": "shared/model", "contextWindow": 32768, "maxTokens": 4096}
                            ],
                        },
                        "route-b": {
                            "baseUrl": "http://endpoint-b/v1",
                            "api": "openai-completions",
                            "apiKey": "$PI_KEY",
                            "models": [
                                {"id": "shared/model", "contextWindow": 32768, "maxTokens": 4096}
                            ],
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        profiles_path = self.root / "profiles.toml"
        routes = {
            "schemaVersion": 1,
            "defaults": {"harness": "opencode", "endpointHarness": "qwen"},
            "models": {
                "route-a": {
                    "model": "shared/model",
                    "harness": "pi",
                    "endpoint": {"baseUrl": "http://endpoint-a/v1", "apiKeyEnv": "PI_KEY"},
                },
                "route-b": {
                    "model": "shared/model",
                    "harness": "pi",
                    "endpoint": {"baseUrl": "http://endpoint-b/v1", "apiKeyEnv": "PI_KEY"},
                },
            },
        }
        save_profiles(
            {**self.env, "PITWALL_AGENTS_PROFILES": str(profiles_path)},
            validate_profiles(routes, registry=load_registry()),
            registry=load_registry(),
        )
        args_path = self.root / "pi-args"
        env = self.production_env(
            PI_BIN=str(fake_pi),
            PI_CODING_AGENT_DIR=str(agent_dir),
            PI_KEY="fixture-secret",
            FAKE_ARGS_FILE=str(args_path),
            PITWALL_AGENTS_PROFILES=str(profiles_path),
        )
        body = {
            "schemaVersion": 1,
            "name": "named-pi",
            "tasks": {
                "agent": {
                    "route": {"name": "route-b", "provider": "pi", "model": "shared/model"},
                    "mode": "read",
                    "prompt": {"text": "reply with pong"},
                }
            },
        }
        path = self.repo / "named-pi.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        state = run_workflow(
            path, host="copilot", repo_root=self.repo, env=env, registry=load_registry()
        )
        self.assertEqual("succeeded", state["status"], state)
        args = args_path.read_bytes().split(b"\0")
        self.assertIn(b"--provider", args)
        self.assertEqual(b"route-b", args[args.index(b"--provider") + 1])
        self.assertEqual(b"shared/model", args[args.index(b"--model") + 1])
        result = json.loads(
            Path(state["tasks"]["agent"]["attempts"][0]["resultPath"]).read_text(encoding="utf-8")
        )
        self.assertEqual("shared/model", result["model"])
        self.assertEqual("endpoint-b", result["route"]["endpointHost"])
        self.assertEqual("route-b", result["route"]["name"])

    def test_production_runner_assigns_lineage_before_dispatch(self) -> None:
        env = self.production_env()
        state = run_workflow(
            self.workflow({"agent": task()}),
            host="copilot",
            repo_root=self.repo,
            env=env,
            registry=self.registry,
        )
        self.assertEqual("succeeded", state["status"], state)
        attempt = state["tasks"]["agent"]["attempts"][0]
        result = json.loads(Path(attempt["resultPath"]).read_text(encoding="utf-8"))
        self.assertEqual(state["workflowId"], result["workflowId"])
        self.assertEqual("agent", result["taskId"])
        self.assertEqual(attempt["dispatchId"], result["dispatchId"])

    def test_ask_support_task_dispatches_with_channel_flags(self) -> None:
        env = self.production_env()
        asking = task()
        asking["askSupport"] = True
        asking["maxAsks"] = 2
        state = run_workflow(
            self.workflow({"agent": asking}),
            host="copilot",
            repo_root=self.repo,
            env=env,
            registry=self.registry,
        )
        self.assertEqual("succeeded", state["status"], state)
        dispatch_id = state["tasks"]["agent"]["attempts"][0]["dispatchId"]
        resume = json.loads(
            (state_root(env) / "runs" / dispatch_id / "resume.json").read_text(encoding="utf-8")
        )
        self.assertTrue(resume["askSupport"])
        self.assertEqual(2, resume["maxAsks"])

    def test_cli_cancel_interrupts_active_dispatch_and_leaves_resumable_state(self) -> None:
        env = self.production_env(FAKE_SLEEP_SECS="30")
        workflow = self.workflow({"slow": task()})
        process = subprocess.Popen(
            [
                str(PITWALL),
                "agents",
                "workflow",
                "run",
                str(workflow),
                "--host",
                "copilot",
            ],
            cwd=self.repo,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            workflows_root = state_root(env) / "workflows"
            deadline = time.monotonic() + HANG_GUARD_SECS
            workflow_dir = None
            while time.monotonic() < deadline:
                candidates = (
                    list(workflows_root.glob("*/state.json")) if workflows_root.is_dir() else []
                )
                if candidates:
                    candidate_state = json.loads(candidates[0].read_text(encoding="utf-8"))
                    if candidate_state["tasks"]["slow"]["state"] == "running":
                        workflow_dir = candidates[0].parent
                        break
                time.sleep(0.02)
            self.assertIsNotNone(workflow_dir, "workflow task never reached running state")
            cancelled = subprocess.run(
                [
                    str(PITWALL),
                    "agents",
                    "workflow",
                    "cancel",
                    workflow_dir.name,
                ],
                cwd=self.repo,
                env=env,
                capture_output=True,
                timeout=HANG_GUARD_SECS,
                check=False,
            )
            self.assertEqual(0, cancelled.returncode, cancelled.stderr.decode(errors="replace"))
            stdout, stderr = process.communicate(timeout=HANG_GUARD_SECS)
            self.assertEqual(1, process.returncode, stderr.decode(errors="replace"))
            final = json.loads((workflow_dir / "state.json").read_text(encoding="utf-8"))
            self.assertEqual("cancelled", final["status"])
            self.assertEqual("cancelled", final["tasks"]["slow"]["state"])
            self.assertIn(workflow_dir.name, final["resumeCommand"])
            self.assertIn("--host copilot", final["resumeCommand"])
            self.assertIn(b'"status": "cancelled"', stdout)
        finally:
            reap(process)


if __name__ == "__main__":
    unittest.main()
