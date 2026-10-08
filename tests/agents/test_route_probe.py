"""Tests for the explicit route liveness probe."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from pitwall.agents import cli, endpoints, profiles_probe
from pitwall.agents.broker import PITWALL_TOKEN_ENV
from tests.agents.http_test_support import (
    LoopbackServer,
)
from tests.agents.shim_test_support import PITWALL

ROOT = Path(__file__).resolve().parents[2]


def entry(base_url: str, **extra: object) -> dict[str, object]:
    return {
        "model": "meta-models/Muse-Glimmer-30B",
        "endpoint": {"baseUrl": base_url, "apiKeyEnv": "PITWALL_API_TOKEN"},
        "args": [],
        "env": {},
        **extra,
    }


class ProbeTests(unittest.TestCase):
    def probe(self, server: LoopbackServer, **extra: object) -> profiles_probe.ProbeResult:
        return profiles_probe.probe_profile(
            "glimmer",
            entry(server.base_url + "/v1", **extra),
            env={"PITWALL_API_TOKEN": "tok"},
            timeout=2.0,
        )

    def test_reachable_when_model_listed_and_bearer_sent(self) -> None:
        with LoopbackServer(
            {"/v1/models": (200, {"data": [{"id": "meta-models/Muse-Glimmer-30B"}]})}
        ) as server:
            result = self.probe(server)
            self.assertEqual("reachable", result.status)
            self.assertEqual(("meta-models/Muse-Glimmer-30B",), result.models)
            self.assertEqual("Bearer tok", server.requests[0][1].get("Authorization"))
            self.assertIsNone(result.lease_id)
            self.assertIn("leaseId", result.to_dict())

    def test_malformed_model_list_payloads_are_down_with_a_fixed_detail(self) -> None:
        for label, body in {
            "list": [],
            "string": "x",
            "data is a string": {"data": "x"},
            "data is an object": {"data": {"id": "meta-models/Muse-Glimmer-30B"}},
            "data holds a non-object": {"data": [{"id": "meta-models/Muse-Glimmer-30B"}, "junk"]},
            "no data": {"object": "list"},
        }.items():
            with self.subTest(payload=label):
                with LoopbackServer({"/v1/models": (200, body)}) as server:
                    result = self.probe(server)
                self.assertEqual("down", result.status)
                self.assertEqual((), result.models)
                self.assertTrue(result.detail.endswith("returned a non-OpenAI models payload"))

    def test_endpoint_requests_name_the_client_not_python_urllib(self) -> None:
        # RunPod's proxy (Cloudflare error 1010) answers 403 to urllib's default
        # "Python-urllib" agent, which the probe would report as unauthorized.
        with LoopbackServer(
            {
                "/v1/models": (200, {"data": [{"id": "meta-models/Muse-Glimmer-30B"}]}),
                "/running": (200, {"running": []}),
            }
        ) as server:
            self.assertEqual("reachable", self.probe(server).status)
            endpoints.discover_models(server.base_url + "/v1", api_key="tok", timeout=2.0)
            agents = [headers.get("User-Agent", "") for _path, headers in server.requests]
        self.assertEqual(4, len(agents))
        for agent in agents:
            self.assertTrue(agent.startswith("pitwall-agents/"), agent)

    def test_route_probe_ignores_the_broker_token_variable(self) -> None:
        with LoopbackServer(
            {
                "/v1/models": (
                    200,
                    {"data": [{"id": "meta-models/Muse-Glimmer-30B"}]},
                )
            }
        ) as server:
            agents_entry = entry(server.base_url + "/v1")
            agents_entry["endpoint"]["apiKeyEnv"] = PITWALL_TOKEN_ENV  # type: ignore[index]  # reason: the test navigates a loosely typed JSON document
            result = profiles_probe.probe_profile(
                "glimmer",
                agents_entry,
                env={
                    PITWALL_TOKEN_ENV: "new-token",
                    "PITWALL_API_TOKEN": "broker-token",
                },
                timeout=2.0,
            )
            self.assertEqual("reachable", result.status)
            self.assertEqual(
                "Bearer new-token",
                server.requests[0][1].get("Authorization"),
            )

    def test_warming_when_running_reports_configured_model_starting(self) -> None:
        with LoopbackServer(
            {
                "/v1/models": (200, {"data": [{"id": "meta-models/Muse-Glimmer-30B"}]}),
                "/running": (
                    200,
                    {"running": [{"model": "meta-models/Muse-Glimmer-30B", "state": "starting"}]},
                ),
            }
        ) as server:
            result = self.probe(server)
        self.assertEqual("warming", result.status)
        self.assertEqual(
            "the model is loading; retry shortly — cold starts on swapper endpoints commonly take 1–2 minutes",
            result.remedy,
        )
        self.assertEqual(["/v1/models", "/running"], [path for path, _headers in server.requests])

    def test_model_missing_unauthorized_and_down(self) -> None:
        with LoopbackServer({"/v1/models": (200, {"data": [{"id": "other"}]})}) as server:
            self.assertEqual("model-missing", self.probe(server).status)
        with LoopbackServer({"/v1/models": (401, {"error": "nope"})}) as server:
            self.assertEqual("unauthorized", self.probe(server).status)
        with LoopbackServer({"/v1/models": (503, {"error": "HarnessUnavailable"})}) as server:
            result = self.probe(server)
            self.assertEqual(("down", 503), (result.status, result.http_status))
        refused = profiles_probe.probe_profile(
            "glimmer", entry("http://127.0.0.1:9/v1"), env={}, timeout=1.0
        )
        self.assertEqual("down", refused.status)

    def test_expired_short_circuits_and_pitwall_remedy_names_capability(self) -> None:
        with LoopbackServer({"/v1/models": (200, {"data": []})}) as server:
            result = profiles_probe.probe_profile(
                "glimmer",
                entry(
                    server.base_url + "/v1",
                    expiresAt="2000-01-01T00:00:00Z",
                    origin={
                        "kind": "pitwall",
                        "capability": "llm.glimmer",
                        "leaseId": "l1",
                        "url": server.base_url,
                    },
                ),
                env={"PITWALL_API_TOKEN": "tok"},
                now=datetime(2026, 1, 1, tzinfo=UTC),
                timeout=1.0,
            )
            self.assertEqual("expired", result.status)
            self.assertEqual([], server.requests)
            self.assertIn("pitwall serve --capability llm.glimmer", result.remedy or "")
            self.assertIn("profiles refresh glimmer", result.remedy or "")
            self.assertEqual("l1", result.lease_id)

    def test_routes_without_endpoint_are_not_applicable(self) -> None:
        result = profiles_probe.probe_profile(
            "sol", {"model": "gpt-5.6-sol", "args": [], "env": {}}, env={}
        )
        self.assertEqual("not-applicable", result.status)


class ProbeCliTests(unittest.TestCase):
    def test_timeout_flag_threads_into_probe_route(self) -> None:
        config = {"models": {"g": entry("http://localhost:9292/v1")}}
        result = profiles_probe.ProbeResult(
            "g", "warming", 200, ("m",), None, "retry shortly", "starting"
        )
        with (
            mock.patch.object(cli, "_routes_context", return_value=({}, config, Path("."))),
            mock.patch.object(cli, "probe_profile", return_value=result) as probe,
            mock.patch("sys.stdout"),
        ):
            self.assertEqual(0, cli.main(["profiles", "probe", "g", "--timeout", "2.5"]))
        probe.assert_called_once_with("g", config["models"]["g"], env=mock.ANY, timeout=2.5)

    def test_cli_exit_codes_and_json(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            LoopbackServer({"/v1/models": (200, {"data": [{"id": "m"}]})}) as server,
        ):
            env = {
                "HOME": directory,
                "XDG_CONFIG_HOME": str(Path(directory) / "config"),
                "PATH": os.environ.get("PATH", ""),
                "K": "tok",
            }
            cli = [
                str(PITWALL),
                "agents",
                "profiles",
            ]
            subprocess.run(
                [
                    *cli,
                    "add",
                    "g",
                    "--model",
                    "m",
                    "--base-url",
                    server.base_url + "/v1",
                    "--api-key-env",
                    "K",
                ],
                env=env,
                check=True,
                capture_output=True,
            )
            ok = subprocess.run(
                [*cli, "probe", "g", "--json"], env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(0, ok.returncode, ok.stderr)
            self.assertEqual("reachable", json.loads(ok.stdout)["status"])
            missing = subprocess.run(
                [*cli, "probe", "nosuch"], env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(1, missing.returncode)


if __name__ == "__main__":
    unittest.main()
