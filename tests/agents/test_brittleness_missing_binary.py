"""A missing harness CLI at dispatch says how to install it and how to check the setup."""

from __future__ import annotations

import unittest

from tests.agents.shim_test_support import ShimSandbox

HARNESSES = ("codex", "claude", "grok", "qwen", "pi", "goose", "hermes", "agy")


class MissingBinaryHintTests(unittest.TestCase):
    def test_missing_binary_points_at_setup_and_doctor(self) -> None:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        for harness in HARNESSES:
            with self.subTest(harness=harness):
                result = sandbox.run(harness, [str(sandbox.prompt())])
                stderr = result.stderr.decode()
                self.assertEqual(127, result.returncode, stderr)
                self.assertIn("pitwall agents setup harnesses", stderr)
                self.assertIn(f"select {harness}", stderr)
                self.assertIn("pitwall agents doctor", stderr)
