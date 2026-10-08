"""Durability edges for a managed parent wait without a live harness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from pitwall.agents import managed_channel
from pitwall.agents.channel import (
    ChannelConfig,
    epoch_of,
    resolve_with_default,
    write_channel_config,
)
from pitwall.agents.mailbox import Mailbox
from pitwall.agents.managed_channel import (
    LaunchRequest,
    ManagedChannelError,
    WaitCancelled,
    _pid_alive,
    _process_identity,
    _process_state,
    _unacked_steer_ids,
    _write_file,
    answer_once,
    start_dispatch,
    steer_once,
    wait_for_event,
)
from pitwall.agents.run_store import (
    RunStore,
    atomic_write_json,
    cleanup_runs,
    ensure_private_directory,
    reconcile_run,
)
from tests.hang_guard import HANG_GUARD_SECS


class ManagedChannelEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = {"PITWALL_AGENTS_STATE_HOME": str(self.root)}
        self.dispatch_id = str(uuid.uuid4())
        self.launch = self.root / "launches" / self.dispatch_id
        ensure_private_directory(self.launch)

    def _standalone_run(self, supervisor: dict[str, object]) -> RunStore:
        dispatch_id = str(uuid.uuid4())
        store = RunStore.create(self.env, dispatch_id)
        store.write_json(
            "run.json",
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "state": "running",
                "supervisor": supervisor,
            },
        )
        return store

    def test_live_standalone_run_is_never_orphaned(self) -> None:
        store = self._standalone_run(
            {"pid": os.getpid(), "pidStartIdentity": _process_identity(os.getpid())}
        )
        event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=3)
        self.assertEqual("still_running", event["event"])
        self.assertFalse((store.path / "orphan.json").exists())

    def _dead_supervisor_run(self) -> tuple[RunStore, str]:
        child = subprocess.Popen([sys.executable, "-c", ""])
        child.wait()
        store = self._standalone_run({"pid": child.pid, "pidStartIdentity": "exited"})
        return store, f"supervisor pid {child.pid} exited without recording a terminal state"

    def test_standalone_run_with_a_dead_supervisor_is_orphaned_and_failed(self) -> None:
        store, reason = self._dead_supervisor_run()
        event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=3)
        self.assertEqual("orphan", event["event"])
        self.assertEqual(reason, event["error"])
        document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", document["state"])
        abandoned = json.loads(store.artifact("abandoned.json").read_text(encoding="utf-8"))
        self.assertEqual(reason, abandoned["reason"])
        self.assertTrue((store.path / "orphan.json").is_file())

    def test_orphan_names_the_steers_its_dead_supervisor_never_acknowledged(self) -> None:
        store, _reason = self._dead_supervisor_run()
        steer = Mailbox(store.path, store.dispatch_id).write_steer(
            kind="scope", message="SQLite only"
        )
        event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=3)
        self.assertEqual("orphan", event["event"])
        self.assertEqual([steer["steer_id"]], event["unacked_steer_ids"])

    def test_unacked_steer_ids_is_none_without_a_mailbox_and_a_list_with_one(self) -> None:
        store, _reason = self._dead_supervisor_run()
        self.assertIsNone(_unacked_steer_ids(store.path, store.dispatch_id))
        Mailbox(store.path, store.dispatch_id).write_steer(kind="scope", message="SQLite only")
        self.assertEqual(["0001"], _unacked_steer_ids(store.path, store.dispatch_id))

    def test_polling_a_channel_less_live_run_never_creates_its_mailbox(self) -> None:
        store = self._standalone_run(
            {"pid": os.getpid(), "pidStartIdentity": _process_identity(os.getpid())}
        )
        event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=1)
        self.assertEqual("still_running", event["event"])
        self.assertFalse((store.path / "mailbox").exists())
        self.assertFalse((store.path / "mailbox.json").exists())

    def test_rewaiting_a_reconciled_standalone_run_returns_the_orphan_at_once(self) -> None:
        store, reason = self._dead_supervisor_run()
        for _ in range(2):
            started = time.monotonic()
            event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=30)
            self.assertLess(time.monotonic() - started, 5)
            self.assertEqual("orphan", event["event"])
            self.assertEqual(reason, event["error"])

    def test_terminal_standalone_run_with_a_live_supervisor_is_not_orphaned_early(self) -> None:
        store = self._standalone_run(
            {"pid": os.getpid(), "pidStartIdentity": _process_identity(os.getpid())}
        )
        document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
        document["state"] = "succeeded"
        store.write_json("run.json", document)
        started = time.monotonic()
        event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=1)
        self.assertGreaterEqual(time.monotonic() - started, 0.9)
        self.assertEqual("orphan", event["event"])

    def test_result_written_after_the_poll_read_beats_the_orphan(self) -> None:
        store = self._standalone_run(
            {"pid": os.getpid(), "pidStartIdentity": _process_identity(os.getpid())}
        )
        document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
        document["state"] = "succeeded"
        store.write_json("run.json", document)

        def exits_after_writing_result(pid: int, identity: str | None = None) -> bool:
            # The supervisor publishes result.json and exits between the poll's read and
            # the liveness check.
            store.write_json("result.json", {"status": "succeeded", "outcome": "complete"})
            return False

        with mock.patch("pitwall.agents.managed_channel._pid_alive", exits_after_writing_result):
            event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=30)
        self.assertEqual("terminal", event["event"])
        self.assertEqual("succeeded", event["status"])
        self.assertFalse((store.path / "orphan.json").exists())

    def test_result_written_during_reconcile_beats_the_orphan(self) -> None:
        store, reason = self._dead_supervisor_run()
        real = reconcile_run

        def reconcile_after_result(env: dict[str, str], run_dir: Path) -> str | None:
            store.write_json("result.json", {"status": "succeeded", "outcome": "complete"})
            return real(env, run_dir)

        with mock.patch("pitwall.agents.managed_channel.reconcile_run", reconcile_after_result):
            event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=30)
        self.assertEqual("terminal", event["event"])
        self.assertFalse((store.path / "orphan.json").exists())

    def test_a_reconcile_error_is_retried_on_the_next_poll(self) -> None:
        store, reason = self._dead_supervisor_run()
        real = reconcile_run
        calls = {"count": 0}

        def flaky(env: dict[str, str], run_dir: Path) -> str | None:
            calls["count"] += 1
            if calls["count"] == 1:
                raise OSError("run directory busy")
            return real(env, run_dir)

        with mock.patch("pitwall.agents.managed_channel.reconcile_run", flaky):
            event = wait_for_event(self.env, store.dispatch_id, None, wait_seconds=30)
        self.assertEqual("orphan", event["event"])
        self.assertEqual(reason, event["error"])
        self.assertGreaterEqual(calls["count"], 2)

    def test_cancelled_wait_leaves_launcher_and_reattach_record(self) -> None:
        atomic_write_json(self.launch / "launcher.json", {"pid": os.getpid()})
        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(WaitCancelled):
            wait_for_event(self.env, self.dispatch_id, cancel, wait_seconds=1)

        self.assertTrue((self.launch / "launcher.json").is_file())
        self.assertFalse((self.launch / "orphan.json").exists())

    def test_cancellation_wins_over_pending_ask(self) -> None:
        run = RunStore.create(self.env, self.dispatch_id)
        atomic_write_json(
            run.path / "run.json", {"dispatchId": self.dispatch_id, "state": "running"}
        )
        run.mailbox().write_ask(
            blocked_on="choice",
            question="continue?",
            options=[{"id": "a", "text": "yes"}],
            default="a",
            deadline_s=60,
        )
        atomic_write_json(self.launch / "launcher.json", {"pid": os.getpid()})
        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(WaitCancelled):
            wait_for_event(self.env, self.dispatch_id, cancel, wait_seconds=1)

    def test_late_explicit_answer_resolves_validated_default(self) -> None:
        run = RunStore.create(self.env, self.dispatch_id)
        atomic_write_json(
            run.path / "run.json", {"dispatchId": self.dispatch_id, "state": "running"}
        )
        ask = run.mailbox().write_ask(
            blocked_on="choice",
            question="continue?",
            options=[{"id": "a", "text": "yes"}, {"id": "b", "text": "no"}],
            default="a",
            deadline_s=1,
        )
        # Move the durable creation time behind the precise deadline without
        # making the unit suite sleep. This is equivalent to a +1.2s retry and
        # exercises the same effective-deadline calculation.
        ask["created_at"] = "2000-01-01T00:00:00Z"
        atomic_write_json(run.path / "mailbox" / "asks" / "0001.json", ask)

        late = answer_once(self.env, self.dispatch_id, "0001", "b")
        self.assertEqual("default", late["answered_by"])
        self.assertEqual("a", late["choice"])
        # A later conflicting retry observes the default as the sole winner;
        # it must remain idempotent instead of reopening the ask.
        retry = answer_once(self.env, self.dispatch_id, "0001", "b")
        self.assertEqual(late, retry)

    def test_default_wins_when_explicit_writer_is_paused_before_commit(self) -> None:
        run = RunStore.create(self.env, self.dispatch_id)
        atomic_write_json(
            run.path / "run.json", {"dispatchId": self.dispatch_id, "state": "running"}
        )
        ask = run.mailbox().write_ask(
            blocked_on="choice",
            question="continue?",
            options=[{"id": "a", "text": "yes"}, {"id": "b", "text": "no"}],
            default="a",
            deadline_s=1,
        )
        entered_write = threading.Event()
        release_write = threading.Event()
        result: dict[str, object] = {}
        errors: list[BaseException] = []
        original_write_answer = Mailbox.write_answer

        def delayed_write_answer(
            box: Mailbox,
            ask_id: str,
            *,
            choice: str,
            answered_by: str,
            note: str | None = None,
        ) -> dict[str, object]:
            if answered_by == "orchestrator":
                entered_write.set()
                if not release_write.wait(HANG_GUARD_SECS):
                    raise AssertionError("explicit writer was not released")
            return original_write_answer(
                box,
                ask_id,
                choice=choice,
                answered_by=answered_by,
                note=note,
            )

        def answer_worker() -> None:
            try:
                result["answer"] = answer_once(self.env, self.dispatch_id, "0001", "b")
            except BaseException as exc:  # report worker failures in the test thread
                errors.append(exc)

        # Hold answer_once after both caller-side deadline checks. The ask
        # expires while it is paused; the child/default writer then gets the
        # shared mailbox arbitration path first.
        with (
            mock.patch.object(Mailbox, "write_answer", delayed_write_answer),
            mock.patch(
                "pitwall.agents.managed_channel._ask_deadline_expired", side_effect=[False, False]
            ),
        ):
            worker = threading.Thread(target=answer_worker)
            worker.start()
            self.assertTrue(entered_write.wait(HANG_GUARD_SECS))
            # Let the ask's one-second deadline pass on the wall clock the mailbox checks.
            expires_at = epoch_of(ask["created_at"]) + 1
            while time.time() <= expires_at:
                time.sleep(0.01)
            default = resolve_with_default(run, ask, emitter=None)
            release_write.set()
            worker.join(HANG_GUARD_SECS)

        self.assertFalse(worker.is_alive())
        self.assertEqual([], errors)
        self.assertEqual("default", default["answered_by"])
        self.assertEqual("a", default["choice"])
        resolved = result["answer"]
        assert isinstance(resolved, dict)
        self.assertEqual("default", resolved["answered_by"])
        self.assertEqual(default, run.mailbox().get_answer("0001"))

    def test_cleanup_scrubs_prompt_after_durable_request_without_waiter(self) -> None:
        run = RunStore.create(self.env, self.dispatch_id)
        atomic_write_json(
            run.path / "run.json", {"dispatchId": self.dispatch_id, "state": "running"}
        )
        atomic_write_json(run.path / "request.json", {"dispatchId": self.dispatch_id})
        atomic_write_json(
            self.launch / "request.json",
            {"dispatchId": self.dispatch_id, "retainPrompt": False},
        )
        (self.launch / "prompt.md").write_text("private prompt", encoding="utf-8")

        cleanup_runs(self.env, older_than_seconds=0, remove_all=False)

        self.assertFalse((self.launch / "prompt.md").exists())
        self.assertTrue(run.path.exists())

    @staticmethod
    def _reap(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            process.kill()
        process.wait()

    def _managed_run(
        self, supervisor: dict[str, object], harness: dict[str, object] | None = None
    ) -> RunStore:
        """A running managed run: launch sidecar naming a dead launcher, run.json's own supervisor."""
        atomic_write_json(self.launch / "launcher.json", {"pid": -1})
        store = RunStore.create(self.env, self.dispatch_id)
        document: dict[str, object] = {
            "schemaVersion": 1,
            "dispatchId": self.dispatch_id,
            "state": "running",
            "supervisor": supervisor,
        }
        if harness is not None:
            document["harnessProcess"] = harness
        store.write_json("run.json", document)
        return store

    def test_a_launch_sidecar_does_not_hide_a_dead_supervisor(self) -> None:
        import signal

        child = subprocess.Popen([sys.executable, "-c", ""])
        child.wait()
        harness = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], process_group=0
        )
        self.addCleanup(self._reap, harness)
        identity = _process_identity(harness.pid)
        assert identity is not None
        store = self._managed_run(
            {"pid": child.pid, "pidStartIdentity": "exited"},
            {"pgid": harness.pid, "pidStartIdentity": identity},
        )

        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=3)

        self.assertEqual("orphan", event["event"])
        self.assertIn("exited without recording a terminal state", event["error"])
        self.assertEqual(-signal.SIGTERM, harness.wait(timeout=10))
        document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
        self.assertEqual("failed", document["state"])
        abandoned = json.loads(store.artifact("abandoned.json").read_text(encoding="utf-8"))
        self.assertIs(True, abandoned["harnessTerminated"])

    def test_runs_stop_on_a_dead_managed_supervisor_promises_no_abort_window(self) -> None:
        import contextlib
        import io

        from pitwall.agents.cli import _runs_stop

        child = subprocess.Popen([sys.executable, "-c", ""])
        child.wait()
        harness = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], process_group=0
        )
        self.addCleanup(self._reap, harness)
        identity = _process_identity(harness.pid)
        assert identity is not None
        store = self._managed_run(
            {"pid": child.pid, "pidStartIdentity": "exited"},
            {"pgid": harness.pid, "pidStartIdentity": identity},
        )
        err = io.StringIO()
        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, self.env, clear=True),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(out),
        ):
            exit_code = _runs_stop(self.dispatch_id, None, 1)
        self.assertEqual(1, exit_code)
        self.assertIn("abandoned", err.getvalue())
        self.assertNotIn("aborted", out.getvalue())
        self.assertIsNotNone(harness.wait(timeout=10))
        self.assertFalse((store.path / "mailbox").exists())

    def test_cleanup_ends_the_harness_of_a_dead_managed_supervisor(self) -> None:
        child = subprocess.Popen([sys.executable, "-c", ""])
        child.wait()
        harness = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], process_group=0
        )
        self.addCleanup(self._reap, harness)
        identity = _process_identity(harness.pid)
        assert identity is not None
        store = self._managed_run(
            {"pid": child.pid, "pidStartIdentity": "exited"},
            {"pgid": harness.pid, "pidStartIdentity": identity},
        )
        cleanup_runs(self.env, older_than_seconds=None, remove_all=True)
        self.assertIsNotNone(harness.wait(timeout=10))
        self.assertFalse(store.path.exists())

    def test_a_stale_launch_sidecar_does_not_orphan_a_live_resumed_supervisor(self) -> None:
        store = self._managed_run(
            {"pid": os.getpid(), "pidStartIdentity": _process_identity(os.getpid())}
        )
        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=2)
        self.assertEqual("still_running", event["event"])
        self.assertFalse((store.path / "orphan.json").exists())
        self.assertEqual([], cleanup_runs(self.env, older_than_seconds=None, remove_all=True))
        self.assertTrue(store.path.is_dir())
        self.assertTrue(self.launch.is_dir())

    def test_dead_launcher_without_receipt_is_orphan(self) -> None:
        atomic_write_json(self.launch / "launcher.json", {"pid": -1})

        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)

        self.assertEqual("orphan", event["event"])
        self.assertEqual(self.dispatch_id, event["reattach_handle"])
        self.assertTrue((self.launch / "orphan.json").is_file())

    def test_terminal_receipt_wins_over_dead_launcher(self) -> None:
        atomic_write_json(self.launch / "launcher.json", {"pid": -1})
        run = self.root / "runs" / self.dispatch_id
        ensure_private_directory(run)
        atomic_write_json(run / "result.json", {"status": "succeeded", "outcome": "complete"})

        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)

        self.assertEqual("terminal", event["event"])
        self.assertEqual("succeeded", event["status"])
        self.assertFalse((self.launch / "orphan.json").exists())

    def test_terminal_artifacts_point_at_the_current_run_directory(self) -> None:
        run = self.root / "runs" / self.dispatch_id
        ensure_private_directory(run)
        atomic_write_json(
            run / "result.json",
            {"status": "succeeded", "outcome": "ok", "artifacts": {"result": "/gone/result.json"}},
        )
        atomic_write_json(self.launch / "launcher.json", {"pid": -1})
        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)
        expected = RunStore(self.root, self.dispatch_id).artifact_summary()
        self.assertEqual(expected, event["artifacts"])
        self.assertEqual(str(run / "result.json"), event["artifacts"]["result"])
        for name, path in event["artifacts"].items():
            self.assertEqual(str(run / Path(path).name), path, name)
        self.assertEqual(event["artifacts"], event["receipt"]["artifacts"])

    def test_orphan_artifacts_point_at_the_run_directory_when_it_exists(self) -> None:
        run = self.root / "runs" / self.dispatch_id
        ensure_private_directory(run)
        atomic_write_json(run / "run.json", {"schemaVersion": 1, "state": "running"})
        atomic_write_json(self.launch / "launcher.json", {"pid": -1})
        atomic_write_json(self.launch / "launch_error.json", {"error": "boom"})
        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)
        self.assertEqual("orphan", event["event"])
        expected = RunStore(self.root, self.dispatch_id).artifact_summary()
        self.assertEqual(expected, event["artifacts"])
        self.assertEqual(expected, event["receipt"]["artifacts"])

    def test_orphan_without_a_run_directory_has_no_artifacts(self) -> None:
        atomic_write_json(self.launch / "launcher.json", {"pid": -1})
        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)
        self.assertEqual("orphan", event["event"])
        self.assertNotIn("artifacts", event)

    def test_terminal_payload_carries_the_soft_denial_reason_from_run_json(self) -> None:
        run = self.root / "runs" / self.dispatch_id
        ensure_private_directory(run)
        atomic_write_json(run / "result.json", {"status": "failed", "outcome": "error"})
        atomic_write_json(run / "run.json", {"state": "failed"})
        plain = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)
        self.assertNotIn("soft_denial_reason", plain)

        reason = "exited 0 without writing anything to stdout; recording exit 77"
        atomic_write_json(run / "run.json", {"state": "failed", "softDenialReason": reason})
        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)
        self.assertEqual(reason, event["soft_denial_reason"])
        self.assertNotIn("softDenialReason", event["receipt"])

    def test_soft_denial_reason_is_read_after_result_json_is_seen(self) -> None:
        run = self.root / "runs" / self.dispatch_id
        ensure_private_directory(run)
        atomic_write_json(run / "result.json", {"status": "failed", "outcome": "error"})
        reason = "exited 0 without writing anything to stdout; recording exit 77"
        run_reads = [{"state": "running"}, {"state": "failed", "softDenialReason": reason}]
        real_read_json = managed_channel._read_json

        def read_json(path: Path) -> dict[str, object] | None:
            if path.name == "run.json":
                return run_reads.pop(0) if len(run_reads) > 1 else run_reads[0]
            return real_read_json(path)

        with mock.patch.object(managed_channel, "_read_json", read_json):
            event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)

        self.assertEqual("terminal", event["event"])
        self.assertEqual(reason, event["soft_denial_reason"])

    def test_terminal_payload_lists_steers_still_unacked_only_for_a_mailbox_run(self) -> None:
        run = self.root / "runs" / self.dispatch_id
        ensure_private_directory(run)
        atomic_write_json(run / "result.json", {"status": "succeeded", "outcome": "complete"})
        plain = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)
        self.assertNotIn("unacked_steer_ids", plain)

        box = Mailbox(run, self.dispatch_id)
        box.write_steer(kind="scope", message="SQLite only")
        acked = box.write_steer(kind="scope", message="no ORM")
        box.write_ack(acked["steer_id"])
        event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)
        self.assertEqual(["0001"], event["unacked_steer_ids"])

    def test_cleanup_removes_sidecar_without_run_after_launcher_dies(self) -> None:
        atomic_write_json(self.launch / "launcher.json", {"pid": -1})
        atomic_write_json(self.launch / "orphan.json", {"event": "orphan"})

        self.assertEqual([], cleanup_runs(self.env, older_than_seconds=0, remove_all=False))
        self.assertFalse(self.launch.exists())

    def test_cleanup_keeps_live_sidecar_before_run_exists(self) -> None:
        atomic_write_json(self.launch / "launcher.json", {"pid": os.getpid()})

        self.assertEqual([], cleanup_runs(self.env, older_than_seconds=0, remove_all=False))
        self.assertTrue(self.launch.is_dir())

    def test_process_identity_and_state_fall_back_to_ps_without_procfs(self) -> None:
        lstart = "Mon Sep 22 03:12:42 2026"
        with (
            mock.patch.object(Path, "read_text", side_effect=FileNotFoundError),
            mock.patch(
                "pitwall.agents.pids.subprocess.run",
                side_effect=[
                    subprocess.CompletedProcess([], 0, stdout=f"{lstart}\n", stderr=""),
                    subprocess.CompletedProcess([], 0, stdout="S+\n", stderr=""),
                ],
            ) as run,
        ):
            self.assertEqual(f"ps:{lstart}", _process_identity(4242))
            self.assertEqual("S", _process_state(4242))

        self.assertEqual(
            [
                mock.call(
                    ["ps", "-p", "4242", "-o", "lstart="],
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                    check=False,
                ),
                mock.call(
                    ["ps", "-p", "4242", "-o", "stat="],
                    capture_output=True,
                    text=True,
                    timeout=0.5,
                    check=False,
                ),
            ],
            run.call_args_list,
        )

    def test_stale_pid_identity_is_orphaned(self) -> None:
        atomic_write_json(
            self.launch / "launcher.json",
            {"pid": os.getpid(), "pidStartIdentity": "ps:stale"},
        )
        with (
            mock.patch("pitwall.agents.pids._process_state", return_value="S"),
            mock.patch("pitwall.agents.pids.process_identity", return_value="ps:current"),
        ):
            event = wait_for_event(self.env, self.dispatch_id, None, wait_seconds=1)

        self.assertEqual("orphan", event["event"])
        self.assertEqual(self.dispatch_id, event["reattach_handle"])
        self.assertTrue((self.launch / "orphan.json").is_file())

    def test_posix_launch_uses_a_new_process_group(self) -> None:
        if os.name != "posix":
            self.skipTest("managed launcher process groups are a POSIX behavior")
        dispatch_id = str(uuid.uuid4())
        process = mock.Mock()
        process.pid = 4242
        with (
            mock.patch(
                "pitwall.agents.managed_channel._execution_argv", return_value=["/missing/launcher"]
            ),
            mock.patch("pitwall.agents.managed_channel._process_identity", return_value=None),
            mock.patch(
                "pitwall.agents.managed_channel.subprocess.Popen", return_value=process
            ) as popen,
            mock.patch("pitwall.agents.managed_channel.threading.Thread"),
        ):
            start_dispatch(
                self.env,
                LaunchRequest(
                    dispatch_id=dispatch_id,
                    route=None,
                    harness="codex",
                    prompt="test launch",
                ),
            )

        self.assertEqual(0, popen.call_args.kwargs["process_group"])

    def test_matching_pid_identity_remains_live(self) -> None:
        with (
            mock.patch("pitwall.agents.pids._process_state", return_value="S"),
            mock.patch("pitwall.agents.pids.process_identity", return_value="ps:current"),
            mock.patch("pitwall.agents.pids.os.kill") as kill,
        ):
            self.assertTrue(_pid_alive(4242, "ps:current"))
        kill.assert_called_once_with(4242, 0)

    def test_pid_alive_treats_permission_error_as_alive(self) -> None:
        # EPERM means the kernel found the pid and refused the signal because
        # another user owns it -- the process still exists, unlike ESRCH.
        with (
            mock.patch("pitwall.agents.pids.os.kill", side_effect=PermissionError),
            mock.patch("pitwall.agents.pids._process_state", return_value="S"),
            mock.patch("pitwall.agents.pids.process_identity", return_value=None),
        ):
            self.assertTrue(_pid_alive(4242, None))

    def test_pid_alive_treats_missing_process_as_dead(self) -> None:
        with mock.patch("pitwall.agents.pids.os.kill", side_effect=ProcessLookupError):
            self.assertFalse(_pid_alive(4242, None))

    def test_write_file_does_not_double_close_descriptor(self) -> None:
        path = self.root / "written.bin"
        with mock.patch("pitwall.agents.managed_channel.os.close") as close:
            _write_file(path, b"payload")
        close.assert_not_called()
        self.assertEqual(b"payload", path.read_bytes())

    def test_write_file_closes_descriptor_once_on_fchmod_failure(self) -> None:
        path = self.root / "unwritten.bin"
        with (
            mock.patch("pitwall.agents.managed_channel.os.fchmod", side_effect=OSError("boom")),
            mock.patch("pitwall.agents.managed_channel.os.close", wraps=os.close) as close,
            self.assertRaises(OSError),
        ):
            _write_file(path, b"payload")
        close.assert_called_once()

    def test_managed_steer_without_channel_accepts_only_stop(self) -> None:
        store = RunStore.create(self.env, self.dispatch_id)
        store.write_json("run.json", {"schemaVersion": 1, "state": "running"})
        with self.assertRaisesRegex(ManagedChannelError, "without the orchestrator channel"):
            steer_once(self.env, self.dispatch_id, kind="note", message="hello")
        stop = steer_once(self.env, self.dispatch_id, kind="stop", message="wrap up")
        self.assertEqual("abort-at-deadline", stop["delivery"])

    def test_managed_refused_steer_writes_nothing(self) -> None:
        store = RunStore.create(self.env, self.dispatch_id)
        store.write_json("run.json", {"schemaVersion": 1, "state": "running"})
        with self.assertRaises(ManagedChannelError):
            steer_once(self.env, self.dispatch_id, kind="note", message="hello")
        self.assertFalse((store.path / "mailbox").exists())
        self.assertFalse(store.artifact("events.jsonl").exists())

    def test_managed_steer_on_a_channel_run_has_no_delivery_marker(self) -> None:
        store = RunStore.create(self.env, self.dispatch_id)
        store.write_json("run.json", {"schemaVersion": 1, "state": "running"})
        write_channel_config(
            store, ChannelConfig(self.dispatch_id, tier="1", attempt_started_epoch=time.time())
        )
        note = steer_once(self.env, self.dispatch_id, kind="note", message="hello")
        stop = steer_once(self.env, self.dispatch_id, kind="stop", message="wrap up")
        self.assertNotIn("delivery", note)
        self.assertNotIn("delivery", stop)
        self.assertEqual(("0001", "0002"), (note["steer_id"], stop["steer_id"]))

    def test_managed_steer_to_a_pre_launch_run_without_its_config_says_still_preparing(
        self,
    ) -> None:
        store = RunStore.create(self.env, self.dispatch_id)
        store.write_json("run.json", {"schemaVersion": 1, "state": "ready"})
        with self.assertRaises(ManagedChannelError) as caught:
            steer_once(self.env, self.dispatch_id, kind="note", message="hello")
        self.assertEqual(
            f"run {self.dispatch_id} is still preparing and has not recorded its orchestrator channel yet; retry the steer once it is running, or send a stop steer",
            str(caught.exception),
        )
        self.assertFalse((store.path / "mailbox").exists())
        stop = steer_once(self.env, self.dispatch_id, kind="stop", message="wrap up")
        self.assertEqual("abort-at-deadline", stop["delivery"])

    def test_managed_steer_to_a_pre_launch_run_with_its_config_is_accepted(self) -> None:
        store = RunStore.create(self.env, self.dispatch_id)
        store.write_json("run.json", {"schemaVersion": 1, "state": "workspace_ready"})
        write_channel_config(
            store, ChannelConfig(self.dispatch_id, tier="1", attempt_started_epoch=time.time())
        )
        steer = steer_once(self.env, self.dispatch_id, kind="note", message="hello")
        self.assertEqual("0001", steer["steer_id"])

    def test_managed_steer_with_an_unreadable_channel_json_is_refused(self) -> None:
        store = RunStore.create(self.env, self.dispatch_id)
        store.write_json("run.json", {"schemaVersion": 1, "state": "running"})
        store.artifact("channel.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ManagedChannelError) as caught:
            steer_once(self.env, self.dispatch_id, kind="note", message="hello")
        self.assertEqual(
            f"run {self.dispatch_id} has an unreadable channel.json; only a stop steer applies",
            str(caught.exception),
        )
        self.assertFalse((store.path / "mailbox").exists())


if __name__ == "__main__":
    unittest.main()
