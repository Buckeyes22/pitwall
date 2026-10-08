"""profiles probe reports Model Studio pairing, reachability, and Credits."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

from pitwall.agents import endpoints, profiles, profiles_probe
from pitwall.providers.model_studio import catalog as model_studio
from pitwall.providers.model_studio import openapi as model_studio_openapi
from tests.agents.http_test_support import (
    LoopbackServer,
)

ROOT = Path(__file__).resolve().parents[2]

ENDPOINT = model_studio.validate_endpoint(
    {
        "kind": "model-studio",
        "plan": "token-plan-personal",
        "tier": "pro",
        "apiKeyEnv": "MODEL_STUDIO_API_KEY",
        "renewsOn": "2026-09-12",
        "tokenPlanAutomation": "accept",
    },
    "e",
)
NOW = datetime(2026, 9, 26, tzinfo=UTC)


def entry() -> dict[str, object]:
    return {
        "model": "qwen3.8-flash",
        "endpoint": profiles.EndpointReference("ms", ENDPOINT),
        "args": [],
        "env": {},
    }


class ProbeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {
            "MODEL_STUDIO_API_KEY": "sk-sp-x",
            "PITWALL_AGENTS_STATE_HOME": self.tmp.name,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def probe(self, server: LoopbackServer) -> profiles_probe.ProbeResult:
        def base_url(endpoint: object, protocol: str | None = None) -> str:
            # The Token Plan host answers GET /api/v1/models with 404 (live check, 2026-09-26);
            # only the OpenAI-compatible model list is served, so the probe must use it.
            if protocol not in (None, "openai"):
                raise AssertionError(f"probe used the {protocol} base URL")
            return server.base_url + "/compatible-mode/v1"

        with mock.patch.object(model_studio, "base_url", side_effect=base_url):
            return profiles_probe.probe_profile(
                "flash", entry(), env=self.env, now=NOW, timeout=2.0
            )

    def test_reachable_reports_renewal_without_stats(self) -> None:
        with LoopbackServer(
            {
                "/compatible-mode/v1/models": (
                    200,
                    {
                        "object": "list",
                        "data": [
                            {"id": "qwen3.8-max", "object": "model"},
                            {"id": "qwen3.8-flash", "object": "model"},
                        ],
                    },
                )
            }
        ) as server:
            result = self.probe(server)
            self.assertEqual("Bearer sk-sp-x", server.requests[0][1].get("Authorization"))
            self.assertEqual("/compatible-mode/v1/models", server.requests[0][0])
        self.assertEqual("reachable", result.status)
        self.assertIn("renews 2026-10-12", result.detail)

    def test_malformed_model_list_payloads_are_down_with_a_bounded_detail(self) -> None:
        for label, body in {
            "list": [],
            "string": "x",
            "data is a string": {"data": "x"},
            "data is an object": {"data": {"id": "qwen3.8-flash"}},
            "data holds non-mappings": {"data": ["qwen3.8-flash"]},
            "no data": {"object": "list"},
        }.items():
            with self.subTest(payload=label):
                with LoopbackServer({"/compatible-mode/v1/models": (200, body)}) as server:
                    result = self.probe(server)
                self.assertEqual("down", result.status)
                self.assertEqual(200, result.http_status)
                self.assertEqual((), result.models)
                self.assertLess(len(result.detail), 120)
                self.assertNotIn("qwen3.8-flash", result.detail)

    def test_missing_model(self) -> None:
        with LoopbackServer(
            {
                "/compatible-mode/v1/models": (
                    200,
                    {"object": "list", "data": [{"id": "qwen3.8-max", "object": "model"}]},
                )
            }
        ) as server:
            self.assertEqual("model-missing", self.probe(server).status)

    def test_wrong_key_is_misconfigured_without_a_request(self) -> None:
        self.env["MODEL_STUDIO_API_KEY"] = "sk-other"
        with LoopbackServer({}) as server:
            result = self.probe(server)
            self.assertEqual([], server.requests)
        self.assertEqual("misconfigured", result.status)
        self.assertIn("key_plan_mismatch", result.detail)

    def test_local_lockout_reports_quota_exhausted(self) -> None:
        endpoints.write_lockout(self.env, "ms", NOW + timedelta(days=2))
        with LoopbackServer(
            {
                "/compatible-mode/v1/models": (
                    200,
                    {
                        "object": "list",
                        "data": [
                            {"id": "qwen3.8-max", "object": "model"},
                            {"id": "qwen3.8-flash", "object": "model"},
                        ],
                    },
                )
            }
        ) as server:
            self.assertEqual("quota-exhausted", self.probe(server).status)

    def test_stats_report_remaining_credits(self) -> None:
        stats = model_studio_openapi.SubscriptionStats(
            NOW - timedelta(days=5), NOW + timedelta(days=25), Decimal(180000), Decimal(90000)
        )
        with (
            mock.patch.object(
                model_studio_openapi, "get_subscription_stats_sync", return_value=stats
            ),
            LoopbackServer(
                {
                    "/compatible-mode/v1/models": (
                        200,
                        {
                            "object": "list",
                            "data": [
                                {"id": "qwen3.8-max", "object": "model"},
                                {"id": "qwen3.8-flash", "object": "model"},
                            ],
                        },
                    )
                }
            ) as server,
        ):
            result = self.probe(server)
        self.assertEqual("reachable", result.status)
        self.assertIn("90000 of 180000 Credits", result.detail)


if __name__ == "__main__":
    unittest.main()
