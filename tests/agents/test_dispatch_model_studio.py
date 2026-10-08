"""dispatch_route gates Token Plan automation, key pairing, lockouts, and concurrency."""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from pitwall.agents import endpoints, profiles, profiles_resolve
from pitwall.providers.model_studio import catalog as model_studio

ROOT = Path(__file__).resolve().parents[2]

ENDPOINT = model_studio.validate_endpoint(
    {
        "kind": "model-studio",
        "plan": "token-plan-personal",
        "tier": "pro",
        "apiKeyEnv": "MODEL_STUDIO_API_KEY",
        "renewsOn": "2026-09-12",
    },
    "e",
)


def resolved(endpoint: dict[str, object]) -> SimpleNamespace:
    entry = {"model": "qwen3.8-flash", "endpoint": profiles.EndpointReference("ms", endpoint)}
    return SimpleNamespace(
        harness="opencode",
        name="flash",
        model="qwen3.8-flash",
        endpoint_host="token-plan",
        notices=(),
        env_updates={},
        argv=("p.md",),
        entry=entry,
    )


class DispatchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def dispatch(
        self, endpoint: dict[str, object], key: str = "sk-sp-x", **extra_env: str
    ) -> tuple[int, str, mock.Mock]:
        env = {
            "HOME": self.tmp.name,
            "PITWALL_AGENTS_STATE_HOME": self.tmp.name,
            "MODEL_STUDIO_API_KEY": key,
            **extra_env,
        }
        stderr = io.StringIO()
        with (
            mock.patch.object(profiles_resolve, "load_registry", return_value={}),
            mock.patch.object(profiles_resolve, "load_profiles", return_value={"models": {}}),
            mock.patch.object(profiles_resolve, "resolve_profile", return_value=resolved(endpoint)),
            mock.patch.object(profiles_resolve, "dispatch_legacy", return_value=0) as legacy,
            redirect_stderr(stderr),
        ):
            code = profiles_resolve.dispatch_profile(["flash", "p.md"], environ=env)
        return code, stderr.getvalue(), legacy

    def test_token_plan_without_acceptance_is_refused(self) -> None:
        code, err, legacy = self.dispatch(ENDPOINT)
        self.assertEqual(profiles_resolve.EX_CONFIG, code)
        self.assertIn("automation_not_accepted", err)
        self.assertIn(model_studio.AUTOMATION_ENV, err)
        legacy.assert_not_called()

    def test_wrong_key_is_refused_without_echoing_it(self) -> None:
        code, err, legacy = self.dispatch(
            {**ENDPOINT, "tokenPlanAutomation": "accept"}, key="sk-secretvalue"
        )
        self.assertEqual(profiles_resolve.EX_CONFIG, code)
        self.assertIn("key_plan_mismatch", err)
        self.assertNotIn("secretvalue", err)
        legacy.assert_not_called()

    def test_accepted_dispatch_runs_inside_a_slot_with_a_dispatch_id(self) -> None:
        code, _err, legacy = self.dispatch({**ENDPOINT, "tokenPlanAutomation": "accept"})
        self.assertEqual(0, code)
        self.assertIn("PITWALL_AGENTS_DISPATCH_ID", legacy.call_args.kwargs["environ"])

    def test_busy_endpoint_is_refused_with_tempfail(self) -> None:
        accepted = {**ENDPOINT, "tokenPlanAutomation": "accept", "concurrency": 1}
        env = {"PITWALL_AGENTS_STATE_HOME": self.tmp.name}
        with endpoints.acquire_slot(env, "ms", 1, wait_seconds=0):
            code, err, legacy = self.dispatch(accepted, PITWALL_AGENTS_TIMEOUT_SECS="0.2")
        self.assertEqual(endpoints.EX_TEMPFAIL, code)
        self.assertIn("endpoint_busy", err)
        legacy.assert_not_called()

    def test_locked_endpoint_is_refused_until_renewal(self) -> None:
        from datetime import datetime, timedelta

        env = {"PITWALL_AGENTS_STATE_HOME": self.tmp.name}
        endpoints.write_lockout(env, "ms", datetime.now(UTC) + timedelta(days=1))
        code, err, legacy = self.dispatch({**ENDPOINT, "tokenPlanAutomation": "accept"})
        self.assertEqual(endpoints.EX_TEMPFAIL, code)
        self.assertIn("credits_exhausted", err)
        legacy.assert_not_called()

    def test_routes_without_an_entry_attribute_still_dispatch(self) -> None:
        plain = SimpleNamespace(
            harness="codex",
            name="c",
            model="m",
            endpoint_host=None,
            notices=(),
            env_updates={},
            argv=("p.md",),
        )
        with (
            mock.patch.object(profiles_resolve, "load_registry", return_value={}),
            mock.patch.object(profiles_resolve, "load_profiles", return_value={"models": {}}),
            mock.patch.object(profiles_resolve, "resolve_profile", return_value=plain),
            mock.patch.object(profiles_resolve, "dispatch_legacy", return_value=0),
            redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(
                0, profiles_resolve.dispatch_profile(["c", "p.md"], environ={"HOME": self.tmp.name})
            )


if __name__ == "__main__":
    unittest.main()
