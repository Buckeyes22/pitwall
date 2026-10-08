"""Opt-in smoke tests for a live OpenAI-compatible endpoint."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib import request

from tests.agents.shim_test_support import PITWALL

ROOT = Path(__file__).resolve().parents[2]
REQUIRED_ENV = (
    "SMR_LIVE_BASE_URL",
    "SMR_LIVE_API_KEY_ENV",
    "SMR_LIVE_CHAT_MODEL",
)
MISSING_ENV = tuple(name for name in REQUIRED_ENV if not os.environ.get(name))
SKIP_MESSAGE = "live endpoint tests require " + ", ".join(REQUIRED_ENV)


@unittest.skipIf(bool(MISSING_ENV), SKIP_MESSAGE)
class LiveEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        temporary_home = self._temporary_directory.name
        self.env = dict(os.environ)
        self.env["HOME"] = temporary_home
        self.env["XDG_CONFIG_HOME"] = str(Path(temporary_home) / "config")
        self.base_url = self.env["SMR_LIVE_BASE_URL"].rstrip("/")
        self.key_env = self.env["SMR_LIVE_API_KEY_ENV"]
        self.model = self.env["SMR_LIVE_CHAT_MODEL"]

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    def test_routes_discover_lists_chat_model(self) -> None:
        result = self._routes(
            "discover",
            "--base-url",
            self.base_url,
            "--api-key-env",
            self.key_env,
            "--json",
        )

        self.assertEqual(0, result.returncode, result.stderr)
        models = [item["id"] for item in json.loads(result.stdout)["models"]]
        self.assertIn(self.model, models)

    def test_routes_add_then_probe_reports_reachable(self) -> None:
        add = self._routes(
            "add",
            "live-endpoint",
            "--model",
            self.model,
            "--base-url",
            self.base_url,
            "--api-key-env",
            self.key_env,
        )
        self.assertEqual(0, add.returncode, add.stderr)

        probe = self._routes("probe", "live-endpoint", "--json")
        self.assertEqual(0, probe.returncode, probe.stderr)
        self.assertEqual("reachable", json.loads(probe.stdout)["status"])

    def test_raw_completion_returns_non_empty_message(self) -> None:
        body = json.dumps(
            {
                "model": self.model,
                "messages": [{"role": "user", "content": "Reply with pong."}],
            }
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        api_key = self.env.get(self.key_env)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = request.Request(
            self.base_url + "/chat/completions",
            data=body,
            headers=headers,
            method="POST",
        )

        with request.urlopen(req, timeout=180) as response:
            payload = json.loads(response.read().decode("utf-8"))

        message = payload["choices"][0]["message"]["content"]
        self.assertIsInstance(message, str)
        self.assertTrue(message.strip())

    def _routes(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "profiles",
                *args,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
