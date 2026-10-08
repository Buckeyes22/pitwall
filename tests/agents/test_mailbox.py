"""Mailbox schemas, atomic writes, dead-letter, and permission tests (Phase A)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from pitwall.agents.mailbox import (
    BODY_CAP_BYTES,
    DEFAULT_MAX_ASKS,
    Mailbox,
    MailboxError,
    MailboxOpenAskError,
    derive_deadline_s,
)
from tests.agents.mailbox_probe import OpenAskContention
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]

DISPATCH = "00000000-0000-4000-8000-000000000001"


def _options(*ids: str) -> list[dict[str, str]]:
    return [{"id": i, "text": f"option {i}"} for i in ids]


def _ask_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "blocked_on": "choice",
        "question": "Apply 0032 then 0031?",
        "options": _options("a", "b"),
        "default": "b",
        "deadline_s": 600,
    }
    kwargs.update(overrides)
    return kwargs  # type: ignore[return-value]  # reason: test helper returns a loosely typed mapping


class MailboxSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.box = Mailbox(Path(self._tmp.name) / "runs" / DISPATCH, DISPATCH)

    def test_write_and_read_ask_round_trip(self) -> None:
        ask = self.box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual("0001", ask["ask_id"])
        self.assertEqual(DISPATCH, ask["dispatch_id"])
        self.assertEqual(1, len(self.box.pending_asks()))
        second = self.box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual("0002", second["ask_id"])

    def test_missing_default_is_rejected(self) -> None:
        kwargs = _ask_kwargs()
        del kwargs["default"]
        with self.assertRaises(MailboxError):
            self.box.write_ask(**kwargs)  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual([], list((self.box.root / "asks").glob("*.json")))

    def test_more_than_eight_options_is_rejected(self) -> None:
        with self.assertRaises(MailboxError):
            self.box.write_ask(
                **_ask_kwargs(options=_options(*[f"o{i}" for i in range(9)]))  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
            )

    def test_eight_options_is_accepted(self) -> None:
        ask = self.box.write_ask(
            **_ask_kwargs(options=_options(*[f"o{i}" for i in range(8)]), default="o1")  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        )
        self.assertEqual(8, len(ask["context"]["options"]))

    def test_unknown_blocked_on_is_rejected(self) -> None:
        with self.assertRaises(MailboxError):
            self.box.write_ask(**_ask_kwargs(blocked_on="vibes"))  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument

    def test_body_cap_is_enforced_on_write(self) -> None:
        with self.assertRaises(MailboxError):
            self.box.write_ask(**_ask_kwargs(question="q" * (BODY_CAP_BYTES + 1)))  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument

    def test_default_must_name_an_option_or_abort(self) -> None:
        with self.assertRaises(MailboxError):
            self.box.write_ask(**_ask_kwargs(default="zzz"))  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        aborted = self.box.write_ask(**_ask_kwargs(default="abort"))  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.assertEqual("abort", aborted["default"])

    def test_ask_cap_defaults_to_five_and_is_overridable(self) -> None:
        self.assertEqual(5, DEFAULT_MAX_ASKS)
        for _ in range(5):
            self.box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        with self.assertRaises(MailboxError):
            self.box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        roomy = Mailbox(Path(self._tmp.name) / "runs" / "other", "other", max_asks=6)
        for _ in range(6):
            roomy.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument

    def test_answer_resolves_exactly_once(self) -> None:
        self.box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        answer = self.box.write_answer("0001", choice="a", answered_by="operator")
        self.assertEqual("0001", answer["ask_id"])
        self.assertEqual([], self.box.pending_asks())
        with self.assertRaises(MailboxError):
            self.box.write_answer("0001", choice="a", answered_by="operator")

    def test_answer_to_unknown_ask_fails_closed(self) -> None:
        with self.assertRaises(MailboxError):
            self.box.write_answer("0009", choice="a", answered_by="operator")

    def test_answer_choice_must_select_an_option(self) -> None:
        self.box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        with self.assertRaises(MailboxError):
            self.box.write_answer("0001", choice="zzz", answered_by="policy")
        with self.assertRaises(MailboxError):
            self.box.write_answer("0001", choice="a", answered_by="cron")

    def test_steer_kinds_are_enumerated(self) -> None:
        for kind in ("note", "scope", "budget", "priority", "stop"):
            steer = self.box.write_steer(kind=kind, message="m")
            self.assertEqual(kind, steer["kind"])
        with self.assertRaises(MailboxError):
            self.box.write_steer(kind="nudge", message="m")

    def test_unacked_steers_and_ack_flow(self) -> None:
        self.box.write_steer(kind="note", message="fyi", requires_ack=False)
        tracked = self.box.write_steer(kind="scope", message="narrow it")
        self.assertEqual([tracked["steer_id"]], [s["steer_id"] for s in self.box.unacked_steers()])
        self.box.write_ack(tracked["steer_id"])
        self.assertEqual([], self.box.unacked_steers())

    def test_derive_deadline_s_follows_d1(self) -> None:
        self.assertEqual(3600, derive_deadline_s(100000.0))
        self.assertEqual(900, derive_deadline_s(6000.0))
        self.assertEqual(1, derive_deadline_s(0.0))

    def test_summary_counts_for_mailbox_json(self) -> None:
        self.box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        self.box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
        steer = self.box.write_steer(kind="note", message="m")
        self.box.write_answer("0001", choice="a", answered_by="orchestrator")
        self.box.write_ack(steer["steer_id"])
        summary = self.box.summary()
        self.assertEqual(
            {"asks": 2, "answers": 1, "steers": 1},
            {
                "asks": summary["asks"],
                "answers": summary["answers"],
                "steers": summary["steers"],
            },
        )
        self.assertEqual(["0002"], summary["unresolvedAskIds"])
        self.assertEqual([], summary["unackedSteerIds"])
        self.assertEqual(steer["steer_id"], summary["lastAckedSteerId"])


class MailboxAtomicityTests(unittest.TestCase):
    def test_concurrent_writer_reader_never_sees_partial_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            box = Mailbox(Path(directory) / "runs" / DISPATCH, DISPATCH)
            stop = threading.Event()
            errors: list[BaseException] = []

            def reader() -> None:
                while not stop.is_set():
                    try:
                        for ask in box.pending_asks():
                            json.dumps(ask)
                    except BaseException as exc:  # noqa: BLE001  # reason: collected, then asserted
                        errors.append(exc)

            threads = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
            for thread in threads:
                thread.start()
            try:
                for _ in range(25):
                    writer = Mailbox(Path(directory) / "runs" / DISPATCH, DISPATCH, max_asks=1000)
                    writer.write_ask(**_ask_kwargs(question="q" * 2000))  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
            finally:
                stop.set()
                for thread in threads:
                    thread.join(timeout=HANG_GUARD_SECS)
            self.assertEqual([], errors)

    def test_concurrent_asks_respect_max_open(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runs" / DISPATCH
            # Each writer holds its open-ask lookup until both have asked for the open-ask lock.
            # Without the lock both pass the check and both write; with it the second writer queues
            # behind the first, finds its ask open, and is refused.
            contention = OpenAskContention()
            outcomes: list[str] = []

            def write(index: int) -> None:
                box = Mailbox(root, DISPATCH)
                original = box.pending_asks

                def synchronized_pending() -> list[dict[str, object]]:
                    asks = original()
                    contention.hold()
                    return asks

                box.pending_asks = synchronized_pending  # type: ignore[method-assign]  # reason: the test widens the check-then-write window
                try:
                    box.write_ask(**_ask_kwargs(question=f"q{index}", max_open=1))  # type: ignore[arg-type]  # reason: the test passes a loosely typed kwargs mapping
                    outcomes.append("written")
                except MailboxOpenAskError:
                    outcomes.append("refused")

            threads = [threading.Thread(target=write, args=(i,)) for i in range(2)]
            with contention.installed():
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=HANG_GUARD_SECS)
            self.assertEqual(["refused", "written"], sorted(outcomes))
            self.assertEqual(1, len(Mailbox(root, DISPATCH).pending_asks()))

    def test_reenter_open_returns_the_open_ask_with_the_same_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            box = Mailbox(Path(directory) / "runs" / DISPATCH, DISPATCH)
            first = box.write_ask(**_ask_kwargs(max_open=1, reenter_open=True))  # type: ignore[arg-type]  # reason: the test passes a loosely typed kwargs mapping
            again = box.write_ask(**_ask_kwargs(max_open=1, reenter_open=True))  # type: ignore[arg-type]  # reason: the test passes a loosely typed kwargs mapping
            self.assertEqual(first["ask_id"], again["ask_id"])
            with self.assertRaises(MailboxOpenAskError):
                box.write_ask(**_ask_kwargs(question="other", max_open=1, reenter_open=True))  # type: ignore[arg-type]  # reason: the test passes a loosely typed kwargs mapping
            self.assertEqual(1, len(box.pending_asks()))


class MailboxDeadLetterTests(unittest.TestCase):
    def test_invalid_write_is_quarantined_never_interpreted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            box = Mailbox(Path(directory) / "runs" / DISPATCH, DISPATCH)
            box.ensure()
            bad = b'{"version": 1, "ask_id": "0001"}'  # missing mandatory fields
            (box.root / "asks").mkdir(parents=True, exist_ok=True)
            (box.root / "asks" / "0001.json").write_bytes(bad)
            self.assertEqual([], box.pending_asks())
            dead = list((box.root / "dead-letter").glob("*.json"))
            self.assertEqual(1, len(dead))
            self.assertFalse((box.root / "asks" / "0001.json").exists())

    def test_malformed_json_is_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            box = Mailbox(Path(directory) / "runs" / DISPATCH, DISPATCH)
            box.ensure()
            (box.root / "steer").mkdir(parents=True, exist_ok=True)
            (box.root / "steer" / "0001.json").write_bytes(b"{not json")
            self.assertEqual([], box.unacked_steers())
            self.assertEqual(1, len(list((box.root / "dead-letter").glob("*.json"))))

    def test_dead_letter_pruned_to_newest_twenty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            box = Mailbox(Path(directory) / "runs" / DISPATCH, DISPATCH)
            box.ensure()
            for index in range(25):
                box.quarantine("asks", f"bad-{index:02d}.json", b"not json", "malformed")
            remaining = sorted(
                (box.root / "dead-letter").glob("*.json"), key=lambda path: path.name
            )
            self.assertEqual(20, len(remaining))
            self.assertFalse((box.root / "dead-letter" / "asks_bad-00.json").exists())
            self.assertTrue((box.root / "dead-letter" / "asks_bad-24.json").exists())
            self.assertEqual("asks_bad-24.json", remaining[-1].name)

    def test_dead_letter_prune_keeps_the_newest_by_write_order_not_name(self) -> None:
        # Repeated quarantines of one filename produce asks_x.json, asks_x-2.json,
        # ... asks_x-25.json; a lexical sort would drop asks_x-10..14 instead of
        # the five oldest.
        with tempfile.TemporaryDirectory() as directory:
            box = Mailbox(Path(directory) / "runs" / DISPATCH, DISPATCH)
            box.ensure()
            for _ in range(25):
                box.quarantine("asks", "x.json", b"not json", "malformed")
            names = {path.name for path in (box.root / "dead-letter").glob("*.json")}
            self.assertEqual(20, len(names))
            for gone in ("asks_x.json", "asks_x-2.json", "asks_x-5.json"):
                self.assertNotIn(gone, names)
            for kept in ("asks_x-6.json", "asks_x-10.json", "asks_x-25.json"):
                self.assertIn(kept, names)

    def test_dead_letter_never_overwrites_a_stem_that_ends_in_digits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            box = Mailbox(Path(directory) / "runs" / DISPATCH, DISPATCH)
            box.ensure()
            first = box.quarantine("asks", "bad-24.json", b"one", "malformed")
            second = box.quarantine("asks", "bad-24.json", b"two", "malformed")
            self.assertEqual("asks_bad-24.json", first.name)
            self.assertEqual("asks_bad-24-2.json", second.name)
            self.assertIn(b"one", first.read_bytes())
            self.assertIn(b"two", second.read_bytes())


class MailboxPermissionTests(unittest.TestCase):
    def test_directories_are_0700_and_files_are_0600(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "runs" / DISPATCH
            box = Mailbox(run_dir, DISPATCH)
            box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
            box.write_steer(kind="note", message="m")
            box.write_answer("0001", choice="a", answered_by="operator")
            box.write_ack("0001")
            # The run dir itself is owned by RunStore (0700 there); the mailbox
            # subtree must be private regardless of the ambient umask.
            for path in box.root.rglob("*"):
                expected = 0o700 if path.is_dir() else 0o600
                actual = path.stat().st_mode & 0o777
                self.assertEqual(expected, actual, f"{path}: {oct(actual)}")
            self.assertEqual(0o700, box.root.stat().st_mode & 0o777)
            # umask must not leak through: force a permissive umask and rewrite
            old = os.umask(0o022)
            try:
                box.write_ask(**_ask_kwargs())  # type: ignore[arg-type]  # reason: the test passes a test double or a deliberately malformed argument
            finally:
                os.umask(old)
            self.assertEqual(0o600, (box.root / "asks" / "0002.json").stat().st_mode & 0o777)


if __name__ == "__main__":
    unittest.main()
