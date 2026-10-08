"""Phase D exit, scripted: unattended fan-out, one policy answer, one escalation (plan Task 29)."""

from __future__ import annotations

import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents.run_store import (
    RunStore,
    state_root,
)
from pitwall.agents.scheduler import (
    run_workflow,
)
from tests.agents.profiles_fixture import write_profiles
from tests.agents.shim_test_support import PITWALL
from tests.agents.test_channel_policy import (
    PolicyStub,
)
from tests.agents.test_scheduler import (
    ProductionEnvMixin,
    SchedulerFixture,
    make_registry,
    task,
)
from tests.hang_guard import HANG_GUARD_SECS, BackgroundAction

ROOT = Path(__file__).resolve().parents[2]


# A fake opencode: attempt 1 asks (routine or destructive, by prompt) and pauses; attempt 2 finishes.
_ASKING_OPENCODE = """\
#!/usr/bin/env python3
import os, sys
from pathlib import Path
sys.path.insert(0, {runtime!r})
from pitwall.agents.mailbox import Mailbox
prompt = sys.stdin.read()
if os.environ["PITWALL_AGENTS_CHANNEL_ATTEMPT"] == "1":
    dispatch_id = os.environ["PITWALL_AGENTS_CHANNEL_DISPATCH_ID"]
    root = Path(os.environ["PITWALL_AGENTS_CHANNEL_STATE_ROOT"])
    Mailbox(root / "runs" / dispatch_id, dispatch_id).write_ask(
        blocked_on="naming" if "ROUTINE" in prompt else "destructive", question="Which option?",
        options=[{{"id": "a", "text": "first"}}, {{"id": "b", "text": "second"}}], default="a", deadline_s=600)
    sys.exit(75)
print("resumed", flush=True)
"""

# A fake tier-1 opencode: it asks and then blocks until the ask is answered, as the MCP
# ask_orchestrator tool does, so the attempt never pauses.
_BLOCKING_OPENCODE = """\
#!/usr/bin/env python3
import os, sys, time
from pathlib import Path
sys.path.insert(0, {runtime!r})
from pitwall.agents.mailbox import Mailbox
prompt = sys.stdin.read()
dispatch_id = os.environ["PITWALL_AGENTS_CHANNEL_DISPATCH_ID"]
root = Path(os.environ["PITWALL_AGENTS_CHANNEL_STATE_ROOT"])
box = Mailbox(root / "runs" / dispatch_id, dispatch_id)
ask = box.write_ask(
    blocked_on="naming" if "ROUTINE" in prompt else "destructive", question="Which option?",
    options=[{{"id": "a", "text": "first"}}, {{"id": "b", "text": "second"}}], default="a", deadline_s=600)
deadline = time.monotonic() + {hang_guard!r}
while time.monotonic() < deadline:
    answer = box.get_answer(ask["ask_id"])
    if answer is not None:
        print("answered", answer["choice"], flush=True)
        sys.exit(0)
    time.sleep(0.05)
sys.exit(1)
"""

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


@mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
class WorkflowChannelEndToEndTests(ProductionEnvMixin, SchedulerFixture):
    def setUp(self) -> None:
        super().setUp()
        registry = make_registry()
        registry["harnesses"]["qwen"] = _ENDPOINT_QWEN
        self.registry = registry

    def _operator_answers_dangerous(self, env: dict[str, str]) -> BackgroundAction:
        """Answer the dangerous task's ask as the operator once the workflow waits on it."""

        def poll() -> bool:
            for state_file in (state_root(env) / "workflows").glob("*/state.json"):
                try:
                    record = json.loads(state_file.read_text())["tasks"]["dangerous"]
                except json.JSONDecodeError, KeyError:
                    continue
                if record["state"] == "waiting_for_answer":
                    subprocess.run(
                        [
                            str(PITWALL),
                            "agents",
                            "answer",
                            record["attempts"][0]["dispatchId"],
                            "0001",
                            "b",
                        ],
                        env=env,
                        check=True,
                        capture_output=True,
                    )
                    return True
            return False

        helper = BackgroundAction(poll, interval=0.1)
        self.addCleanup(helper.stop)
        return helper

    def test_unattended_fan_out_with_one_policy_answer_and_one_escalation(self) -> None:
        stub = PolicyStub()
        self.addCleanup(stub.close)
        routes = Path(self.env["HOME"]) / ".config" / "pitwall" / "pitwall.toml"
        write_profiles(
            routes,
            {
                "schemaVersion": 1,
                "defaults": {"endpointHarness": "qwen"},
                "models": {"policy-stub": stub.entry},
            },
        )
        fake = self.root / "asking-opencode"
        fake.write_text(_ASKING_OPENCODE.format(runtime=str(ROOT / "src")), encoding="utf-8")
        fake.chmod(0o755)
        ledger = self.root / "ledger.jsonl"
        env = self.production_env(OPENCODE_BIN=str(fake), PITWALL_AGENTS_LEDGER=str(ledger))
        workflow = self.workflow(
            {
                "routine": {**task(), "prompt": {"text": "ROUTINE task"}},
                "dangerous": {**task(), "prompt": {"text": "DANGEROUS task"}},
            },
            {"maxConcurrency": 2, "askSupport": True, "autoAnswer": {"route": "policy-stub"}},
        )

        helper = self._operator_answers_dangerous(env)
        state = run_workflow(
            workflow, host="copilot", repo_root=self.repo, env=env, registry=self.registry
        )
        helper.finish()
        self.assertEqual("succeeded", state["status"], state)
        expected = {"routine": "policy:stub-model", "dangerous": "operator"}
        finished = {
            row["dispatch_id"]: row
            for row in map(json.loads, ledger.read_text().splitlines())
            if row.get("event") == "finished"
        }
        for task_id, source in expected.items():
            with self.subTest(task=task_id):
                attempts = state["tasks"][task_id]["attempts"]
                self.assertEqual(2, len(attempts))
                dispatch_id = attempts[0]["dispatchId"]
                self.assertEqual(dispatch_id, attempts[1]["dispatchId"])
                store = RunStore(state_root(env), dispatch_id)
                answer = store.mailbox().get_answer("0001")
                assert answer is not None
                self.assertEqual(source, answer["answered_by"])
                self.assertEqual(
                    "succeeded", json.loads(store.artifact("result.json").read_text())["status"]
                )
                self.assertEqual([f"0001:{source}"], finished[dispatch_id]["askResolutions"])
        dangerous_events = (
            RunStore(state_root(env), state["tasks"]["dangerous"]["attempts"][0]["dispatchId"])
            .artifact("events.jsonl")
            .read_text()
        )
        self.assertIn('"event":"ask.escalated"', dangerous_events)

    def test_tier1_children_that_block_on_an_ask_are_answered_while_they_run(self) -> None:
        stub = PolicyStub()
        self.addCleanup(stub.close)
        routes = Path(self.env["HOME"]) / ".config" / "pitwall" / "pitwall.toml"
        write_profiles(
            routes,
            {
                "schemaVersion": 1,
                "defaults": {"endpointHarness": "qwen"},
                "models": {"policy-stub": stub.entry},
            },
        )
        fake = self.root / "blocking-opencode"
        fake.write_text(
            _BLOCKING_OPENCODE.format(runtime=str(ROOT / "src"), hang_guard=HANG_GUARD_SECS),
            encoding="utf-8",
        )
        fake.chmod(0o755)
        env = self.production_env(OPENCODE_BIN=str(fake))
        workflow = self.workflow(
            {
                "routine": {**task(), "prompt": {"text": "ROUTINE task"}},
                "dangerous": {**task(), "prompt": {"text": "DANGEROUS task"}},
            },
            {"maxConcurrency": 2, "askSupport": True, "autoAnswer": {"route": "policy-stub"}},
        )

        helper = self._operator_answers_dangerous(env)
        state = run_workflow(
            workflow, host="copilot", repo_root=self.repo, env=env, registry=self.registry
        )
        helper.finish()
        self.assertEqual("succeeded", state["status"], state)
        for task_id, source in {"routine": "policy:stub-model", "dangerous": "operator"}.items():
            with self.subTest(task=task_id):
                attempts = state["tasks"][task_id]["attempts"]
                self.assertEqual(1, len(attempts))
                answer = (
                    RunStore(state_root(env), attempts[0]["dispatchId"])
                    .mailbox()
                    .get_answer("0001")
                )
                assert answer is not None
                self.assertEqual(source, answer["answered_by"])


if __name__ == "__main__":
    unittest.main()
