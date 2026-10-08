"""Launch-guard lock files do not outlive their sessions."""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from tests.hang_guard import HANG_GUARD_SECS

HOOKS = Path(__file__).resolve().parents[2] / "plugins" / "claude" / "hooks"


class LaunchGuardLockTests(unittest.TestCase):
    def setUp(self) -> None:
        sys.path.insert(0, str(HOOKS))
        self.addCleanup(sys.path.remove, str(HOOKS))
        self.markers = importlib.import_module("launch_guard_markers")
        self.leases = importlib.import_module("launch_guard_leases")
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "routing-sessions"
        self.root.mkdir()
        patcher = mock.patch.dict(os.environ, {"PITWALL_AGENTS_ROUTING_MARKER_DIR": str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_session_end_removes_the_lock(self) -> None:
        (self.root / "s1.json").write_text("{}", encoding="utf-8")
        (self.root / ".s1.lock").write_text("", encoding="utf-8")
        self.leases.deactivate_marker({"session_id": "s1"})
        self.assertEqual([], sorted(p.name for p in self.root.iterdir()))

    def test_sweep_removes_only_old_locks_without_a_marker(self) -> None:
        old = time.time() - 8 * 24 * 3600
        for name in ("old", "recent", "live"):
            (self.root / f".{name}.lock").write_text("", encoding="utf-8")
        (self.root / "live.json").write_text("{}", encoding="utf-8")
        for name in ("old", "live"):
            os.utime(self.root / f".{name}.lock", (old, old))
        self.markers.sweep_stale_locks(self.root, time.time())
        self.assertEqual(
            [".live.lock", ".recent.lock", "live.json"], sorted(p.name for p in self.root.iterdir())
        )

    def _hold(
        self, session_id: str, entered: threading.Event, release: threading.Event
    ) -> threading.Thread:
        def run() -> None:
            with self.markers.marker_lock(self.root, session_id):
                entered.set()
                self.assertTrue(release.wait(HANG_GUARD_SECS))

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def test_sweep_never_frees_a_lock_a_live_session_holds(self) -> None:
        entered, release = threading.Event(), threading.Event()
        holder = self._hold("s", entered, release)
        self.assertTrue(entered.wait(HANG_GUARD_SECS))
        old = time.time() - 8 * 24 * 3600
        os.utime(self.root / ".s.lock", (old, old))
        self.markers.sweep_stale_locks(self.root, time.time())
        self.assertTrue((self.root / ".s.lock").exists())

        second_entered, second_release = threading.Event(), threading.Event()
        second = self._hold("s", second_entered, second_release)
        self.assertFalse(second_entered.wait(0.3), "second caller acquired a held lock")
        release.set()
        holder.join(HANG_GUARD_SECS)
        self.assertTrue(second_entered.wait(HANG_GUARD_SECS))
        second_release.set()
        second.join(HANG_GUARD_SECS)

    def test_a_waiter_in_flight_at_session_end_gives_up_without_recreating_the_lock(
        self,
    ) -> None:
        # Session end is held inside its critical section until a second caller has opened
        # the same lock file and is about to wait on it, so the race is exercised every run.
        ender_inside, ender_release, waiter_waiting = (threading.Event() for _ in range(3))
        real_marker_path = self.leases.marker_path
        real_fcntl = self.markers.fcntl

        def paused_marker_path(root: Path, session_id: str) -> Path:
            if threading.current_thread().name == "ender":
                ender_inside.set()
                self.assertTrue(ender_release.wait(HANG_GUARD_SECS))
            return real_marker_path(root, session_id)

        def signalling_flock(fd: int, operation: int) -> None:
            if threading.current_thread().name == "waiter" and operation == real_fcntl.LOCK_EX:
                waiter_waiting.set()
            real_fcntl.flock(fd, operation)

        fcntl_double = SimpleNamespace(
            flock=signalling_flock,
            LOCK_EX=real_fcntl.LOCK_EX,
            LOCK_NB=real_fcntl.LOCK_NB,
            LOCK_UN=real_fcntl.LOCK_UN,
        )
        entered: list[str] = []
        outcome: dict[str, BaseException] = {}

        def critical(name: str) -> None:
            try:
                with self.markers.marker_lock(self.root, "s"):
                    entered.append(name)
            except BaseException as exc:  # reason: the test thread reports the outcome
                outcome[name] = exc

        (self.root / "s.json").write_text("{}", encoding="utf-8")
        with (
            mock.patch.object(self.leases, "marker_path", paused_marker_path),
            mock.patch.object(self.markers, "fcntl", fcntl_double),
        ):
            ender = threading.Thread(
                target=self.leases.deactivate_marker, args=({"session_id": "s"},), name="ender"
            )
            ender.start()
            self.assertTrue(ender_inside.wait(HANG_GUARD_SECS))
            waiter = threading.Thread(target=critical, args=("waiter",), name="waiter")
            waiter.start()
            self.assertTrue(waiter_waiting.wait(HANG_GUARD_SECS))
            ender_release.set()
            for thread in (ender, waiter):
                thread.join(HANG_GUARD_SECS)
                self.assertFalse(thread.is_alive())
        self.assertEqual([], entered, "the in-flight waiter ran after its session ended")
        self.assertIsInstance(outcome.get("waiter"), FileNotFoundError)
        self.assertEqual([], sorted(p.name for p in self.root.iterdir()))

        # A caller that arrives after session end starts a fresh lock as usual.
        late = threading.Thread(target=critical, args=("late",), name="late")
        late.start()
        late.join(HANG_GUARD_SECS)
        self.assertEqual(["late"], entered)
        self.assertNotIn("late", outcome)

    def test_a_waiter_in_flight_during_the_stale_sweep_retries_on_a_fresh_lock(self) -> None:
        # The waiter is held between opening the lock file and locking it while the stale
        # sweep removes that file, so the race is exercised every run. Unlike session end,
        # the session is live: the waiter must retry on a fresh file, not drop its update.
        lock_path = self.root / ".s.lock"
        lock_path.write_text("", encoding="utf-8")
        old = time.time() - 8 * 24 * 3600
        os.utime(lock_path, (old, old))
        # Keeping the old file open pins its inode number, so a fresh file cannot reuse it.
        pinned = os.open(lock_path, os.O_RDONLY)
        self.addCleanup(os.close, pinned)
        old_inode = os.fstat(pinned).st_ino
        waiter_opened, sweep_done = threading.Event(), threading.Event()
        real_fcntl = self.markers.fcntl

        def held_flock(fd: int, operation: int) -> None:
            if (
                threading.current_thread().name == "waiter"
                and operation == real_fcntl.LOCK_EX
                and not waiter_opened.is_set()
            ):
                waiter_opened.set()
                self.assertTrue(sweep_done.wait(HANG_GUARD_SECS))
            real_fcntl.flock(fd, operation)

        fcntl_double = SimpleNamespace(
            flock=held_flock,
            LOCK_EX=real_fcntl.LOCK_EX,
            LOCK_NB=real_fcntl.LOCK_NB,
            LOCK_UN=real_fcntl.LOCK_UN,
        )
        held_inodes: list[int] = []
        outcome: dict[str, BaseException] = {}

        def critical() -> None:
            try:
                with self.markers.marker_lock(self.root, "s"):
                    held_inodes.append(os.stat(lock_path).st_ino)
            except BaseException as exc:  # reason: the test thread reports the outcome
                outcome["waiter"] = exc

        with mock.patch.object(self.markers, "fcntl", fcntl_double):
            waiter = threading.Thread(target=critical, name="waiter")
            waiter.start()
            self.assertTrue(waiter_opened.wait(HANG_GUARD_SECS))
            self.markers.sweep_stale_locks(self.root, time.time())
            self.assertFalse(lock_path.exists(), "the sweep did not remove the stale lock")
            sweep_done.set()
            waiter.join(HANG_GUARD_SECS)
            self.assertFalse(waiter.is_alive())
        self.assertEqual({}, outcome)
        self.assertEqual(1, len(held_inodes), "the waiter dropped its update after the sweep")
        self.assertNotEqual(old_inode, held_inodes[0])
