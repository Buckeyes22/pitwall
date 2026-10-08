"""A slow `ps` is a timeout, not "no such process"; it is never recorded as a None identity."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents import pids


class PsTimeoutTests(unittest.TestCase):
    def no_procfs(self) -> mock._patch[mock.MagicMock]:
        return mock.patch.object(Path, "read_text", side_effect=FileNotFoundError)

    def test_ps_timeout_is_generous(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="Mon Sep 22 03:12:42 2026\n")
        with (
            self.no_procfs(),
            mock.patch.object(pids.subprocess, "run", return_value=completed) as run,
        ):
            self.assertEqual("ps:Mon Sep 22 03:12:42 2026", pids.process_identity(4242))
        self.assertGreaterEqual(run.call_args.kwargs["timeout"], 5.0)

    def test_a_slow_first_probe_is_retried(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="Mon Sep 22 03:12:42 2026\n")
        with (
            self.no_procfs(),
            mock.patch.object(
                pids.subprocess,
                "run",
                side_effect=[subprocess.TimeoutExpired("ps", 5), completed],
            ),
        ):
            self.assertEqual("ps:Mon Sep 22 03:12:42 2026", pids.process_identity(4242))

    def test_persistent_timeout_raises_instead_of_returning_none(self) -> None:
        with (
            self.no_procfs(),
            mock.patch.object(
                pids.subprocess, "run", side_effect=subprocess.TimeoutExpired("ps", 5)
            ),
            self.assertRaises(pids.ProcessProbeTimeout),
        ):
            pids.process_identity(4242)

    def test_no_such_process_is_still_none(self) -> None:
        gone = subprocess.CompletedProcess([], 1, stdout="")
        with self.no_procfs(), mock.patch.object(pids.subprocess, "run", return_value=gone):
            self.assertIsNone(pids.process_identity(4242))

    def test_a_timed_out_probe_keeps_a_live_pid_alive_and_blocks_signalling(self) -> None:
        with (
            mock.patch.object(pids.os, "kill"),
            mock.patch.object(pids, "_process_state", return_value="S"),
            mock.patch.object(pids, "process_identity", side_effect=pids.ProcessProbeTimeout()),
        ):
            self.assertTrue(pids.pid_alive(4242, "ps:recorded"))
            with mock.patch.object(pids.os, "getpgrp", return_value=1):
                self.assertFalse(pids.terminate_process_group(4242, "ps:recorded"))
