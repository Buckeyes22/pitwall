"""Default/policy answer sources, deadline expiry, and resume-time defaults (plan Task 3)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pitwall.agents.channel import (
    expire_overdue,
)
from pitwall.agents.dispatch import (
    build_resume_prompt,
)
from pitwall.agents.events import (
    EventEmitter,
)
from pitwall.agents.mailbox import (
    MailboxError,
    validate_answer,
)
from pitwall.agents.run_store import (
    RunStore,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH_ID = "00000000-0000-4000-8000-0000000000c3"
OPTIONS = [{"id": "a", "text": "x"}, {"id": "b", "text": "y"}]


def _age_ask(run_dir: Path, ask_id: str, *, seconds: int) -> None:
    path = run_dir / "mailbox" / "asks" / f"{ask_id}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["created_at"] = (
        (datetime.now(UTC) - timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    )
    path.write_text(json.dumps(doc), encoding="utf-8")


class AnswerSourceTests(unittest.TestCase):
    def _answer(self, answered_by: str, choice: str = "a") -> dict:
        return {
            "version": 1,
            "ask_id": "0001",
            "answered_by": answered_by,
            "choice": choice,
            "answered_at": "2026-09-10T00:00:00Z",
        }

    def test_sources(self) -> None:
        for good in (
            "operator",
            "orchestrator",
            "default",
            "policy:glm-5.3-flash",
            "policy:zai/glm-5.3",
        ):
            validate_answer(self._answer(good), options=["a", "b"])
        for bad in ("policy", "policy:", "policy: spaced", "cron", ""):
            with self.subTest(bad=bad), self.assertRaises(MailboxError):
                validate_answer(self._answer(bad), options=["a", "b"])

    def test_policy_may_not_abort(self) -> None:
        with self.assertRaises(MailboxError):
            validate_answer(self._answer("policy:m", choice="abort"), options=["a", "b"])


class ExpiryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.store = RunStore(Path(self._temp.name), DISPATCH_ID)
        self.store.path.mkdir(parents=True)
        self.emitter = EventEmitter(self.store, harness="test", model="m")

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _ask(self, deadline_s: int, default: str = "b") -> str:
        return str(
            self.store.mailbox().write_ask(
                blocked_on="choice",
                question="q",
                options=OPTIONS,
                default=default,
                deadline_s=deadline_s,
            )["ask_id"]
        )

    def test_only_overdue_asks_get_their_default_once(self) -> None:
        overdue, fresh = self._ask(60), self._ask(600)
        _age_ask(self.store.path, overdue, seconds=120)
        applied = expire_overdue(self.store, emitter=self.emitter)
        self.assertEqual([overdue], [a["ask_id"] for a in applied])
        answer = self.store.mailbox().get_answer(overdue)
        assert answer is not None
        self.assertEqual(("default", "b"), (answer["answered_by"], answer["choice"]))
        self.assertIsNone(self.store.mailbox().get_answer(fresh))
        self.assertEqual([], expire_overdue(self.store, emitter=self.emitter))
        events = [
            json.loads(line)
            for line in self.store.artifact("events.jsonl").read_text().splitlines()
        ]
        resolved = [e for e in events if e["event"] == "ask.resolved"]
        self.assertEqual(
            [{"askId": overdue, "resolvedBy": "default"}], [e["data"] for e in resolved]
        )

    def test_expired_operator_answer_is_replaced_by_default(self) -> None:
        ask_id = self._ask(60)
        _age_ask(self.store.path, ask_id, seconds=120)
        answer = self.store.mailbox().write_answer(ask_id, choice="a", answered_by="operator")
        self.assertEqual(("default", "b"), (answer["answered_by"], answer["choice"]))
        self.assertEqual([], expire_overdue(self.store, emitter=self.emitter))

    def test_resume_prompt_is_deterministic_and_marks_abort(self) -> None:
        ask = {
            "ask_id": "0001",
            "blocked_on": "choice",
            "question": "q",
            "default": "abort",
            "context": {"options": OPTIONS},
        }
        answer = {"answered_by": "default", "answered_at": "t", "choice": "abort"}
        first = build_resume_prompt(b"original\n", [(ask, answer)], 2)
        self.assertEqual(first, build_resume_prompt(b"original\n", [(ask, answer)], 2))
        self.assertIn(b"The orchestrator chose to abort", first)


if __name__ == "__main__":
    unittest.main()
