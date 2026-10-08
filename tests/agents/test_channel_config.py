"""channel.json, D1 derived deadlines, and the per-dispatch ask cap (plan Task 2)."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pitwall.agents.channel import (
    ChannelConfig,
    effective_deadline_s,
    load_channel_config,
    write_channel_config,
)
from pitwall.agents.mailbox import (
    MailboxCapError,
)
from pitwall.agents.run_store import (
    RunStore,
)
from tests.agents.shim_test_support import (
    PITWALL,
    ShimSandbox,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000c2"

_ASKING_HARNESS = """\
#!/usr/bin/env python3
import os, sys
sys.path.insert(0, {runtime!r})
from pathlib import Path
from pitwall.agents.mailbox import Mailbox
from pitwall.agents.run_store import state_root
# The channel variable arrives in Task 11; the fallback keeps this fake valid before and after Task 12.
dispatch_id = os.environ.get("PITWALL_AGENTS_CHANNEL_DISPATCH_ID") or os.environ["PITWALL_AGENTS_DISPATCH_ID"]
box = Mailbox(state_root(dict(os.environ)) / "runs" / dispatch_id, dispatch_id, max_asks=99)
for index in range(int(os.environ.get("FAKE_ASKS", "1"))):
    box.write_ask(blocked_on="choice", question=f"q{{index}}",
                  options=[{{"id": "a", "text": "x"}}, {{"id": "b", "text": "y"}}],
                  default="a", deadline_s=3600)
sys.exit(75)
"""


class ChannelConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.state = Path(self._temp.name)
        self.store = RunStore(self.state, DISPATCH_ID)
        self.store.path.mkdir(parents=True)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_round_trip_and_strict_bounds(self) -> None:
        config = ChannelConfig(
            DISPATCH_ID, max_asks=2, timeout_seconds=600.0, attempt_started_epoch=100.0
        )
        write_channel_config(self.store, config)
        self.assertEqual(config, load_channel_config(self.store.path))
        for bad in ({"maxAsks": 0}, {"maxAsks": 51}, {"tier": "3"}, {"timeoutSeconds": 0}):
            doc = {**config.to_json(), **bad}
            self.store.write_json("channel.json", doc)
            self.assertIsNone(load_channel_config(self.store.path), bad)

    def test_d1_cap_clamps_the_effective_deadline(self) -> None:
        started = time.time()
        config = ChannelConfig(DISPATCH_ID, timeout_seconds=600.0, attempt_started_epoch=started)
        created = datetime.fromtimestamp(started, tz=UTC).isoformat().replace("+00:00", "Z")
        ask = {"deadline_s": 3600, "created_at": created}
        self.assertEqual(90, effective_deadline_s(ask, config))  # 15% of 600 s
        self.assertEqual(3600, effective_deadline_s(ask, None))
        late = (datetime.fromtimestamp(started, tz=UTC) + timedelta(seconds=400)).isoformat()
        self.assertEqual(30, effective_deadline_s({"deadline_s": 3600, "created_at": late}, config))

    def test_store_mailbox_reads_the_cap_from_channel_json(self) -> None:
        write_channel_config(self.store, ChannelConfig(DISPATCH_ID, max_asks=2))
        box = self.store.mailbox()
        for _ in range(2):
            box.write_ask(
                blocked_on="choice",
                question="q",
                options=[{"id": "a", "text": "x"}],
                default="a",
                deadline_s=60,
            )
        with self.assertRaises(MailboxCapError):
            box.write_ask(
                blocked_on="choice",
                question="q",
                options=[{"id": "a", "text": "x"}],
                default="a",
                deadline_s=60,
            )


class DispatchCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        target = self.sandbox.bin / "codex"
        target.write_text(_ASKING_HARNESS.format(runtime=str(ROOT / "src")), encoding="utf-8")
        target.chmod(0o755)

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def _dispatch(self, *extra: str, **env: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(self.sandbox.prompt()),
                "--routing-ask-support",
                *extra,
            ],
            capture_output=True,
            env=self.sandbox.environment(
                PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID, PITWALL_AGENTS_TIMEOUT_SECS="600", **env
            ),
            check=False,
        )

    def _run_dir(self) -> Path:
        return self.sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID

    def test_routing_flag_sets_cap_suffix_and_resume_record(self) -> None:
        result = self._dispatch("--routing-max-asks", "2")
        self.assertEqual(75, result.returncode, result.stderr)
        run_dir = self._run_dir()
        config = load_channel_config(run_dir)
        assert config is not None
        self.assertEqual(2, config.max_asks)
        self.assertEqual(600.0, config.timeout_seconds)
        prompt = (run_dir / "prompt.deliver.md").read_text(encoding="utf-8")
        self.assertIn("at most 2", prompt)
        self.assertIn("`deadline_s` is at most 90", prompt)
        self.assertEqual(
            2, json.loads((run_dir / "resume.json").read_text(encoding="utf-8"))["maxAsks"]
        )

    def test_env_sets_the_cap_and_bad_values_are_usage_errors(self) -> None:
        self.assertEqual(75, self._dispatch(PITWALL_AGENTS_MAX_ASKS="3").returncode)
        config = load_channel_config(self._run_dir())
        assert config is not None
        self.assertEqual(3, config.max_asks)
        for bad in ("0", "51", "x"):
            with self.subTest(bad=bad):
                result = subprocess.run(
                    [
                        str(PITWALL),
                        "agents",
                        "dispatch",
                        "codex",
                        str(self.sandbox.prompt()),
                        f"--routing-max-asks={bad}",
                    ],
                    capture_output=True,
                    env=self.sandbox.environment(),
                    check=False,
                )
                self.assertEqual(64, result.returncode)
                self.assertIn(b"--routing-max-asks expects an integer 1..50", result.stderr)

    def test_pause_quarantines_tier4_asks_past_the_cap(self) -> None:
        result = self._dispatch("--routing-max-asks", "2", FAKE_ASKS="3")
        self.assertEqual(75, result.returncode, result.stderr)
        run_dir = self._run_dir()
        pause = json.loads((run_dir / "pause.json").read_text(encoding="utf-8"))
        self.assertEqual(["0001", "0002"], pause["pendingAskIds"])
        dead = [path.name for path in (run_dir / "mailbox" / "dead-letter").iterdir()]
        self.assertEqual(["asks_0003.json"], dead)


if __name__ == "__main__":
    unittest.main()
