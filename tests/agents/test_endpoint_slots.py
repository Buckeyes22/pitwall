"""Endpoint slots cap concurrent dispatches and survive crashed holders."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pitwall.agents import endpoints as slots
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]


class SlotTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"PITWALL_AGENTS_STATE_HOME": self.tmp.name}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_capacity_is_enforced_then_freed(self) -> None:
        with (
            slots.acquire_slot(self.env, "ms", 1, wait_seconds=0),
            self.assertRaises(slots.EndpointBusy),
            slots.acquire_slot(self.env, "ms", 1, wait_seconds=0.3, poll_seconds=0.05),
        ):
            pass
        with slots.acquire_slot(self.env, "ms", 1, wait_seconds=0) as index:
            self.assertEqual(0, index)

    def test_slot_is_released_when_holder_process_dies(self) -> None:
        code = (
            "import sys, time; sys.path.insert(0, sys.argv[1]);"
            "from pitwall.agents import endpoints as s;"
            "cm = s.acquire_slot({'PITWALL_AGENTS_STATE_HOME': sys.argv[2]}, 'ms', 1, wait_seconds=0);"
            "cm.__enter__(); print('held', flush=True); time.sleep(60)"
        )
        holder = subprocess.Popen(
            [sys.executable, "-c", code, str(ROOT / "src"), self.tmp.name],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual("held", holder.stdout.readline().strip())  # type: ignore[union-attr]  # reason: stdout is a pipe because the process was opened with stdout=PIPE
            with (
                self.assertRaises(slots.EndpointBusy),
                slots.acquire_slot(self.env, "ms", 1, wait_seconds=0),
            ):
                pass
            holder.kill()
            holder.wait(timeout=HANG_GUARD_SECS)
            with slots.acquire_slot(self.env, "ms", 1, wait_seconds=2):
                pass
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=HANG_GUARD_SECS)
            if holder.stdout is not None:
                holder.stdout.close()

    def test_lockout_round_trip(self) -> None:
        now = datetime(2026, 9, 26, tzinfo=UTC)
        self.assertIsNone(slots.read_lockout(self.env, "ms", now=now))
        slots.write_lockout(self.env, "ms", now + timedelta(days=3))
        self.assertEqual("2026-09-29T00:00:00+00:00", slots.read_lockout(self.env, "ms", now=now))
        self.assertIsNone(slots.read_lockout(self.env, "ms", now=now + timedelta(days=4)))

    def test_exhaustion_is_detected_in_run_output(self) -> None:
        run = Path(self.tmp.name) / "run"
        run.mkdir()
        (run / "stdout.log").write_text("ok\n", encoding="utf-8")
        (run / "stderr.log").write_text(
            "Error: 429 insufficient_quota: Your token-plan quota has been exhausted.\n",
            encoding="utf-8",
        )
        self.assertTrue(slots.output_reports_exhaustion(run))
        (run / "stderr.log").write_text("", encoding="utf-8")
        self.assertFalse(slots.output_reports_exhaustion(run))


if __name__ == "__main__":
    unittest.main()
