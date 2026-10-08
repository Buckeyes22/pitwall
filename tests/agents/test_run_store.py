"""Run-store privacy, prompt retention, and cleanup tests."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents.run_store import (
    RunStore,
    _managed_run_is_live,
    abandoned_reason,
    cleanup_runs,
    finalize_abandoned,
    find_run,
    list_runs,
    reconcile_run,
    state_root,
)

ROOT = Path(__file__).resolve().parents[2]


class RunStoreTests(unittest.TestCase):
    def test_terminate_process_group_never_signals_the_callers_own_group(self) -> None:
        # An orphaned harness that itself reads the run store would otherwise kill itself
        # (and its own reader) before the failure record is written.
        from pitwall.agents import pids, process

        own = os.getpgrp()
        identity = pids.process_identity(own)
        with (
            mock.patch.object(pids, "pid_alive", return_value=True),
            mock.patch.object(pids, "process_identity", return_value=identity or "id"),
            mock.patch.object(pids.os, "getpgid", return_value=own),
            mock.patch.object(process, "_terminate_remaining_group") as terminate,
        ):
            self.assertFalse(pids.terminate_process_group(own, identity or "id", 0.0))
        terminate.assert_not_called()

    def test_delivery_prompt_is_private_and_removable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            store = RunStore.create(env, "00000000-0000-4000-8000-00000000000a")
            path = store.write_delivery_prompt(b"hello\n")
            self.assertEqual("prompt.deliver.md", path.name)
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            store.remove_delivery_prompt()
            self.assertFalse(path.exists())
            store.remove_delivery_prompt()  # idempotent

    def test_private_atomic_documents_and_prompt_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            store = RunStore.create(env, "dispatch-one")
            store.write_json("run.json", {"state": "created"})
            store.record_request("prompt.md", b"sensitive\n", retain_prompt=False)
            self.assertEqual(0o700, store.path.stat().st_mode & 0o777)
            self.assertEqual(0o600, store.artifact("run.json").stat().st_mode & 0o777)
            self.assertFalse(store.artifact("prompt.md").exists())
            request = json.loads(store.artifact("request.json").read_text(encoding="utf-8"))
            self.assertEqual(10, request["promptSource"]["bytes"])
            self.assertFalse(request["promptSource"]["retained"])
            store.record_request("prompt.md", b"sensitive\n", retain_prompt=True)
            self.assertEqual(b"sensitive\n", store.artifact("prompt.md").read_bytes())

    def test_list_find_and_explicit_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            RunStore.create(env, "dispatch-one")
            RunStore.create(env, "dispatch-two")
            self.assertEqual(2, len(list_runs(env)))
            self.assertEqual("dispatch-one", find_run(env, "dispatch-o").name)
            removed = cleanup_runs(env, older_than_seconds=None, remove_all=True)
            self.assertEqual(2, len(removed))
            self.assertEqual([], list_runs(env))

    def _make_run_dir(self, env: dict[str, str], dispatch_id: str, state: str) -> Path:
        run_dir = state_root(env) / "runs" / dispatch_id
        run_dir.mkdir(parents=True)
        (run_dir / "run.json").write_text(json.dumps({"state": state}), encoding="utf-8")
        (state_root(env) / "launches" / dispatch_id).mkdir(parents=True)
        return run_dir

    def test_managed_run_liveness_uses_run_store_terminal_states(self) -> None:
        from pitwall.agents import run_store

        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            run_dir = self._make_run_dir(env, "dispatch-custom", "custom-terminal")
            # run_store.TERMINAL_STATES is the one definition, so patching it must
            # change what the liveness check treats as terminal.
            with mock.patch.object(run_store, "TERMINAL_STATES", frozenset({"custom-terminal"})):
                self.assertFalse(_managed_run_is_live(env, run_dir))

    def test_blocked_state_is_not_terminal_for_managed_runs(self) -> None:
        # run_store.TERMINAL_STATES does not include "blocked": run.json
        # state is only ever driven by dispatch.py's Lifecycle, whose
        # TRANSITIONS table never produces "blocked" for a single dispatch.
        # "blocked" is a scheduler-only, per-task DAG state stored in a
        # workflow's state.json, not in a managed run's run.json. Treating it
        # as terminal here would race a live managed run into deletion.
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            run_dir = self._make_run_dir(env, "dispatch-blocked", "blocked")
            self.assertTrue(_managed_run_is_live(env, run_dir))

    def test_terminal_states_have_one_definition(self) -> None:
        from pitwall.agents import dispatch, managed_channel, migrate, run_store

        self.assertEqual(
            frozenset({"preflight_failed", "succeeded", "failed", "timed_out", "cancelled"}),
            run_store.TERMINAL_STATES,
        )
        for module in (dispatch, managed_channel, migrate):
            with self.subTest(module=module.__name__):
                self.assertIs(run_store.TERMINAL_STATES, module.TERMINAL_STATES)

    def test_pid_helpers_live_in_one_module(self) -> None:
        from pitwall.agents import managed_channel, pids

        self.assertIs(pids.pid_alive, managed_channel._pid_alive)
        self.assertIs(pids.process_identity, managed_channel._process_identity)
        self.assertTrue(pids.pid_alive(os.getpid(), pids.process_identity(os.getpid())))

    def _run(
        self,
        env: dict[str, str],
        dispatch_id: str,
        *,
        state: str = "running",
        supervisor: dict[str, object] | None = None,
        age_seconds: float = 0.0,
    ) -> RunStore:
        store = RunStore.create(env, dispatch_id)
        document: dict[str, object] = {
            "schemaVersion": 1,
            "dispatchId": dispatch_id,
            "state": state,
            "transitions": [{"state": state, "timestamp": "2026-09-28T00:00:00.000Z"}],
        }
        if supervisor is not None:
            document["supervisor"] = supervisor
        store.write_json("run.json", document)
        if age_seconds:
            stamp = time.time() - age_seconds
            for path in (store.path, *store.path.iterdir()):
                os.utime(path, (stamp, stamp))
        return store

    def test_dead_supervisor_run_is_recorded_failed_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            child = subprocess.Popen([sys.executable, "-c", "pass"])
            child.wait()
            store = self._run(
                env,
                "00000000-0000-4000-8000-0000000000b1",
                supervisor={"pid": child.pid, "pidStartIdentity": "exited"},
            )
            reason = reconcile_run(env, store.path)
            assert reason is not None
            self.assertIn(str(child.pid), reason)
            document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
            self.assertEqual(
                ("failed", "failed"), (document["state"], document["transitions"][-1]["state"])
            )
            abandoned = json.loads(store.artifact("abandoned.json").read_text(encoding="utf-8"))
            self.assertEqual(reason, abandoned["reason"])
            self.assertIsNone(reconcile_run(env, store.path))

    def _abandoned_run_with_harness(
        self, env: dict[str, str], dispatch_id: str, harness: subprocess.Popen[bytes], identity: str
    ) -> RunStore:
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        store = self._run(
            env, dispatch_id, supervisor={"pid": dead.pid, "pidStartIdentity": "exited"}
        )
        document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
        document["harnessProcess"] = {"pgid": harness.pid, "pidStartIdentity": identity}
        store.write_json("run.json", document)
        return store

    def test_an_abandoned_run_terminates_its_surviving_harness_group(self) -> None:
        from pitwall.agents.pids import process_identity

        harness = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], process_group=0
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
                identity = process_identity(harness.pid)
                assert identity is not None
                store = self._abandoned_run_with_harness(
                    env, "00000000-0000-4000-8000-0000000000b5", harness, identity
                )
                self.assertIsNotNone(reconcile_run(env, store.path))
                self.assertEqual(-signal.SIGTERM, harness.wait(timeout=10))
                abandoned = json.loads(store.artifact("abandoned.json").read_text(encoding="utf-8"))
                self.assertIs(True, abandoned["harnessTerminated"])
                state = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
                self.assertEqual("failed", state["state"])
        finally:
            if harness.poll() is None:
                harness.kill()
                harness.wait()

    def test_a_harness_pid_with_another_start_identity_is_never_signalled(self) -> None:
        harness = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"], process_group=0
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
                store = self._abandoned_run_with_harness(
                    env, "00000000-0000-4000-8000-0000000000b6", harness, "linux:0"
                )
                self.assertIsNotNone(reconcile_run(env, store.path))
                self.assertIsNone(harness.poll())
                abandoned = json.loads(store.artifact("abandoned.json").read_text(encoding="utf-8"))
                self.assertIs(False, abandoned["harnessTerminated"])
        finally:
            harness.kill()
            harness.wait()

    def test_a_failed_write_still_restores_the_run_directory_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            store = self._run(
                env,
                "00000000-0000-4000-8000-0000000000b3",
                supervisor={"pid": 1, "pidStartIdentity": "x"},
                age_seconds=3600,
            )
            before = store.path.stat().st_mtime_ns
            real_write = RunStore.write_json

            def fail_on_run_json(self_: RunStore, name: str, data: Any) -> None:
                if name == "run.json":
                    raise OSError("disk full")
                real_write(self_, name, data)

            with (
                mock.patch.object(RunStore, "write_json", fail_on_run_json),
                self.assertRaises(OSError),
            ):
                finalize_abandoned(env, store.path, "test")
            self.assertTrue(store.artifact("abandoned.json").is_file())
            self.assertEqual(before, store.path.stat().st_mtime_ns)

    def test_an_abandoned_run_drops_its_delivery_prompt_unless_retained(self) -> None:
        for retained in (False, True):
            with self.subTest(retained=retained), tempfile.TemporaryDirectory() as directory:
                env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
                store = self._run(
                    env,
                    "00000000-0000-4000-8000-0000000000b4",
                    supervisor={"pid": 1, "pidStartIdentity": "x"},
                )
                store.write_delivery_prompt(b"prompt\n")
                store.record_request("-", b"prompt\n", retain_prompt=retained)
                finalize_abandoned(env, store.path, "test")
                state = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))["state"]
                self.assertEqual("failed", state)
                self.assertEqual(retained, store.artifact("prompt.deliver.md").exists())

    def test_an_abandoned_run_keeps_its_prompt_when_retention_is_unknown(self) -> None:
        damaged: dict[str, bytes | None] = {
            "missing": None,
            "corrupt": b"{not json",
            "list": b"[]",
            "empty object": b"{}",
            "no retained flag": b'{"promptSource": {}}',
            "undecodable": b"\xff\xfe\x00\x80",
        }
        for label, content in damaged.items():
            with self.subTest(request=label), tempfile.TemporaryDirectory() as directory:
                env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
                store = self._run(
                    env,
                    "00000000-0000-4000-8000-0000000000b5",
                    supervisor={"pid": 1, "pidStartIdentity": "x"},
                )
                store.write_delivery_prompt(b"prompt\n")
                if content is not None:
                    store.artifact("request.json").write_bytes(content)
                finalize_abandoned(env, store.path, "test")
                state = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))["state"]
                self.assertEqual("failed", state)
                self.assertTrue(store.artifact("prompt.deliver.md").exists())

    def test_live_recent_paused_and_managed_runs_are_never_abandoned(self) -> None:
        from pitwall.agents.pids import process_identity

        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            me = {"pid": os.getpid(), "pidStartIdentity": process_identity(os.getpid())}
            day = 24 * 3600
            live = self._run(
                env, "00000000-0000-4000-8000-0000000000b2", supervisor=me, age_seconds=30 * day
            )
            recent = self._run(env, "00000000-0000-4000-8000-0000000000b3")
            paused = self._run(
                env, "00000000-0000-4000-8000-0000000000b4", state="paused", age_seconds=2 * day
            )
            managed = self._run(env, "00000000-0000-4000-8000-0000000000b5", age_seconds=2 * day)
            (state_root(env) / "launches" / managed.path.name).mkdir(parents=True)
            stale = self._run(env, "00000000-0000-4000-8000-0000000000b6", age_seconds=25 * 3600)
            for store in (live, recent, paused, managed):
                with self.subTest(run=store.path.name):
                    self.assertIsNone(abandoned_reason(env, store.path))
            self.assertIn("24 hours", abandoned_reason(env, stale.path) or "")

    def test_a_managed_launch_sidecar_does_not_exempt_a_dead_supervisor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            managed = self._run(
                env, "00000000-0000-4000-8000-0000000000bc", supervisor=self._dead_supervisor()
            )
            (state_root(env) / "launches" / managed.path.name).mkdir(parents=True)
            self.assertIn("exited without recording", abandoned_reason(env, managed.path) or "")

    def test_lifecycle_records_its_supervisor(self) -> None:
        from pitwall.agents.dispatch import Lifecycle
        from pitwall.agents.events import EventEmitter

        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            store = RunStore.create(env, "00000000-0000-4000-8000-0000000000b7")
            Lifecycle(store, "codex", "m", EventEmitter(store, harness="codex", model="m"))
            document = json.loads(store.artifact("run.json").read_text(encoding="utf-8"))
            self.assertEqual(os.getpid(), document["supervisor"]["pid"])

    def _dead_supervisor(self) -> dict[str, object]:
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        return {"pid": child.pid, "pidStartIdentity": "exited"}

    def test_reconcile_keeps_the_run_directory_age(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            store = self._run(
                env,
                "00000000-0000-4000-8000-0000000000b8",
                supervisor=self._dead_supervisor(),
                age_seconds=30 * 24 * 3600,
            )
            before = store.path.stat()
            self.assertIsNotNone(reconcile_run(env, store.path))
            after = store.path.stat()
            self.assertEqual(
                (before.st_atime_ns, before.st_mtime_ns), (after.st_atime_ns, after.st_mtime_ns)
            )

    def test_cleanup_removes_an_old_abandoned_run_on_the_first_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            store = self._run(
                env,
                "00000000-0000-4000-8000-0000000000b9",
                supervisor=self._dead_supervisor(),
                age_seconds=30 * 24 * 3600,
            )
            removed = cleanup_runs(env, older_than_seconds=7 * 86400, remove_all=False)
            self.assertEqual([store.path], removed)
            self.assertFalse(store.path.exists())

    def test_cleanup_keeps_a_nonterminal_run_whose_supervisor_is_alive(self) -> None:
        from pitwall.agents import pids

        live = {"pid": os.getpid(), "pidStartIdentity": pids.process_identity(os.getpid())}
        for label, older_than, remove_all in (
            ("remove-all", None, True),
            ("age-zero", 0.0, False),
        ):
            with self.subTest(label), tempfile.TemporaryDirectory() as directory:
                env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
                store = self._run(env, "00000000-0000-4000-8000-0000000000aa", supervisor=live)
                store.write_bytes("stdout.log", b"live output")
                removed = cleanup_runs(env, older_than_seconds=older_than, remove_all=remove_all)
                self.assertEqual([], removed)
                self.assertTrue(store.path.is_dir())
                self.assertEqual(b"live output", store.artifact("stdout.log").read_bytes())
                self.assertFalse((store.path / "workspace.json").exists())

    def test_cleanup_keeps_a_run_that_turns_nonterminal_during_distillation(self) -> None:
        from pitwall.agents import channel

        with tempfile.TemporaryDirectory() as directory:
            env = {"HOME": directory, "XDG_STATE_HOME": str(Path(directory) / "state")}
            store = self._run(env, "00000000-0000-4000-8000-0000000000ab", state="failed")

            def revive(_env: object, run_dir: Path) -> None:
                document = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
                document["state"] = "running"
                (run_dir / "run.json").write_text(json.dumps(document), encoding="utf-8")

            with mock.patch.object(channel, "distill_before_cleanup", side_effect=revive):
                removed = cleanup_runs(env, older_than_seconds=None, remove_all=True)
            self.assertEqual([], removed)
            self.assertTrue(store.path.is_dir())


if __name__ == "__main__":
    unittest.main()
