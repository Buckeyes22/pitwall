"""Profile sync covers routes that inherit defaults.endpointHarness and OpenCode status matches sync."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from pitwall.agents import profiles, profiles_resolve, profiles_sync
from pitwall.agents.registry import load_registry

REGISTRY = load_registry()
ROUTE = "inherited"


def config(endpoint_harness: str, *, pinned: bool = False) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "model": "my-model",
        "endpoint": {"baseUrl": "http://127.0.0.1:8080/v1"},
    }
    if pinned:
        entry["harness"] = endpoint_harness
    return profiles.validate_profiles(
        {
            "schemaVersion": 1,
            "defaults": {"harness": "opencode", "endpointHarness": endpoint_harness},
            "models": {ROUTE: entry},
        },
        registry=REGISTRY,
    )


class InheritedHarnessSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.home = Path(self.directory.name)
        self.env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "PATH": os.environ.get("PATH", ""),
        }

    def tearDown(self) -> None:
        self.directory.cleanup()

    def plans(self, routes: dict[str, Any], harness: str) -> list[profiles_sync.SyncPlan]:
        return profiles_sync.plan_sync(
            routes, registry=REGISTRY, env=self.env, home=self.home, harness=harness
        )

    def sync_status(self, routes: dict[str, Any]) -> str:
        resolved = profiles_resolve.resolve_profile(
            ROUTE,
            registry=REGISTRY,
            routes=routes,
            env=self.env,
            home=self.home,
            prompt_source="<prompt>",
            caller_args=(),
        )
        return resolved.sync_status

    def assert_current_after_apply(self, harness: str) -> None:
        routes = config(harness)
        self.assertNotIn("harness", routes["models"][ROUTE])
        with self.assertRaises(profiles_resolve.ProfileConfigError):
            self.sync_status(routes)  # missing before sync
        with mock.patch.dict(os.environ, self.env):
            for plan in self.plans(routes, harness):
                profiles_sync.apply_plan(plan)
        self.assertEqual("synced", self.sync_status(routes))

    def test_hermes_syncs_a_route_that_inherits_the_endpoint_harness(self) -> None:
        (plan,) = self.plans(config("hermes"), "hermes")
        self.assertIn(f"  {ROUTE}:\n", plan.after)
        self.assert_current_after_apply("hermes")

    def test_dsh_syncs_a_route_that_inherits_the_endpoint_harness(self) -> None:
        plans = self.plans(config("dsh"), "dsh")
        self.assertTrue(any(ROUTE in plan.after for plan in plans))
        self.assert_current_after_apply("dsh")

    def test_cline_syncs_a_route_that_inherits_the_endpoint_harness(self) -> None:
        providers = self.home / ".cline" / "data" / "settings" / "providers.json"
        fake = self.home / "cline"
        fake.write_text(
            "#!/bin/sh\n"
            f'mkdir -p "{providers.parent}"\n'
            f'printf \'{{"provider":"openai-compatible","baseUrl":"%s","modelId":"%s"}}\' '
            f'"http://127.0.0.1:8080/v1" "my-model" > "{providers}"\n',
            encoding="utf-8",
        )
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.env["CLINE_BIN"] = str(fake)
        (plan,) = self.plans(config("cline"), "cline")
        self.assertEqual(1, len(plan.commands))
        self.assertIn("http://127.0.0.1:8080/v1", plan.commands[0])
        self.assert_current_after_apply("cline")

    def test_pinned_and_inherited_routes_plan_identically(self) -> None:
        inherited = self.plans(config("hermes"), "hermes")
        pinned = self.plans(config("hermes", pinned=True), "hermes")
        self.assertEqual([p.after for p in pinned], [p.after for p in inherited])

    def test_effective_harness_is_resolved_in_one_place(self) -> None:
        routes = config("hermes")
        entry = routes["models"][ROUTE]
        self.assertEqual(
            "hermes", profiles_resolve.effective_harness(entry, routes=routes, registry=REGISTRY)
        )
        pinned = {**entry, "harness": "qwen"}
        self.assertEqual(
            "qwen", profiles_resolve.effective_harness(pinned, routes=routes, registry=REGISTRY)
        )


class OpenCodeAnthropicStatusTests(unittest.TestCase):
    def test_anthropic_model_studio_route_is_synced_right_after_opencode_sync(self) -> None:
        routes = profiles.validate_profiles(
            {
                "schemaVersion": 1,
                "endpoints": {
                    "ms": {
                        "kind": "model-studio",
                        "plan": "token-plan-personal",
                        "tier": "pro",
                        "apiKeyEnv": "MODEL_STUDIO_API_KEY",
                        "protocol": "anthropic",
                    }
                },
                "models": {
                    "flash": {
                        "model": "qwen3.8-flash",
                        "endpoint": "ms",
                        "harness": "opencode",
                        "limits": {"context": 991808, "output": 131072},
                    }
                },
            },
            registry=REGISTRY,
        )
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            env = {"HOME": directory, "XDG_CONFIG_HOME": str(home / "config")}
            (plan,) = profiles_sync.plan_sync(
                routes, registry=REGISTRY, env=env, home=home, harness="opencode"
            )
            profiles_sync.apply_plan(plan)
            resolved = profiles_resolve.resolve_profile(
                "flash",
                registry=REGISTRY,
                routes=routes,
                env=env,
                home=home,
                prompt_source="<prompt>",
                caller_args=(),
            )
        self.assertEqual("synced", resolved.sync_status)


if __name__ == "__main__":
    unittest.main()
