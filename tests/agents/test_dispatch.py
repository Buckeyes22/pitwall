"""Dispatch-level behaviour: timeout validation, no GNU timeout, and prompt delivery."""

from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path

from pitwall.agents.harnesses.base import ARGV_PROMPT_LIMIT_BYTES
from tests.agents.shim_test_support import SHIMS, ShimSandbox
from tests.agents.test_shim_contract import HARNESS_ARGS

ROOT = Path(__file__).resolve().parents[2]


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandboxes: list[ShimSandbox] = []

    def tearDown(self) -> None:
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def sandbox(self) -> ShimSandbox:
        value = ShimSandbox()
        self.sandboxes.append(value)
        return value

    def test_invalid_timeout_is_usage_error(self) -> None:
        for raw in ("abc", "0", "-5", "0s", "", "nan", "inf", "1x2"):
            with self.subTest(timeout=raw):
                sandbox = self.sandbox()
                sandbox.install_harness("qwen")
                prompt = sandbox.prompt()
                env = sandbox.environment(PITWALL_AGENTS_TIMEOUT_SECS=raw)
                result = sandbox.run("qwen", [str(prompt)], env=env)
                self.assertEqual(64, result.returncode)
                self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
                self.assertIn("PITWALL_AGENTS_TIMEOUT_SECS", result.stderr.decode())
                self.assertIn(f"PITWALL_AGENTS_TIMEOUT_SECS={raw!r}", result.stderr.decode())
                self.assertEqual(
                    b"", sandbox.args_file.read_bytes() if sandbox.args_file.exists() else b""
                )
                self.assertEqual([], sandbox.ledger_records())

    def test_valid_timeout_forms_still_dispatch(self) -> None:
        # Every form must parse to at least the hang guard: the value is the harness's real kill
        # timer, so a short one ("30s") times the fake harness out on a loaded machine.
        for raw in ("1140", "19m", "0.5h", "1140s"):
            with self.subTest(timeout=raw):
                sandbox = self.sandbox()
                sandbox.install_harness("qwen")
                result = sandbox.run(
                    "qwen",
                    [str(sandbox.prompt())],
                    env=sandbox.environment(PITWALL_AGENTS_TIMEOUT_SECS=raw),
                )
                self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))

    def test_dispatch_runs_without_gnu_timeout(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                env = sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1")
                self.assertIsNone(shutil.which("timeout", path=env["PATH"]))
                self.assertIsNone(shutil.which("gtimeout", path=env["PATH"]))
                result = sandbox.run(shim, HARNESS_ARGS[shim](sandbox.prompt()), env=env)
                self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
                self.assertEqual(
                    ["started", "finished"],
                    [record["event"] for record in sandbox.ledger_records()],
                )

    def test_pi_prompt_reaches_the_child_as_a_private_run_directory_file(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("pi")
        prompt_text = "pi private prompt text\nsecond line\n"
        report = sandbox.root / "at-report.json"
        env = sandbox.environment(FAKE_AT_FILE_REPORT=str(report))
        result = sandbox.run("pi", [str(sandbox.prompt(prompt_text))], env=env)
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        args = sandbox.captured_args()
        self.assertFalse(any("pi private prompt" in argument for argument in args))
        at_arguments = [argument for argument in args if argument.startswith("@")]
        self.assertEqual(1, len(at_arguments))
        seen = json.loads(report.read_text(encoding="utf-8"))
        self.assertEqual(1, len(seen))
        delivered = Path(seen[0]["path"])
        self.assertEqual(f"@{delivered}", at_arguments[0])
        self.assertEqual(0o600, seen[0]["mode"])
        self.assertEqual(prompt_text, seen[0]["content"])
        run_directories = sandbox.run_directories()
        self.assertEqual(1, len(run_directories))
        self.assertEqual(run_directories[0], delivered.parent)
        self.assertNotEqual(sandbox.root / "prompt.md", delivered)
        self.assertFalse(delivered.exists(), "the private prompt copy is removed when the run ends")

    def test_oversized_argv_prompt_is_a_usage_error_before_the_harness_starts(self) -> None:
        for shim in ("kimi", "dsh"):
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt("x" * (ARGV_PROMPT_LIMIT_BYTES + 1))
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](prompt),
                    env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(64, result.returncode)
                self.assertIn(str(ARGV_PROMPT_LIMIT_BYTES), result.stderr.decode())
                self.assertFalse(sandbox.args_file.exists())
                self.assertEqual(
                    b"SHIM-DONE exit=64\n", result.stdout[-len(b"SHIM-DONE exit=64\n") :]
                )

    def test_restricted_run_is_refused_for_harnesses_without_an_approval_flag(self) -> None:
        # Both an explicit 0 and an unset variable mean restricted.
        for shim in ("kimi", "dsh"):
            for overrides in ({"PITWALL_AGENTS_UNRESTRICTED": "0"}, {}):
                with self.subTest(shim=shim, overrides=overrides):
                    sandbox = self.sandbox()
                    sandbox.install_harness(shim)
                    env = sandbox.environment(**overrides)
                    result = sandbox.run(shim, HARNESS_ARGS[shim](sandbox.prompt()), env=env)
                    self.assertEqual(64, result.returncode)
                    self.assertIn("PITWALL_AGENTS_UNRESTRICTED=1", result.stderr.decode())
                    self.assertFalse(sandbox.args_file.exists())

    def test_unset_unrestricted_variable_keeps_the_codex_sandbox(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        result = sandbox.run("codex", HARNESS_ARGS["codex"](sandbox.prompt()))
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        args = sandbox.captured_args()
        self.assertEqual("workspace-write", args[args.index("--sandbox") + 1])
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", args)


if __name__ == "__main__":
    unittest.main()
