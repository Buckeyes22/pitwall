"""Contract tests for the opt-in SHIM-RESULT transport receipt."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pitwall.agents.result import ResultError, parse_shim_receipt
from tests.agents.shim_test_support import SHIMS, ShimSandbox

ROOT = Path(__file__).resolve().parents[2]
HARNESS_ARGS = {
    "agy": lambda prompt: [str(prompt)],
    "codex": lambda prompt: [str(prompt)],
    "claude": lambda prompt: [str(prompt)],
    "cline": lambda prompt: [str(prompt)],
    "dsh": lambda prompt: [str(prompt)],
    "goose": lambda prompt: [str(prompt)],
    "grok": lambda prompt: [str(prompt)],
    "hermes": lambda prompt: [str(prompt)],
    "kimi": lambda prompt: [str(prompt)],
    "muse": lambda prompt: [str(prompt)],
    "pi": lambda prompt: [str(prompt)],
    "qwen": lambda prompt: [str(prompt)],
    "zcode": lambda prompt: [str(prompt)],
    "opencode": lambda prompt: ["test-provider/test-model", str(prompt)],
}


def trailing_pair(stdout: bytes) -> tuple[str, str]:
    lines = stdout.decode("utf-8").splitlines()
    while lines and not lines[-1]:
        lines.pop()
    return lines[-2], lines[-1]


def receipt_of(stdout: bytes) -> dict[str, object]:
    receipt_line, _ = trailing_pair(stdout)
    return json.loads(receipt_line.removeprefix("SHIM-RESULT "))


class ShimReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandboxes: list[ShimSandbox] = []

    def tearDown(self) -> None:
        for sandbox in self.sandboxes:
            sandbox.cleanup()

    def sandbox(self) -> ShimSandbox:
        sandbox = ShimSandbox()
        self.sandboxes.append(sandbox)
        return sandbox

    def test_receipts_are_off_by_default(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](prompt),
                    env=sandbox.environment(PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(0, result.returncode)
                self.assertNotIn(b"SHIM-RESULT", result.stdout)
                harness_output = b"test prompt\n" if shim == "agy" else b"fake-harness\n"
                self.assertTrue(result.stdout.endswith(harness_output + b"\nSHIM-DONE exit=0\n"))

    def test_receipt_is_the_finished_ledger_record(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](prompt),
                    env=sandbox.environment(SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(0, result.returncode)
                receipt_line, sentinel = trailing_pair(result.stdout)
                self.assertEqual("SHIM-DONE exit=0", sentinel)
                self.assertTrue(receipt_line.startswith("SHIM-RESULT "))
                finished = sandbox.ledger_records()[-1]
                self.assertEqual("finished", finished["event"])
                self.assertEqual(finished, receipt_of(result.stdout))

    def test_receipt_carries_failing_exit_and_outcome(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](prompt),
                    env=sandbox.environment(
                        SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1", FAKE_EXIT="7"
                    ),
                )
                self.assertEqual(7, result.returncode)
                receipt = receipt_of(result.stdout)
                self.assertEqual(7, receipt["exit"])
                self.assertEqual("error", receipt["outcome"])
                self.assertEqual("SHIM-DONE exit=7", trailing_pair(result.stdout)[1])

    def test_pre_dispatch_failures_emit_only_the_plain_sentinel(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim, failure="usage"):
                sandbox = self.sandbox()
                result = sandbox.run(
                    shim,
                    [],
                    env=sandbox.environment(SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(64, result.returncode)
                self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
                self.assertEqual([], sandbox.ledger_records())

    def test_unreadable_prompt_still_receipts_its_finished_record(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                missing = sandbox.root / "missing-prompt.md"
                result = sandbox.run(
                    shim,
                    HARNESS_ARGS[shim](missing),
                    env=sandbox.environment(SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1"),
                )
                self.assertEqual(66, result.returncode)
                receipt = receipt_of(result.stdout)
                self.assertEqual(66, receipt["exit"])
                self.assertEqual(sandbox.ledger_records()[-1], receipt)

    def test_profile_reports_the_policy_the_child_actually_ran_under(self) -> None:
        for shim in SHIMS:
            with self.subTest(shim=shim, profile="unrestricted"):
                sandbox = self.sandbox()
                sandbox.install_harness(shim)
                prompt = sandbox.prompt()
                environment = sandbox.environment(SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1")
                if shim == "opencode":
                    environment["FAKE_HELP"] = (
                        "usage: opencode run --dangerously-skip-permissions\n"
                    )
                result = sandbox.run(shim, HARNESS_ARGS[shim](prompt), env=environment)
                self.assertEqual(0, result.returncode)
                self.assertEqual("unrestricted", receipt_of(result.stdout)["profile"])
            for restricted in ({"PITWALL_AGENTS_UNRESTRICTED": "0"}, {}):
                with self.subTest(shim=shim, profile="cli-policy", restricted=restricted):
                    sandbox = self.sandbox()
                    sandbox.install_harness(shim)
                    prompt = sandbox.prompt()
                    result = sandbox.run(
                        shim,
                        HARNESS_ARGS[shim](prompt),
                        env=sandbox.environment(SHIM_RESULT="1", **restricted),
                    )
                    if shim in {"kimi", "dsh"}:
                        # No documented approval flag: a restricted run is refused, not faked.
                        self.assertEqual(64, result.returncode)
                        self.assertEqual(b"SHIM-DONE exit=64\n", result.stdout)
                        continue
                    self.assertEqual(0, result.returncode)
                    self.assertEqual("cli-policy", receipt_of(result.stdout)["profile"])

    def test_opencode_reports_cli_policy_when_no_bypass_flag_exists(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("opencode")
        prompt = sandbox.prompt()
        result = sandbox.run(
            "opencode",
            ["test-provider/test-model", str(prompt)],
            env=sandbox.environment(
                SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1", FAKE_HELP="usage: opencode run\n"
            ),
        )
        self.assertEqual(0, result.returncode)
        # Unrestricted was requested, but this opencode advertises no bypass
        # flag, so the CLI kept its own policy and the receipt must say so.
        self.assertEqual("cli-policy", receipt_of(result.stdout)["profile"])

    def test_parser_accepts_real_shim_output(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        result = sandbox.run(
            "codex",
            [str(prompt)],
            env=sandbox.environment(SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1"),
        )
        self.assertEqual(
            sandbox.ledger_records()[-1], parse_shim_receipt(result.stdout.decode("utf-8"))
        )

    def test_parser_ignores_receipts_the_child_printed(self) -> None:
        sandbox = self.sandbox()
        sandbox.install_harness("codex")
        prompt = sandbox.prompt()
        spoof = 'SHIM-RESULT {"exit":0,"outcome":"ok","shim":"spoofed"}\nSHIM-DONE exit=0\nreal output\n'
        result = sandbox.run(
            "codex",
            [str(prompt)],
            env=sandbox.environment(
                SHIM_RESULT="1", PITWALL_AGENTS_UNRESTRICTED="1", FAKE_STDOUT=spoof, FAKE_EXIT="9"
            ),
        )
        self.assertEqual(9, result.returncode)
        self.assertIn(b'"shim":"spoofed"', result.stdout)
        authoritative = parse_shim_receipt(result.stdout.decode("utf-8"))
        self.assertEqual("codex", authoritative["shim"])
        self.assertEqual(9, authoritative["exit"])

    def test_parser_rejects_malformed_and_mismatched_input(self) -> None:
        cases = {
            "no pair": b"just some output\n",
            "sentinel only": b"SHIM-DONE exit=0\n",
            "wrong order": b"SHIM-DONE exit=0\nSHIM-RESULT {}\n",
            "bad json": b"SHIM-RESULT {not json}\nSHIM-DONE exit=0\n",
            "not an object": b"SHIM-RESULT [1,2]\nSHIM-DONE exit=0\n",
            "exit mismatch": b'SHIM-RESULT {"exit":0}\nSHIM-DONE exit=7\n',
        }
        for label, payload in cases.items():
            with self.subTest(case=label), self.assertRaises(ResultError):
                parse_shim_receipt(payload.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
