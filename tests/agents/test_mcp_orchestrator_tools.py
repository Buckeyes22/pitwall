"""Orchestrator-role MCP tools and role separation (plan Task 15)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.mcp_test_client import (
    McpTestClient,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000d5"


class OrchestratorToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        root = Path(self._temp.name)
        self.store = RunStore(root / "state" / "pitwall" / "agents", DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self.store.mailbox().write_ask(
            blocked_on="choice",
            question="Apply 0032 first?",
            options=[{"id": "a", "text": "yes"}, {"id": "b", "text": "no"}],
            default="b",
            deadline_s=600,
        )
        self.client = McpTestClient(
            {
                "HOME": str(root),
                "XDG_STATE_HOME": str(root / "state"),
                "PATH": os.environ.get("PATH", ""),
            }
        )
        self.client.initialize()

    def tearDown(self) -> None:
        self.client.close()
        self._temp.cleanup()

    def test_only_orchestrator_tools_are_listed(self) -> None:
        names = {tool["name"] for tool in self.client.request("tools/list")["result"]["tools"]}
        self.assertEqual(
            {
                "inbox",
                "answer_ask",
                "dispatch_and_wait",
                "answer_and_wait",
                "wait_dispatch",
                "steer_and_wait",
            },
            names,
        )
        refused = self.client.request("tools/call", {"name": "ask_orchestrator", "arguments": {}})
        self.assertEqual(-32602, refused["error"]["code"])

    def test_inbox_then_answer(self) -> None:
        inbox = self.client.call("inbox", {})["result"]["structuredContent"]
        self.assertEqual(["0001"], [a["askId"] for a in inbox["asks"]])
        self.assertEqual(
            f"pitwall agents runs diff {DISPATCH_ID}", inbox["asks"][0]["contextCommand"]
        )
        reply = self.client.call(
            "answer_ask",
            {
                "dispatch_id": DISPATCH_ID,
                "ask_id": "0001",
                "choice": "a",
                "note": "0032 renames first",
            },
        )["result"]
        self.assertFalse(reply["isError"])
        self.assertEqual("orchestrator", reply["structuredContent"]["answer"]["answered_by"])
        self.assertEqual([], self.client.call("inbox", {})["result"]["structuredContent"]["asks"])

    def test_refusals_are_is_error_results(self) -> None:
        self.assertTrue(self.client.call("inbox", {"dispatch_id": "ffffffff"})["result"]["isError"])
        bad = self.client.call(
            "answer_ask", {"dispatch_id": DISPATCH_ID, "ask_id": "0001", "choice": "zzz"}
        )
        self.assertTrue(bad["result"]["isError"])
        policy = self.client.call(
            "answer_ask",
            {
                "dispatch_id": DISPATCH_ID,
                "ask_id": "0001",
                "choice": "a",
                "answered_by": "policy:x",
            },
        )
        self.assertTrue(
            policy["result"]["isError"]
        )  # the policy source belongs to the scheduler, not a tool


if __name__ == "__main__":
    unittest.main()
