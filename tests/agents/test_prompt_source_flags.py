"""Flags in the prompt-source position are usage, never a run (and never a ledger row)."""

from __future__ import annotations

import os
import unittest

from tests.agents.shim_test_support import ShimSandbox


class PromptSourceFlagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.addCleanup(self.sandbox.cleanup)
        self.sandbox.install_harness("codex")

    def test_help_prints_usage_and_creates_no_run(self) -> None:
        result = self.sandbox.run("codex", ["--help"])
        self.assertEqual(0, result.returncode)
        self.assertIn(b"usage:", result.stdout)
        self.assertTrue(result.stdout.endswith(b"SHIM-DONE exit=0\n"), result.stdout)
        self.assertEqual([], self.sandbox.run_directories())
        self.assertEqual([], self.sandbox.ledger_records())

    def test_short_help_prints_usage_and_creates_no_run(self) -> None:
        result = self.sandbox.run("codex", ["-h"])
        self.assertEqual(0, result.returncode)
        self.assertIn(b"usage:", result.stdout)
        self.assertTrue(result.stdout.endswith(b"SHIM-DONE exit=0\n"), result.stdout)
        self.assertEqual([], self.sandbox.run_directories())
        self.assertEqual([], self.sandbox.ledger_records())

    def test_flag_first_is_a_usage_error_without_a_run(self) -> None:
        result = self.sandbox.run("codex", ["-m", "gpt-6-sol", str(self.sandbox.prompt())])
        self.assertEqual(64, result.returncode)
        self.assertIn(b"is a flag, not a prompt source", result.stderr)
        self.assertIn(b"./-name", result.stderr)
        self.assertEqual([], self.sandbox.run_directories())
        self.assertEqual([], self.sandbox.ledger_records())

    def test_long_flag_first_is_a_usage_error_without_a_run(self) -> None:
        result = self.sandbox.run("codex", ["--model", "gpt-6-sol", str(self.sandbox.prompt())])
        self.assertEqual(64, result.returncode)
        self.assertIn(b"is a flag, not a prompt source", result.stderr)
        self.assertEqual([], self.sandbox.run_directories())
        self.assertEqual([], self.sandbox.ledger_records())

    def test_stdin_prompt_source_still_dispatches(self) -> None:
        result = self.sandbox.run("codex", ["-"], input_bytes=b"hello\n")
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        self.assertEqual(1, len(self.sandbox.run_directories()))
        self.assertEqual(b"hello\n", self.sandbox.captured_stdin())

    def test_dash_prefixed_file_dispatches_as_dot_slash_name(self) -> None:
        self.addCleanup(os.chdir, os.getcwd())
        os.chdir(self.sandbox.root)
        (self.sandbox.root / "-name").write_text("dash prompt\n", encoding="utf-8")
        result = self.sandbox.run("codex", ["./-name"])
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        self.assertEqual(b"dash prompt\n", self.sandbox.captured_stdin())


class OtherAdapterFlagTests(unittest.TestCase):
    """OpenCode takes <provider/model> first; ZCode has a fixed model."""

    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.addCleanup(self.sandbox.cleanup)
        for harness in ("opencode", "zcode"):
            self.sandbox.install_harness(harness)

    def assert_no_run(self) -> None:
        self.assertEqual([], self.sandbox.run_directories())
        self.assertEqual([], self.sandbox.ledger_records())

    def test_help_exits_zero_for_opencode_and_zcode(self) -> None:
        for shim in ("opencode", "zcode"):
            for flag in ("--help", "-h"):
                with self.subTest(shim=shim, flag=flag):
                    result = self.sandbox.run(shim, [flag])
                    self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
                    self.assertIn(b"usage:", result.stdout)
                    self.assertTrue(result.stdout.endswith(b"SHIM-DONE exit=0\n"), result.stdout)
                    self.assert_no_run()

    def test_opencode_flag_in_model_position_is_usage(self) -> None:
        result = self.sandbox.run("opencode", ["-m", "x/y", str(self.sandbox.prompt())])
        self.assertEqual(64, result.returncode)
        self.assertIn(b"is a flag, not a model", result.stderr)
        self.assertIn(b"<provider/model>", result.stderr)
        self.assertNotIn(b"prompt file (or - for stdin) first", result.stderr)
        self.assert_no_run()

    def test_opencode_flag_in_prompt_position_is_usage(self) -> None:
        result = self.sandbox.run("opencode", ["x/y", "--help-me"])
        self.assertEqual(64, result.returncode)
        self.assertIn(b"is a flag, not a prompt source", result.stderr)
        self.assert_no_run()

    def test_zcode_flag_first_is_usage(self) -> None:
        result = self.sandbox.run("zcode", ["-m", "x", str(self.sandbox.prompt())])
        self.assertEqual(64, result.returncode)
        self.assertIn(b"is a flag, not a prompt source", result.stderr)
        self.assert_no_run()
