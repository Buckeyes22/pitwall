"""A managed route must execute through the harness verified before launch."""

from __future__ import annotations

import io
import threading
import unittest
from contextlib import redirect_stderr
from types import SimpleNamespace
from unittest import mock

from pitwall.agents import profiles_resolve
from pitwall.agents.mcp_tools import OrchestratorTools

EXPECTED_KEY = "PITWALL_AGENTS_MANAGED_EXPECTED_HARNESS"


class ManagedRoutePinTests(unittest.TestCase):
    def _dispatch(self, resolved_harness: str, expected_harness: str) -> tuple[int, str, mock.Mock]:
        resolved = SimpleNamespace(
            harness=resolved_harness,
            name="chosen",
            model="example/model",
            endpoint_host=None,
            notices=(),
            env_updates={},
            argv=("prompt.md",),
        )
        stderr = io.StringIO()
        with (
            mock.patch.object(profiles_resolve, "load_registry", return_value={}),
            mock.patch.object(profiles_resolve, "load_profiles", return_value={"models": {}}),
            mock.patch.object(profiles_resolve, "resolve_profile", return_value=resolved),
            mock.patch.object(profiles_resolve, "dispatch_legacy", return_value=0) as dispatch,
            redirect_stderr(stderr),
        ):
            code = profiles_resolve.dispatch_profile(
                ["chosen", "prompt.md"],
                environ={"HOME": "/tmp", EXPECTED_KEY: expected_harness},
            )
        return code, stderr.getvalue(), dispatch

    def test_changed_harness_is_rejected_before_harness_launch(self) -> None:
        code, output, dispatch = self._dispatch("opencode", "codex")

        self.assertEqual(profiles_resolve.EX_CONFIG, code)
        self.assertIn("changed harness after preflight", output)
        dispatch.assert_not_called()

    def test_matching_harness_runs_without_leaking_the_internal_pin(self) -> None:
        code, _output, dispatch = self._dispatch("codex", "codex")

        self.assertEqual(0, code)
        self.assertNotIn(EXPECTED_KEY, dispatch.call_args.kwargs["environ"])

    def test_managed_parent_pins_the_verified_harness_on_launch(self) -> None:
        tools = OrchestratorTools({"HOME": "/tmp"})
        with (
            mock.patch.object(tools, "_require_child_channel", return_value="codex"),
            mock.patch.object(tools, "_launch_request", return_value=object()),
            mock.patch(
                "pitwall.agents.mcp_tools.start_dispatch", return_value={"launcher": {}}
            ) as launch,
            mock.patch.object(tools, "_wait", return_value={"event": "terminal"}),
        ):
            tools.dispatch_and_wait({"route": "chosen"}, threading.Event(), lambda _message: None)

        self.assertEqual("codex", launch.call_args.args[0][EXPECTED_KEY])


if __name__ == "__main__":
    unittest.main()
