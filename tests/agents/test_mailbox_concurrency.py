"""Mailbox interleaving and flood properties (spec §11; plan Task 1)."""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from pitwall.agents.mailbox import (
    Mailbox,
    MailboxCapError,
    MailboxError,
)

ROOT = Path(__file__).resolve().parents[2]

DISPATCH = "00000000-0000-4000-8000-0000000000c1"
OPTIONS = [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}]


def _ask_in_process(run_dir: str, index: int) -> str:
    """Module level so ProcessPoolExecutor can import it in a worker."""
    try:
        ask = Mailbox(Path(run_dir), DISPATCH).write_ask(
            blocked_on="choice", question=f"q{index}", options=OPTIONS, default="a", deadline_s=600
        )
    except MailboxCapError:
        return "capped"
    return str(ask["ask_id"])


def _answer_in_process(run_dir: str, answered_by: str, choice: str) -> tuple[str, str]:
    """Race one explicit/default answer through separate processes."""

    try:
        answer = Mailbox(Path(run_dir), DISPATCH).write_answer(
            "0001", choice=choice, answered_by=answered_by
        )
    except MailboxError as exc:
        return ("error", str(exc))
    return ("ok", f"{answer['answered_by']}:{answer['choice']}")


def _run_threads(count: int, target) -> None:  # type: ignore[no-untyped-def]  # reason: test helper is intentionally left unannotated
    barrier = threading.Barrier(count)

    def wrapped(index: int) -> None:
        barrier.wait()
        target(index)

    threads = [threading.Thread(target=wrapped, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


class MailboxInterleavingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_concurrent_steer_writers_never_lose_a_message(self) -> None:
        for trial in range(20):
            run = self.root / f"steer-{trial}"
            _run_threads(
                8,
                lambda i, run=run: Mailbox(run, DISPATCH).write_steer(kind="note", message=f"m{i}"),
            )
            steers = Mailbox(run, DISPATCH).steers()
            self.assertEqual(
                sorted(f"m{i}" for i in range(8)), sorted(s["message"] for s in steers)
            )
            self.assertEqual([f"{n:04d}" for n in range(1, 9)], [s["steer_id"] for s in steers])

    def test_concurrent_acks_never_lose_a_message(self) -> None:
        run = self.root / "acks"
        box = Mailbox(run, DISPATCH)
        steer_ids = [box.write_steer(kind="note", message=f"m{i}")["steer_id"] for i in range(8)]
        _run_threads(8, lambda i: Mailbox(run, DISPATCH).write_ack(steer_ids[i]))
        self.assertEqual(
            sorted(steer_ids), sorted(a["steer_id"] for a in Mailbox(run, DISPATCH).acks())
        )

    def test_exactly_one_answer_wins_a_race(self) -> None:
        run = self.root / "answer"
        Mailbox(run, DISPATCH).write_ask(
            blocked_on="choice", question="q", options=OPTIONS, default="a", deadline_s=600
        )
        outcomes: list[str] = []
        lock = threading.Lock()

        def answer(index: int) -> None:
            try:
                Mailbox(run, DISPATCH).write_answer(
                    "0001",
                    choice="a" if index % 2 else "b",
                    answered_by="operator",
                    note=str(index),
                )
                result = f"won:{index}"
            except MailboxError:
                result = "lost"
            with lock:
                outcomes.append(result)

        _run_threads(6, answer)
        winners = [outcome for outcome in outcomes if outcome.startswith("won:")]
        self.assertEqual(1, len(winners), outcomes)
        stored = Mailbox(run, DISPATCH).get_answer("0001")
        assert stored is not None
        self.assertEqual(winners[0].split(":")[1], stored["note"])

    def test_expired_default_wins_cross_process_answer_race(self) -> None:
        run = self.root / "expired-answer"
        box = Mailbox(run, DISPATCH)
        box.write_ask(blocked_on="choice", question="q", options=OPTIONS, default="a", deadline_s=1)
        ask_path = run / "mailbox" / "asks" / "0001.json"
        ask = json.loads(ask_path.read_text(encoding="utf-8"))
        ask["created_at"] = datetime(2000, 1, 1, tzinfo=UTC).isoformat().replace("+00:00", "Z")
        ask_path.write_text(json.dumps(ask), encoding="utf-8")

        with ProcessPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    _answer_in_process,
                    [str(run), str(run)],
                    ["orchestrator", "default"],
                    ["b", "a"],
                )
            )

        stored = box.get_answer("0001")
        assert stored is not None
        self.assertEqual(("default", "a"), (stored["answered_by"], stored["choice"]))
        self.assertEqual(1, sum(result[0] == "ok" for result in results), results)
        self.assertEqual("default:a", next(result[1] for result in results if result[0] == "ok"))

    def test_ask_cap_holds_under_a_cross_process_flood(self) -> None:
        run = self.root / "flood"
        Mailbox(run, DISPATCH).ensure()
        with ProcessPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_ask_in_process, [str(run)] * 16, range(16)))
        self.assertEqual(
            ["0001", "0002", "0003", "0004", "0005"], sorted(r for r in results if r != "capped")
        )
        self.assertEqual(11, results.count("capped"))
        self.assertEqual(5, len(Mailbox(run, DISPATCH).asks()))

    def test_ids_stay_monotonic_after_the_newest_ask_is_quarantined(self) -> None:
        run = self.root / "monotonic"
        box = Mailbox(run, DISPATCH)
        box.write_ask(
            blocked_on="choice", question="q1", options=OPTIONS, default="a", deadline_s=600
        )
        (box.root / "asks" / "0002.json").write_text("{not json", encoding="utf-8")
        box.asks()  # quarantines 0002
        self.assertEqual(
            "0003",
            box.write_ask(
                blocked_on="choice", question="q3", options=OPTIONS, default="a", deadline_s=600
            )["ask_id"],
        )


if __name__ == "__main__":
    unittest.main()
