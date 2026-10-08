"""The CLI creates Model Studio endpoints and fills route limits from the catalog."""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents import (
    cli,
)
from tests.agents.profiles_fixture import read_profiles, write_profiles

ROOT = Path(__file__).resolve().parents[2]


class ModelStudioCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.routes = Path(self.tmp.name) / "profiles.toml"
        self.env = {
            "HOME": self.tmp.name,
            "PITWALL_AGENTS_PROFILES": str(self.routes),
            "PATH": os.environ.get("PATH", ""),
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *argv: str, extra_env: dict[str, str] | None = None) -> tuple[int, str]:
        err = io.StringIO()
        with (
            mock.patch.dict(os.environ, {**self.env, **(extra_env or {})}, clear=True),
            contextlib.redirect_stderr(err),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = cli.main(list(argv))
        return code, err.getvalue()

    def test_endpoint_is_created_with_env_defaults(self) -> None:
        code, err = self.run_cli(
            "profiles",
            "add-model-studio-endpoint",
            "ms",
            "--renews-on",
            "2026-09-12",
            extra_env={
                "MODEL_STUDIO_PLAN": "token-plan-personal",
                "MODEL_STUDIO_TIER": "pro",
                "MODEL_STUDIO_TOKEN_PLAN_AUTOMATION": "accept",
            },
        )
        self.assertEqual(0, code, err)
        endpoint = read_profiles(self.routes)["endpoints"]["ms"]
        self.assertEqual("MODEL_STUDIO_API_KEY", endpoint["apiKeyEnv"])
        self.assertEqual("accept", endpoint["tokenPlanAutomation"])
        self.assertEqual(8, endpoint["concurrency"])

    def test_automation_env_does_not_leak_onto_pay_as_you_go(self) -> None:
        code, err = self.run_cli(
            "profiles",
            "add-model-studio-endpoint",
            "payg",
            "--plan",
            "pay-as-you-go",
            "--region",
            "ap-southeast-1",
            extra_env={"MODEL_STUDIO_TOKEN_PLAN_AUTOMATION": "accept"},
        )
        self.assertEqual(0, code, err)
        self.assertNotIn(
            "tokenPlanAutomation",
            read_profiles(self.routes)["endpoints"]["payg"],
        )

    def test_route_add_fills_limits_and_pins_the_endpoint_harness(self) -> None:
        # The vendor default differs from the endpoint default so the pin cannot pick the wrong one.
        write_profiles(
            self.routes, {"defaults": {"harness": "codex", "endpointHarness": "opencode"}}
        )
        self.run_cli(
            "profiles",
            "add-model-studio-endpoint",
            "ms",
            "--plan",
            "token-plan-personal",
            "--tier",
            "pro",
        )
        code, err = self.run_cli(
            "profiles", "add", "flash", "--model", "qwen3.8-flash", "--endpoint", "ms"
        )
        self.assertEqual(0, code, err)
        route = read_profiles(self.routes)["models"]["flash"]
        self.assertEqual({"context": 991808, "output": 131072}, route["limits"])
        self.assertEqual("opencode", route["harness"])

    def test_route_add_keeps_an_explicit_harness(self) -> None:
        self.run_cli(
            "profiles",
            "add-model-studio-endpoint",
            "ms",
            "--plan",
            "token-plan-personal",
            "--tier",
            "pro",
        )
        code, err = self.run_cli(
            "profiles",
            "add",
            "flash",
            "--model",
            "qwen3.8-flash",
            "--endpoint",
            "ms",
            "--harness",
            "pi",
        )
        self.assertEqual(0, code, err)
        self.assertEqual("pi", read_profiles(self.routes)["models"]["flash"]["harness"])

    def test_model_without_catalog_limits_needs_explicit_limits(self) -> None:
        self.run_cli(
            "profiles",
            "add-model-studio-endpoint",
            "ms",
            "--plan",
            "token-plan-personal",
            "--tier",
            "pro",
        )
        code, err = self.run_cli("profiles", "add", "router", "--model", "auto", "--endpoint", "ms")
        self.assertEqual(2, code)
        self.assertIn("--limits", err)

    def test_invalid_endpoint_is_refused_without_writing(self) -> None:
        code, err = self.run_cli(
            "profiles",
            "add-model-studio-endpoint",
            "ms",
            "--plan",
            "token-plan-personal",
            "--tier",
            "pro",
            "--region",
            "us-east-1",
        )
        self.assertEqual(2, code)
        self.assertIn("region_not_allowed", err)
        self.assertFalse(self.routes.exists())


if __name__ == "__main__":
    unittest.main()
