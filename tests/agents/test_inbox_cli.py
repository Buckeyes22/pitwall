"""`inbox` CLI: unresolved asks plus unacked steers across runs (Phase A, Task 10)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import UTC
from pathlib import Path

from pitwall.agents.mailbox import (
    Mailbox,
)
from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.shim_test_support import PITWALL

ROOT = Path(__file__).resolve().parents[2]


def _env(directory: str) -> dict[str, str]:
    return {
        "HOME": directory,
        "PATH": "/usr/bin:/bin",
        "XDG_STATE_HOME": str(Path(directory) / "state"),
        "XDG_CONFIG_HOME": str(Path(directory) / "config"),
    }


def _seed(directory: str) -> tuple[str, str]:
    """Two runs: one waiting ask plus one waiting steer; one settled ask."""
    env = _env(directory)
    first = "00000000-0000-4000-8000-0000000000c1"
    second = "00000000-0000-4000-8000-0000000000c2"
    RunStore.create(env, first)
    RunStore.create(env, second)
    root = Path(directory) / "state" / "pitwall" / "agents" / "runs"
    Mailbox(root / first, first).write_ask(
        blocked_on="choice",
        question="Which migration first?",
        options=[{"id": "a", "text": "0032"}, {"id": "b", "text": "0031"}],
        default="a",
        deadline_s=600,
    )
    Mailbox(root / first, first).write_ask(
        blocked_on="naming",
        question="Name it?",
        options=[{"id": "a", "text": "x"}],
        default="a",
        deadline_s=600,
    )
    Mailbox(root / first, first).write_answer("0002", choice="a", answered_by="operator")
    Mailbox(root / second, second).write_steer(kind="scope", message="SQLite only")
    return first, second


def _run_inbox(directory: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(PITWALL),
            "agents",
            "inbox",
            *args,
        ],
        capture_output=True,
        text=True,
        env=_env(directory),
        check=False,
    )


class InboxCliTests(unittest.TestCase):
    def test_table_lists_asks_and_steers_with_deadline_remaining(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first, second = _seed(directory)
            result = _run_inbox(directory)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(first, result.stdout)
            self.assertIn("0001", result.stdout)
            self.assertNotIn("0002", result.stdout)  # answered: settled, not waiting
            self.assertIn(second, result.stdout)
            self.assertIn("STEER", result.stdout)
            self.assertIn("SQLite only", result.stdout)
            # D1 deadline remaining is shown per ask (e.g. "9m" for a 600s deadline).
            self.assertRegex(result.stdout, r"ASK\t0001\t\d+m")

    def test_json_lists_asks_and_steers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            first, second = _seed(directory)
            result = _run_inbox(directory, "--json")
            self.assertEqual(0, result.returncode, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual([first], [a["dispatchId"] for a in payload["asks"]])
            self.assertEqual(["0001"], [a["askId"] for a in payload["asks"]])
            self.assertGreaterEqual(payload["asks"][0]["deadlineRemainingS"], 0)
            self.assertEqual([second], [s["dispatchId"] for s in payload["steers"]])
            self.assertEqual(["0001"], [s["steerId"] for s in payload["steers"]])

    def test_empty_inbox_reports_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = _run_inbox(directory)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("inbox empty", result.stdout)
            as_json = _run_inbox(directory, "--json")
            self.assertEqual(
                {"asks": [], "steers": [], "sequenceGaps": []}, json.loads(as_json.stdout)
            )

    def test_expired_asks_are_flagged_in_json_and_table(self) -> None:
        from datetime import datetime, timedelta

        with tempfile.TemporaryDirectory() as directory:
            first, _second = _seed(directory)
            ask_path = (
                Path(directory)
                / "state"
                / "pitwall"
                / "agents"
                / "runs"
                / first
                / "mailbox"
                / "asks"
                / "0001.json"
            )
            doc = json.loads(ask_path.read_text(encoding="utf-8"))
            doc["created_at"] = (
                (datetime.now(UTC) - timedelta(seconds=1200)).isoformat().replace("+00:00", "Z")
            )
            ask_path.write_text(json.dumps(doc), encoding="utf-8")
            as_json = _run_inbox(directory, "--json")
            self.assertEqual(0, as_json.returncode, as_json.stderr)
            payload = json.loads(as_json.stdout)
            self.assertTrue(payload["asks"][0]["expired"])
            self.assertLess(payload["asks"][0]["deadlineRemainingS"], 0)
            table = _run_inbox(directory)
            self.assertEqual(0, table.returncode, table.stderr)
            self.assertIn("expired", table.stdout)


if __name__ == "__main__":
    unittest.main()
