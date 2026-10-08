"""Tests for endpoint model discovery."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.agents.http_test_support import LoopbackServer
from tests.agents.shim_test_support import PITWALL

ROOT = Path(__file__).resolve().parents[2]


class RoutesDiscoverCliTests(unittest.TestCase):
    def cli(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        command = [
            str(PITWALL),
            "agents",
            "profiles",
            "discover",
            *args,
        ]
        return subprocess.run(command, env=env, capture_output=True, text=True, check=False)

    def test_catalog_lists_models_when_running_is_unavailable(self) -> None:
        with LoopbackServer(
            {"/v1/models": (200, {"data": [{"id": "alpha"}, {"id": "beta"}]})}
        ) as server:
            result = self.cli("--base-url", server.base_url + "/v1", "--timeout", "2")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("alpha\tunknown\nbeta\tunknown\n", result.stdout)

    def test_route_name_resolves_endpoint_key_and_running_states(self) -> None:
        routes = {
            "/v1/models": (200, {"data": [{"id": "alpha"}, {"id": "beta"}, {"id": "gamma"}]}),
            "/running": (
                200,
                {
                    "running": [
                        {"model": "alpha", "state": "ready"},
                        {"model": "beta", "state": "starting"},
                    ]
                },
            ),
        }
        with tempfile.TemporaryDirectory() as directory, LoopbackServer(routes) as server:
            env = {
                "HOME": directory,
                "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                "PATH": os.environ.get("PATH", ""),
                "MY_ENDPOINT_KEY": "test-key",
            }
            add = [
                str(PITWALL),
                "agents",
                "profiles",
                "add",
                "local",
                "--model",
                "alpha",
                "--base-url",
                server.base_url + "/v1",
                "--api-key-env",
                "MY_ENDPOINT_KEY",
            ]
            subprocess.run(add, env=env, check=True, capture_output=True)
            result = self.cli("local", "--json", "--timeout", "2", env=env)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(
            {
                "models": [
                    {"id": "alpha", "state": "ready"},
                    {"id": "beta", "state": "starting"},
                    {"id": "gamma", "state": "not-loaded"},
                ],
                "baseUrl": server.base_url + "/v1",
            },
            json.loads(result.stdout),
        )
        self.assertEqual("Bearer test-key", server.requests[0][1].get("Authorization"))
        self.assertEqual(["/v1/models", "/running"], [item[0] for item in server.requests])

    def test_unauthorized_and_non_json_errors_name_remedy_flags(self) -> None:
        with LoopbackServer({"/v1/models": (401, {"error": "unauthorized"})}) as server:
            unauthorized = self.cli("--base-url", server.base_url + "/v1")
        self.assertEqual(1, unauthorized.returncode)
        self.assertIn("--api-key-env", unauthorized.stderr)

        with LoopbackServer({"/v1/models": (200, b"not json")}) as server:
            non_json = self.cli("--base-url", server.base_url + "/v1")
        self.assertEqual(1, non_json.returncode)
        self.assertIn("--base-url", non_json.stderr)

    def test_missing_route_key_uses_existing_needs_key_message(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            LoopbackServer({"/v1/models": (200, {"data": [{"id": "alpha"}]})}) as server,
        ):
            env = {
                "HOME": directory,
                "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                "PATH": os.environ.get("PATH", ""),
                "MY_ENDPOINT_KEY": "test-key",
            }
            add = [
                str(PITWALL),
                "agents",
                "profiles",
                "add",
                "local",
                "--model",
                "alpha",
                "--base-url",
                server.base_url + "/v1",
                "--api-key-env",
                "MY_ENDPOINT_KEY",
            ]
            subprocess.run(add, env=env, check=True, capture_output=True)
            del env["MY_ENDPOINT_KEY"]
            result = self.cli("local", env=env)

        self.assertEqual(1, result.returncode)
        self.assertIn(
            "route 'local' needs its configured API-key environment variable",
            result.stderr,
        )


if __name__ == "__main__":
    unittest.main()
