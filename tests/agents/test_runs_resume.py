"""Tier-4 prompt contract and `runs resume` (Phase A, Task 9)."""

from __future__ import annotations

import json
import subprocess
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pitwall.agents.mailbox import (
    DEFAULT_MAX_ASKS,
    Mailbox,
)
from tests.agents.shim_test_support import (
    PITWALL,
    ShimSandbox,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000b2"


def _age_ask(run_dir: Path, ask_id: str, *, seconds: int) -> None:
    path = run_dir / "mailbox" / "asks" / f"{ask_id}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["created_at"] = (
        (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(doc), encoding="utf-8")


_FAKE_HARNESS = """\
#!/usr/bin/env python3
\"\"\"Fake harness for resume tests: asks once, then completes.\"\"\"
import json
import os
import sys

sys.path.insert(0, {runtime!r})

mode = os.environ.get("FAKE_RESUME_MODE", "ask")
if mode == "ask":
    print("attempt-1 marker", flush=True)
    from pitwall.agents.mailbox import Mailbox
    from pitwall.agents.run_store import state_root

    env = dict(os.environ)
    dispatch_id = env.get("PITWALL_AGENTS_CHANNEL_DISPATCH_ID") or env["PITWALL_AGENTS_DISPATCH_ID"]
    run_dir = state_root(env) / "runs" / dispatch_id
    box = Mailbox(run_dir, dispatch_id)
    box.write_ask(
        blocked_on="choice",
        question="Apply 0032 then 0031?",
        options=[
            {{"id": "a", "text": "Apply 0032 then 0031"}},
            {{"id": "b", "text": "Rebase 0031 onto 0032"}},
        ],
        default="b",
        deadline_s=600,
    )
    sys.exit(75)

prompt = sys.stdin.read()
capture = os.environ.get("CAPTURE_FILE")
if capture:
    with open(capture, "w", encoding="utf-8") as handle:
        json.dump(
            {{
                "dispatch_id": os.environ.get("PITWALL_AGENTS_CHANNEL_DISPATCH_ID")
                or os.environ.get("PITWALL_AGENTS_DISPATCH_ID"),
                "attempt": os.environ.get("PITWALL_AGENTS_CHANNEL_ATTEMPT")
                or os.environ.get("PITWALL_AGENTS_ATTEMPT"),
                "prompt": prompt,
            }},
            handle,
        )
print("attempt-2 marker", flush=True)
sys.exit(0)
"""


class RunsResumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandboxes: list[ShimSandbox] = []

    def tearDown(self) -> None:
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def _sandbox(self) -> ShimSandbox:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        target = sandbox.bin / "codex"
        target.write_text(_FAKE_HARNESS.format(runtime=str(ROOT / "src")), encoding="utf-8")
        target.chmod(0o755)
        return sandbox

    def _env(self, sandbox: ShimSandbox, mode: str) -> dict[str, str]:
        return sandbox.environment(
            PITWALL_AGENTS_DISPATCH_ID=DISPATCH_ID,
            PITWALL_AGENTS_ASK_SUPPORT="1",
            FAKE_RESUME_MODE=mode,
            CAPTURE_FILE=str(sandbox.root / "captured.json"),
        )

    def _dispatch(
        self, sandbox: ShimSandbox, prompt: Path, mode: str
    ) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(prompt),
            ],
            capture_output=True,
            env=self._env(sandbox, mode),
            check=False,
        )

    def _resume(self, sandbox: ShimSandbox, mode: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "runs",
                "resume",
                DISPATCH_ID,
            ],
            capture_output=True,
            env=self._env(sandbox, mode),
            check=False,
        )

    def _run_dir(self, sandbox: ShimSandbox) -> Path:
        return sandbox.state / "pitwall" / "agents" / "runs" / DISPATCH_ID

    def _received_prompt(self, sandbox: ShimSandbox) -> str:
        """The prompt the resumed attempt read; a finished run removes prompt.deliver.md."""
        captured = json.loads((sandbox.root / "captured.json").read_text(encoding="utf-8"))
        self.assertFalse((self._run_dir(sandbox) / "prompt.deliver.md").exists())
        prompt = captured["prompt"]
        assert isinstance(prompt, str)
        return prompt

    def _answer(self, sandbox: ShimSandbox) -> None:
        from pitwall.agents.mailbox import Mailbox
        from pitwall.agents.run_store import state_root

        run_dir = state_root(self._env(sandbox, "done")) / "runs" / DISPATCH_ID
        Mailbox(run_dir, DISPATCH_ID).write_answer(
            "0001", choice="a", answered_by="operator", note="0032 renames first"
        )

    def test_resume_reuses_dispatch_id_bumps_attempt_and_appends_qa(self) -> None:
        sandbox = self._sandbox()
        paused = self._dispatch(sandbox, sandbox.prompt(), "ask")
        self.assertEqual(75, paused.returncode)

        run_dir = self._run_dir(sandbox)
        retained = (run_dir / "prompt.deliver.md").read_text(encoding="utf-8")
        self.assertIn("tier-4 ask contract", retained)
        self.assertIn("test prompt", retained)
        resume_record = json.loads((run_dir / "resume.json").read_text(encoding="utf-8"))
        self.assertEqual("codex", resume_record["provider"])

        self._answer(sandbox)
        resumed = self._resume(sandbox, "done")
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        self.assertTrue(resumed.stdout.endswith(b"SHIM-DONE exit=0\n"), resumed.stdout)

        captured = json.loads((sandbox.root / "captured.json").read_text(encoding="utf-8"))
        self.assertEqual(DISPATCH_ID, captured["dispatch_id"])
        self.assertEqual("2", captured["attempt"])

        run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("succeeded", run["state"])
        self.assertEqual(2, run["attempt"])
        self.assertTrue((run_dir / "result.json").is_file())

        rebuilt = self._received_prompt(sandbox)
        original_at = rebuilt.index("test prompt")
        clarifications_at = rebuilt.index("# Clarifications (resume attempt 2)")
        question_at = rebuilt.index("Apply 0032 then 0031?")
        choice_at = rebuilt.index("Choice: a")
        self.assertLess(original_at, clarifications_at)
        self.assertLess(clarifications_at, question_at)
        self.assertLess(question_at, choice_at)

        ledger_rows = [
            json.loads(line)
            for line in sandbox.ledger.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        paused_rows = [row for row in ledger_rows if row.get("event") == "paused"]
        self.assertEqual(1, len(paused_rows))
        self.assertEqual(["0001"], paused_rows[0].get("askIds"))
        finished_rows = [row for row in ledger_rows if row.get("event") == "finished"]
        resumed_finish = [row for row in finished_rows if row.get("attempt") == 2]
        self.assertEqual(1, len(resumed_finish))
        self.assertEqual("ok", resumed_finish[0].get("outcome"))
        self.assertEqual(["0001:operator"], resumed_finish[0].get("askResolutions"))

    def test_resume_replays_the_retention_request(self) -> None:
        sandbox = self._sandbox()
        paused = subprocess.run(
            [
                str(PITWALL),
                "agents",
                "dispatch",
                "codex",
                str(sandbox.prompt()),
                "--routing-retain-prompt",
            ],
            capture_output=True,
            env=self._env(sandbox, "ask"),
            check=False,
        )
        self.assertEqual(75, paused.returncode)
        run_dir = self._run_dir(sandbox)
        self.assertTrue(json.loads((run_dir / "resume.json").read_text())["retainPrompt"])
        self._answer(sandbox)
        resumed = self._resume(sandbox, "done")
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        self.assertTrue((run_dir / "prompt.deliver.md").is_file())
        request = json.loads((run_dir / "request.json").read_text(encoding="utf-8"))
        self.assertTrue(request["promptSource"]["retained"])

    def test_resume_appends_the_tier4_suffix_exactly_once(self) -> None:
        sandbox = self._sandbox()
        self.assertEqual(75, self._dispatch(sandbox, sandbox.prompt(), "ask").returncode)
        self._answer(sandbox)
        self.assertEqual(0, self._resume(sandbox, "done").returncode)
        rebuilt = self._received_prompt(sandbox)
        self.assertEqual(1, rebuilt.count("# Orchestrator channel (tier-4 ask contract)"))

    def test_resume_without_replay_inputs_leaves_the_retained_prompt_untouched(self) -> None:
        sandbox = self._sandbox()
        self.assertEqual(75, self._dispatch(sandbox, sandbox.prompt(), "ask").returncode)
        self._answer(sandbox)
        run_dir = self._run_dir(sandbox)
        before = (run_dir / "prompt.deliver.md").read_bytes()
        (run_dir / "resume.json").unlink()
        resumed = self._resume(sandbox, "done")
        self.assertNotEqual(0, resumed.returncode)
        self.assertIn(b"kept no replay inputs", resumed.stderr)
        self.assertEqual(before, (run_dir / "prompt.deliver.md").read_bytes())

    def test_resume_with_unresolved_asks_fails_closed(self) -> None:
        sandbox = self._sandbox()
        self.assertEqual(75, self._dispatch(sandbox, sandbox.prompt(), "ask").returncode)
        resumed = self._resume(sandbox, "done")
        self.assertNotEqual(0, resumed.returncode)
        self.assertIn(b"0001", resumed.stderr)
        self.assertIn(b"answer them before resuming", resumed.stderr)
        run = json.loads((self._run_dir(sandbox) / "run.json").read_text(encoding="utf-8"))
        self.assertEqual("paused", run["state"])

    def test_resume_without_a_retained_prompt_fails_closed(self) -> None:
        sandbox = self._sandbox()
        self.assertEqual(75, self._dispatch(sandbox, sandbox.prompt(), "ask").returncode)
        self._answer(sandbox)
        run_dir = self._run_dir(sandbox)
        (run_dir / "prompt.deliver.md").unlink()
        resumed = self._resume(sandbox, "done")
        self.assertNotEqual(0, resumed.returncode)
        self.assertIn(b"refusing to resume a guessed prompt", resumed.stderr)
        self.assertFalse((sandbox.root / "captured.json").exists())

    def test_resume_of_a_non_paused_run_fails_closed(self) -> None:
        sandbox = self._sandbox()
        done = self._dispatch(sandbox, sandbox.prompt(), "done")
        self.assertEqual(0, done.returncode)
        resumed = self._resume(sandbox, "done")
        self.assertNotEqual(0, resumed.returncode)
        self.assertIn(b"is not paused", resumed.stderr)

    def test_resume_preserves_attempt_one_output_logs(self) -> None:
        sandbox = self._sandbox()
        paused = self._dispatch(sandbox, sandbox.prompt(), "ask")
        self.assertEqual(75, paused.returncode)
        self._answer(sandbox)
        resumed = self._resume(sandbox, "done")
        self.assertEqual(0, resumed.returncode, resumed.stderr)

        run_dir = self._run_dir(sandbox)
        rotated = run_dir / "stdout.attempt1.log"
        self.assertTrue(rotated.is_file(), "attempt-1 stdout must be rotated, not truncated")
        rotated_text = rotated.read_text(encoding="utf-8")
        self.assertIn("attempt-1 marker", rotated_text)
        self.assertNotIn("attempt-2 marker", rotated_text)
        fresh = (run_dir / "stdout.log").read_text(encoding="utf-8")
        self.assertIn("attempt-2 marker", fresh)
        self.assertNotIn("attempt-1 marker", fresh)
        rotated_mode = rotated.stat().st_mode & 0o777
        self.assertEqual(0o600, rotated_mode)
        self.assertTrue((run_dir / "stderr.attempt1.log").is_file())
        self.assertEqual(0, (run_dir / "stderr.attempt1.log").stat().st_size)

    def test_resume_applies_expired_defaults_without_an_operator(self) -> None:
        sandbox = self._sandbox()
        self.assertEqual(75, self._dispatch(sandbox, sandbox.prompt(), "ask").returncode)
        _age_ask(self._run_dir(sandbox), "0001", seconds=4000)
        resumed = self._resume(sandbox, "done")
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        self.assertIn(b"ask 0001 expired; applied its default 'b'", resumed.stderr)
        rebuilt = self._received_prompt(sandbox)
        self.assertIn("## A (by default at", rebuilt)
        finished = [row for row in sandbox.ledger_records() if row.get("event") == "finished"]
        self.assertEqual(["0001:default"], finished[-1]["askResolutions"])

    def test_resume_delivers_steering_written_while_paused(self) -> None:
        sandbox = self._sandbox()
        self.assertEqual(75, self._dispatch(sandbox, sandbox.prompt(), "ask").returncode)
        run_dir = self._run_dir(sandbox)
        box = Mailbox(run_dir, DISPATCH_ID)
        box.write_steer(kind="scope", message="SQLite only")
        self._answer(sandbox)
        resumed = self._resume(sandbox, "done")
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        rebuilt = self._received_prompt(sandbox)
        self.assertIn("# Orchestrator steering (delivered on resume)", rebuilt)
        self.assertIn("[scope 0001] SQLite only", rebuilt)
        acks = Mailbox(run_dir, DISPATCH_ID).acks()
        self.assertEqual(1, len(acks))
        self.assertEqual("delivered on resume", acks[0].get("note"))

    def test_tier4_suffix_promotes_honest_deadline_semantics(self) -> None:
        from pitwall.agents.dispatch import tier4_prompt_suffix

        rendered = tier4_prompt_suffix(
            Path("/state/runs/dispatch/mailbox"), "dispatch", max_asks=DEFAULT_MAX_ASKS
        )
        flattened = " ".join(rendered.split())
        self.assertIn(
            "your stated default is applied by the orchestrator when the run is resumed", flattened
        )
        self.assertNotIn("resumes without you", flattened)
        self.assertIn("The run stays paused until then.", flattened)


if __name__ == "__main__":
    unittest.main()
