"""§9.3 ledger fields: ask count/rate/resolutions, steer count/ack latency (plan Task 4)."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from pitwall.agents.channel import (
    load_channel_config,
)
from tests.agents.shim_test_support import (
    PITWALL,
    ShimSandbox,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000c4"

# Simulates a tier-1 session: the ask is answered mid-run and a steer is acked.
_TIER1_HARNESS = """\
#!/usr/bin/env python3
import os, sys
sys.path.insert(0, {runtime!r})
from pitwall.agents.mailbox import Mailbox
from pitwall.agents.run_store import state_root
dispatch_id = os.environ.get("PITWALL_AGENTS_CHANNEL_DISPATCH_ID") or os.environ["PITWALL_AGENTS_DISPATCH_ID"]
box = Mailbox(state_root(dict(os.environ)) / "runs" / dispatch_id, dispatch_id)
if os.environ.get("FAKE_MODE") == "channel":
    box.write_ask(blocked_on="naming", question="suffix?", options=[{{"id": "a", "text": "x"}}],
                  default="a", deadline_s=60)
    box.write_answer("0001", choice="a", answered_by="orchestrator")
    steer = box.write_steer(kind="scope", message="narrow")
    box.write_ack(steer["steer_id"])
if os.environ.get("FAKE_MODE") == "unacked":
    box.write_steer(kind="scope", message="narrow")
print("done", flush=True)
"""


class LedgerFieldTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(_TIER1_HARNESS.format(runtime=str(ROOT / "src")), encoding="utf-8")
        target.chmod(0o755)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _dispatch(self, mode: str, *extra: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(self.sandbox.prompt()),
                *extra,
            ],
            capture_output=True,
            env=self.sandbox.environment(PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID, FAKE_MODE=mode),
            check=False,
        )

    def _finished(self) -> dict:
        rows = [row for row in self.sandbox.ledger_records() if row.get("event") == "finished"]
        self.assertEqual(1, len(rows))
        return rows[0]

    def test_channel_run_records_ask_and_steer_aggregates(self) -> None:
        self.assertEqual(0, self._dispatch("channel", "--routing-ask-support").returncode)
        row = self._finished()
        self.assertEqual(1, row["askCount"])
        self.assertEqual(["0001:orchestrator"], row["askResolutions"])
        self.assertEqual(1, row["steerCount"])
        self.assertEqual(1, len(row["steerAckLatencyS"]))
        self.assertGreaterEqual(row["steerAckLatencyS"][0], 0)
        self.assertGreater(row["askRatePerHour"], 0)
        self.assertEqual([], row["unackedSteerIds"])

    def test_steer_left_unacked_is_in_the_row_and_the_terminal_event(self) -> None:
        self.assertEqual(0, self._dispatch("unacked").returncode)
        self.assertEqual(["0001"], self._finished()["unackedSteerIds"])
        events = [
            json.loads(line)
            for line in (
                self.sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID / "events.jsonl"
            )
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        (unacked,) = [event for event in events if event["event"] == "steer.unacked"]
        self.assertTrue(unacked["data"]["atExit"])
        terminal = events[-1]
        self.assertEqual(["0001"], terminal["data"]["unackedSteerIds"])

    def test_run_without_a_mailbox_keeps_the_old_row_shape(self) -> None:
        self.assertEqual(0, self._dispatch("plain").returncode)
        row = self._finished()
        for key in (
            "askCount",
            "askRatePerHour",
            "askResolutions",
            "steerCount",
            "steerAckLatencyS",
        ):
            self.assertNotIn(key, row)


class WallAccumulationTests(unittest.TestCase):
    def test_pause_accumulates_prior_wall_seconds(self) -> None:
        from tests.agents.test_runs_resume import _FAKE_HARNESS
        from tests.agents.test_runs_resume import DISPATCH_ID as RESUME_ID

        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        target = sandbox.bin / "codex"
        target.write_text(_FAKE_HARNESS.format(runtime=str(ROOT / "src")), encoding="utf-8")
        target.chmod(0o755)
        env = sandbox.environment(
            PITWALL_AGENTS_DISPATCH_ID=RESUME_ID,
            PITWALL_AGENTS_ASK_SUPPORT="1",
            FAKE_RESUME_MODE="ask",
        )
        subprocess.run(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(sandbox.prompt()),
            ],
            capture_output=True,
            env=env,
            check=False,
        )
        run_dir = sandbox.state / "pitwall" / "agents" / "runs" / RESUME_ID
        paused = [row for row in sandbox.ledger_records() if row.get("event") == "paused"][0]
        config = load_channel_config(run_dir)
        assert config is not None
        self.assertEqual(paused["wall_s"], config.wall_seconds_prior)


if __name__ == "__main__":
    unittest.main()
