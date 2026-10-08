"""A managed launch keeps child output in the run logs only."""

from __future__ import annotations

import tempfile
import unittest
import uuid
from unittest import mock

from pitwall.agents.managed_channel import LaunchRequest, start_dispatch
from pitwall.agents.run_store import MANAGED_LAUNCH_ENV
from tests.agents.shim_test_support import ShimSandbox


class ManagedLaunchOutputTests(unittest.TestCase):
    def test_managed_launch_does_not_mirror_child_output(self) -> None:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness("codex")
        env = sandbox.environment(
            PITWALL_AGENTS_MANAGED_LAUNCH="1",
            FAKE_STDOUT="the answer\n",
            FAKE_STDERR="the progress\n",
        )
        result = sandbox.run("codex", [str(sandbox.prompt())], env=env)
        self.assertEqual(0, result.returncode)
        # The supervisor's own streams are what the managed launcher writes to
        # launcher.stdout.log and launcher.stderr.log.
        self.assertNotIn(b"the answer", result.stdout)
        self.assertNotIn(b"the progress", result.stderr)
        self.assertIn(b"SHIM-DONE exit=0", result.stdout)
        (run,) = sandbox.run_directories()
        self.assertEqual(b"the answer\n", (run / "stdout.log").read_bytes())
        self.assertEqual(b"the progress\n", (run / "stderr.log").read_bytes())
        # The fixture records this key, so None proves the harness never saw it.
        captured = sandbox.captured_env()
        self.assertIn("PITWALL_AGENTS_MANAGED_LAUNCH", captured)
        self.assertIsNone(captured["PITWALL_AGENTS_MANAGED_LAUNCH"])

    def test_direct_launch_still_mirrors_child_output(self) -> None:
        sandbox = ShimSandbox()
        self.addCleanup(sandbox.cleanup)
        sandbox.install_harness("codex")
        env = sandbox.environment(FAKE_STDOUT="the answer\n", FAKE_STDERR="the progress\n")
        result = sandbox.run("codex", [str(sandbox.prompt())], env=env)
        self.assertEqual(0, result.returncode)
        self.assertIn(b"the answer", result.stdout)
        self.assertIn(b"the progress", result.stderr)

    def test_start_dispatch_marks_the_supervisor_as_a_managed_launch(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
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
                {"PITWALL_AGENTS_STATE_HOME": temporary.name},
                LaunchRequest(
                    dispatch_id=str(uuid.uuid4()), route=None, harness="codex", prompt="launch"
                ),
            )
        self.assertEqual("1", popen.call_args.kwargs["env"][MANAGED_LAUNCH_ENV])


if __name__ == "__main__":
    unittest.main()
