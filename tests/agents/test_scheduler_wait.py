"""wait_for_answer: paused dispatches resume when asks resolve (plan Task 27)."""

from __future__ import annotations

import json
import os
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from pitwall.agents.channel import (
    answer_ask,
)
from pitwall.agents.run_store import (
    RunStore,
    atomic_write_json,
    state_root,
)
from pitwall.agents.scheduler import (
    AttemptOutcome,
    cancel_workflow,
    resume_workflow,
)
from tests.agents.profiles_fixture import write_profiles
from tests.agents.test_channel_policy import (
    PolicyStub,
)
from tests.agents.test_scheduler import (
    FakeRunner,
    SchedulerFixture,
    make_registry,
    task,
)
from tests.hang_guard import HANG_GUARD_SECS, BackgroundAction

ROOT = Path(__file__).resolve().parents[2]

_ENDPOINT_QWEN = {
    "displayName": "Qwen Code",
    "shim": "qwen-shim.sh",
    "binaryCandidates": ["qwen"],
    "nativeHosts": [],
    "promptDelivery": "stdin",
    "allowUnknownModels": True,
    "harnessKind": "model-agnostic",
    "endpointDelivery": "config-sync",
    "models": {},
}


class PausingRunner(FakeRunner):
    """First attempt of each task pauses on one ask; resume() succeeds."""

    def __init__(self, env: dict[str, str], repo: Path, asks: dict[str, dict]) -> None:
        super().__init__(env, repo)
        self.asks = asks
        self.resumes: list[str] = []

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
    ):  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
        store = RunStore(state_root(env), dispatch_id)
        store.path.mkdir(parents=True, exist_ok=True)
        ask = self.asks[task_id]
        written = store.mailbox().write_ask(
            blocked_on=ask["blocked_on"],
            question="q",
            default=ask.get("default", "a"),
            options=[{"id": "a", "text": "x"}, {"id": "b", "text": "y"}],
            deadline_s=ask.get("deadline_s", 600),
        )
        if "age" in ask:
            path = store.path / "mailbox" / "asks" / f"{written['ask_id']}.json"
            doc = json.loads(path.read_text())
            doc["created_at"] = (datetime.now(UTC) - timedelta(seconds=ask["age"])).isoformat()
            path.write_text(json.dumps(doc))
        atomic_write_json(
            store.artifact("run.json"),
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "state": "paused",
                "provider": "opencode",
                "model": "m",
                "attempt": 1,
            },
        )
        return AttemptOutcome(dispatch_id, "paused", 75)

    def resume(
        self, task_id, task_value, attempt, dispatch_id, workflow_id, workflow_dir, env, repo_root
    ):  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
        self.resumes.append(dispatch_id)
        return super().__call__(
            task_id,
            task_value,
            b"",
            attempt,
            dispatch_id,
            workflow_id,
            workflow_dir,
            env,
            repo_root,
        )


@mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
class WaitForAnswerTests(SchedulerFixture):
    """The workflow runs on the main thread (so its SIGINT cancel handler is installed, as in production);
    operator actions happen on a helper thread once the task is waiting."""

    def setUp(self) -> None:
        super().setUp()
        # load_routes validates profiles.json against the registry; the synthetic
        # registry needs a model-agnostic endpoint harness for the doc to validate.
        registry = make_registry()
        registry["harnesses"]["qwen"] = _ENDPOINT_QWEN
        self.registry = registry

    def _write_routes(self, stub: PolicyStub) -> None:
        routes = Path(self.env["HOME"]) / ".config" / "pitwall" / "pitwall.toml"
        write_profiles(
            routes,
            {
                "schemaVersion": 1,
                "defaults": {"endpointHarness": "qwen"},
                "models": {"policy-stub": stub.entry},
            },
        )

    def _when_waiting(self, task_id: str, action) -> BackgroundAction:  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
        def poll() -> bool:
            for state_file in (state_root(self.env) / "workflows").glob("*/state.json"):
                try:
                    state = json.loads(state_file.read_text())
                except json.JSONDecodeError:
                    continue
                record = state["tasks"][task_id]
                if record["state"] == "waiting_for_answer":
                    action(state_file.parent.name, record["attempts"][0]["dispatchId"])
                    return True
            return False

        helper = BackgroundAction(poll)
        self.addCleanup(helper.stop)
        return helper

    def _answered_by(self, state: dict, task_id: str) -> str:
        dispatch_id = state["tasks"][task_id]["attempts"][0]["dispatchId"]
        answer = RunStore(state_root(self.env), dispatch_id).mailbox().get_answer("0001")
        assert answer is not None
        return str(answer["answered_by"])

    def test_policy_answers_routine_asks_and_the_dispatch_resumes(self) -> None:
        stub = PolicyStub()
        self.addCleanup(stub.close)
        self._write_routes(stub)
        runner = PausingRunner(self.env, self.repo, {"a": {"blocked_on": "naming"}})
        state = self.execute(
            self.workflow({"a": task()}, {"autoAnswer": {"route": "policy-stub"}}), runner
        )
        self.assertEqual("succeeded", state["status"], state)
        self.assertEqual("policy:stub-model", self._answered_by(state, "a"))
        attempts = state["tasks"]["a"]["attempts"]
        self.assertEqual(2, len(attempts))
        self.assertEqual(attempts[0]["dispatchId"], attempts[1]["dispatchId"])
        self.assertTrue(attempts[1]["resumed"])
        self.assertEqual([attempts[0]["dispatchId"]], runner.resumes)

    def test_consequential_asks_escalate_to_the_operator(self) -> None:
        seen: dict[str, str] = {}

        def operator(_workflow_id: str, dispatch_id: str) -> None:
            # The scheduler publishes waiting_for_answer before it escalates (the policy may
            # still answer), so the operator acts on the escalation notice itself.
            events = RunStore(state_root(self.env), dispatch_id).artifact("events.jsonl")
            deadline = time.monotonic() + HANG_GUARD_SECS
            while time.monotonic() < deadline:
                seen["events"] = events.read_text() if events.is_file() else ""
                if '"event":"ask.escalated"' in seen["events"]:
                    break
                time.sleep(0.02)
            answer_ask(
                self.env,
                dispatch_id,
                "0001",
                choice="b",
                answered_by="operator",
                note=None,
                harness="operator",
            )

        helper = self._when_waiting("a", operator)
        runner = PausingRunner(self.env, self.repo, {"a": {"blocked_on": "destructive"}})
        state = self.execute(self.workflow({"a": task()}), runner)
        helper.finish()
        self.assertEqual("succeeded", state["status"])
        self.assertEqual("operator", self._answered_by(state, "a"))
        self.assertIn('"event":"ask.escalated"', seen["events"])

    def test_expired_asks_take_their_default(self) -> None:
        runner = PausingRunner(
            self.env, self.repo, {"a": {"blocked_on": "spend", "deadline_s": 60, "age": 120}}
        )
        state = self.execute(self.workflow({"a": task()}), runner)
        self.assertEqual("succeeded", state["status"])
        self.assertEqual("default", self._answered_by(state, "a"))

    def _cancelled_while_waiting(self) -> tuple[str, str]:
        ids: dict[str, str] = {}

        def cancel(workflow_id: str, dispatch_id: str) -> None:
            ids.update(workflow=workflow_id, dispatch=dispatch_id)
            cancel_workflow(
                self.env, workflow_id
            )  # SIGINT to this process, handled by the scheduler

        helper = self._when_waiting("a", cancel)
        state = self.execute(
            self.workflow({"a": task()}),
            PausingRunner(self.env, self.repo, {"a": {"blocked_on": "schema"}}),
        )
        helper.finish()
        self.assertEqual("cancelled", state["status"])
        return ids["workflow"], ids["dispatch"]

    def test_cancel_while_waiting_leaves_the_run_paused(self) -> None:
        _workflow_id, dispatch_id = self._cancelled_while_waiting()
        run = json.loads(
            RunStore(state_root(self.env), dispatch_id).artifact("run.json").read_text()
        )
        self.assertEqual("paused", run["state"])

    def test_resume_workflow_reenters_the_wait_for_a_paused_task(self) -> None:
        workflow_id, dispatch_id = self._cancelled_while_waiting()
        # A scheduler that died mid-wait leaves the task persisted as waiting_for_answer.
        state_file = state_root(self.env) / "workflows" / workflow_id / "state.json"
        persisted = json.loads(state_file.read_text())
        persisted["tasks"]["a"]["state"] = "waiting_for_answer"
        atomic_write_json(state_file, persisted)
        answer_ask(
            self.env,
            dispatch_id,
            "0001",
            choice="a",
            answered_by="operator",
            note=None,
            harness="operator",
        )
        second = PausingRunner(self.env, self.repo, {"a": {"blocked_on": "schema"}})
        resumed = resume_workflow(
            workflow_id, repo_root=self.repo, env=self.env, registry=self.registry, runner=second
        )
        self.assertEqual("succeeded", resumed["status"])
        self.assertEqual([dispatch_id], second.resumes)
        self.assertEqual(
            1, len(RunStore(state_root(self.env), dispatch_id).mailbox().asks())
        )  # no fresh dispatch


if __name__ == "__main__":
    unittest.main()
