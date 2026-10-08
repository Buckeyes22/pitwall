"""Claude Code receives its prompt on stdin, so prompt size is not bounded by one argv entry."""

from __future__ import annotations

import unittest

from tests.agents.shim_test_support import ShimSandbox


class ClaudeStdinTests(unittest.TestCase):
    def test_a_200_kib_prompt_is_delivered_on_stdin(self) -> None:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness("claude")
        text = "x" * (200 * 1024) + "\n"
        result = sandbox.run("claude", [str(sandbox.prompt(text))])
        self.assertEqual(0, result.returncode, result.stderr.decode(errors="replace"))
        self.assertEqual(text.encode(), sandbox.captured_stdin())
        arguments = sandbox.captured_args()
        self.assertNotIn(text.rstrip("\n"), arguments)
        self.assertIn("-p", arguments)
