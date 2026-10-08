"""Tests for Pitwall capability metadata and route creation."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from pitwall.agents.broker import (
    PITWALL_SUBSCRIPTION_TOKEN_ENV,
    PITWALL_TOKEN_ENV,
    CapabilityInfo,
    PitwallError,
    ServeRefused,
    fetch_capability,
    proxy_base_url,
    resolve_pitwall_api_token,
    resolve_subscription_token,
    serve_capability,
    wait_until_served,
)
from tests.agents.http_test_support import (
    LoopbackServer,
)
from tests.agents.profiles_fixture import read_profiles, write_profiles
from tests.agents.shim_test_support import PITWALL
from tests.agents.test_broker_documents import capability_document, serve_document
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
BROKER_TOKEN_ENV = "PITWALL_API_TOKEN"  # the broker's own variable; agents never read it

CAPABILITY = capability_document(
    served_model_id="meta-models/Muse-Glimmer-30B",
    active_lease={
        "lease_id": "lease_1",
        "state": "ACTIVE",
        "expires_at": "2026-08-27T10:00:00.123456+00:00",
    },
    idle_timeout_min=20,
    last_traffic_at="2026-08-27T09:05:00Z",
    renewal_policy="activity",
    max_usd_per_hour="2.5000",
)


class PitwallClientTests(unittest.TestCase):
    def test_token_resolution_reads_only_the_named_variable(self) -> None:
        self.assertEqual(
            ("new-token", PITWALL_TOKEN_ENV),
            resolve_pitwall_api_token({PITWALL_TOKEN_ENV: "new-token"}),
        )
        self.assertEqual(
            ("", PITWALL_TOKEN_ENV),
            resolve_pitwall_api_token({BROKER_TOKEN_ENV: "broker-token"}),
        )
        self.assertEqual(
            ("broker-token", BROKER_TOKEN_ENV),
            resolve_pitwall_api_token(
                {PITWALL_TOKEN_ENV: "new-token", BROKER_TOKEN_ENV: "broker-token"},
                BROKER_TOKEN_ENV,
            ),
        )
        self.assertEqual(
            ("custom-token", "CUSTOM_PITWALL_TOKEN"),
            resolve_pitwall_api_token(
                {
                    PITWALL_TOKEN_ENV: "new-token",
                    BROKER_TOKEN_ENV: "broker-token",
                    "CUSTOM_PITWALL_TOKEN": "custom-token",
                },
                "CUSTOM_PITWALL_TOKEN",
            ),
        )
        self.assertEqual(
            ("", "CUSTOM_PITWALL_TOKEN"),
            resolve_pitwall_api_token(
                {PITWALL_TOKEN_ENV: "new-token"},
                "CUSTOM_PITWALL_TOKEN",
            ),
        )

    def test_subscription_token_is_separate_and_never_falls_back(self) -> None:
        self.assertEqual(
            ("subscription-token", PITWALL_SUBSCRIPTION_TOKEN_ENV),
            resolve_subscription_token(
                {
                    PITWALL_SUBSCRIPTION_TOKEN_ENV: "subscription-token",
                    PITWALL_TOKEN_ENV: "routing-token",
                    BROKER_TOKEN_ENV: "broker-token",
                }
            ),
        )
        self.assertEqual(
            ("", PITWALL_SUBSCRIPTION_TOKEN_ENV),
            resolve_subscription_token(
                {PITWALL_TOKEN_ENV: "routing-token", BROKER_TOKEN_ENV: "broker-token"}
            ),
        )

    def test_fetch_capability_maps_metadata_and_sends_bearer(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as server:
            info = fetch_capability(server.base_url, "llm.glimmer", "tok")
            self.assertEqual("meta-models/Muse-Glimmer-30B", info.served_model_id)
            self.assertEqual("lease_1", info.lease_id)
            self.assertEqual("2026-08-27T10:00:00Z", info.expires_at)
            self.assertEqual("Bearer tok", server.requests[0][1].get("Authorization"))

    def test_missing_and_unauthorized_capabilities_are_explained(self) -> None:
        with LoopbackServer({}) as server, self.assertRaisesRegex(PitwallError, "llm.missing"):
            fetch_capability(server.base_url, "llm.missing", "tok")
        with (
            LoopbackServer({"/v1/capabilities/llm.glimmer": (401, {"error": "nope"})}) as server,
            self.assertRaisesRegex(PitwallError, PITWALL_TOKEN_ENV),
        ):
            fetch_capability(server.base_url, "llm.glimmer", "bad")
        secret = "must-not-appear-in-errors"
        with (
            LoopbackServer({"/v1/capabilities/llm.glimmer": (403, {"error": "nope"})}) as server,
            self.assertRaises(PitwallError) as raised,
        ):
            fetch_capability(
                server.base_url,
                "llm.glimmer",
                secret,
                token_env=BROKER_TOKEN_ENV,
            )
        self.assertIn(BROKER_TOKEN_ENV, str(raised.exception))
        self.assertNotIn(secret, str(raised.exception))

    def test_proxy_base_url_normalizes_trailing_slash(self) -> None:
        self.assertEqual(
            "http://h:8000/v1/openai/llm.glimmer/v1",
            proxy_base_url("http://h:8000/", "llm.glimmer"),
        )

    def test_serve_capability_posts_translated_caps_and_returns_response(self) -> None:
        response = serve_document()
        with LoopbackServer({"/v1/serve": (200, response)}) as server:
            result = serve_capability(
                server.base_url,
                "llm.glimmer",
                "tok",
                caps={
                    "ttlMinutes": 60,
                    "idleTimeoutMinutes": 20,
                    "maxUsdPerHour": 0.5,
                    "readyTimeoutMinutes": 15,
                    "model": "must-not-be-sent",
                    "variant": "must-not-be-sent",
                    "gpu_class": "must-not-be-sent",
                },
            )

        self.assertEqual(response, result)
        self.assertEqual("/v1/serve", server.requests[0][0])
        self.assertEqual("Bearer tok", server.requests[0][1].get("Authorization"))
        self.assertEqual(
            {
                "capability": "llm.glimmer",
                "ttl_minutes": 60,
                "idle_timeout_min": 20,
                "max_usd_per_hour": "0.5",
            },
            json.loads(server.request_bodies[0]),
        )

    def test_serve_capability_maps_refusal_codes(self) -> None:
        codes = (
            "cap_exceeded",
            "price_unknown",
            "budget_exhausted",
            "kill_switch_engaged",
            "no_serve_history",
        )
        for code in codes:
            with self.subTest(code=code):
                refusal = {
                    "error": code,
                    "gpu_class": "NVIDIA L4",
                    "price_usd_per_hour": "0.89",
                    "max_usd_per_hour": "0.50",
                }
                with (
                    LoopbackServer({"/v1/serve": (422, refusal)}) as server,
                    self.assertRaises(ServeRefused) as raised,
                ):
                    serve_capability(server.base_url, "llm.glimmer", "tok", caps={})
                self.assertEqual(code, raised.exception.code)

    def test_serve_capability_maps_unrecognized_refusal_to_unknown(self) -> None:
        with (
            LoopbackServer({"/v1/serve": (422, {"error": "new_code"})}) as server,
            self.assertRaises(ServeRefused) as raised,
        ):
            serve_capability(server.base_url, "llm.glimmer", "tok", caps={})
        self.assertEqual("unknown", raised.exception.code)

    def test_wait_until_served_returns_on_second_poll(self) -> None:
        stopped = CapabilityInfo("llm.glimmer", None, None, None)
        ready = CapabilityInfo("llm.glimmer", "model-id", "lease_2", "2026-08-28T12:00:00Z")
        times = iter((0.0, 0.0, 5.0))
        sleeps: list[float] = []
        with mock.patch("pitwall.agents.broker.fetch_capability", side_effect=(stopped, ready)):
            result = wait_until_served(
                "http://pitwall",
                "llm.glimmer",
                "tok",
                deadline_seconds=10,
                poll_seconds=5,
                sleep=sleeps.append,
                now=lambda: next(times),
            )

        self.assertEqual(ready, result)
        self.assertEqual([5], sleeps)

    def test_wait_until_served_raises_at_deadline(self) -> None:
        stopped = CapabilityInfo("llm.glimmer", None, None, None)
        times = iter((0.0, 0.0, 5.0))
        sleeps: list[float] = []
        with (
            mock.patch("pitwall.agents.broker.fetch_capability", return_value=stopped),
            self.assertRaisesRegex(PitwallError, r"capability 'llm\.glimmer' not ready after 5s"),
        ):
            wait_until_served(
                "http://pitwall",
                "llm.glimmer",
                "tok",
                deadline_seconds=5,
                poll_seconds=5,
                sleep=sleeps.append,
                now=lambda: next(times),
            )

        self.assertEqual([5], sleeps)


class PitwallCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.env = {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state"),
            "PATH": os.environ.get("PATH", ""),
        }
        self.path = Path(self.env["XDG_CONFIG_HOME"]) / "pitwall" / "pitwall.toml"
        self.sync_path = (
            Path(self.env["XDG_STATE_HOME"]) / "pitwall" / "agents" / "pitwall-sync.json"
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def cli(self, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "profiles",
                *args,
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=HANG_GUARD_SECS,
        )

    def test_add_from_pitwall_records_metadata_without_token(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as server:
            env = {
                **self.env,
                "PITWALL_API_URL": server.base_url,
                PITWALL_TOKEN_ENV: "new-token",
                BROKER_TOKEN_ENV: "must-not-be-used",
            }
            result = self.cli(
                "add",
                "glimmer",
                "--from-pitwall",
                "llm.glimmer",
                env=env,
            )
            self.assertEqual(
                "Bearer new-token",
                server.requests[0][1].get("Authorization"),
            )
        self.assertEqual(0, result.returncode, result.stderr)
        raw = self.path.read_text(encoding="utf-8")
        entry = read_profiles(self.path)["models"]["glimmer"]
        self.assertEqual(
            server.base_url + "/v1/openai/llm.glimmer/v1", entry["endpoint"]["baseUrl"]
        )
        self.assertEqual(PITWALL_TOKEN_ENV, entry["endpoint"]["apiKeyEnv"])
        self.assertEqual("meta-models/Muse-Glimmer-30B", entry["model"])
        self.assertEqual("2026-08-27T10:00:00Z", entry["expiresAt"])
        self.assertEqual(
            {
                "kind": "pitwall",
                "capability": "llm.glimmer",
                "leaseId": "lease_1",
                "url": server.base_url,
            },
            entry["origin"],
        )
        self.assertNotIn("new-token", raw)
        self.assertNotIn("must-not-be-used", raw)

    def test_add_from_pitwall_ignores_the_broker_token_variable(self) -> None:
        env = {
            **self.env,
            "PITWALL_API_URL": "http://127.0.0.1:1",
            BROKER_TOKEN_ENV: "broker-token",
        }
        result = self.cli("add", "glimmer", "--from-pitwall", "llm.glimmer", env=env)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("configured Pitwall token environment variable", result.stderr)
        self.assertFalse(self.path.exists())

    def test_add_from_pitwall_records_auto_serve_caps(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as server:
            env = {**self.env, "PITWALL_API_URL": server.base_url, PITWALL_TOKEN_ENV: "tok"}
            result = self.cli(
                "add",
                "g",
                "--from-pitwall",
                "llm.glimmer",
                "--auto-serve",
                "max-usd-per-hour=2.5,ttl=60,idle=20,ready-timeout=15",
                env=env,
            )
        self.assertEqual(0, result.returncode, result.stderr)
        entry = read_profiles(self.path)["models"]["g"]
        self.assertEqual(
            {
                "maxUsdPerHour": 2.5,
                "ttlMinutes": 60,
                "idleTimeoutMinutes": 20,
                "readyTimeoutMinutes": 15,
            },
            entry["autoServe"],
        )

    def test_auto_serve_without_from_pitwall_is_a_usage_error(self) -> None:
        result = self.cli(
            "add",
            "g",
            "--model",
            "x/y",
            "--auto-serve",
            "ttl=60",
            env=self.env,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("--from-pitwall", result.stderr)

    def test_explicit_model_skips_fetch_and_expiry(self) -> None:
        with LoopbackServer({}) as server:
            env = {**self.env, "PITWALL_API_URL": server.base_url, PITWALL_TOKEN_ENV: "tok"}
            result = self.cli(
                "add", "glimmer", "--from-pitwall", "llm.glimmer", "--model", "x", env=env
            )
            self.assertEqual([], server.requests)
        self.assertEqual(0, result.returncode, result.stderr)
        entry = read_profiles(self.path)["models"]["glimmer"]
        self.assertEqual("x", entry["model"])
        self.assertNotIn("expiresAt", entry)

    def test_missing_url_token_and_conflicting_base_url_are_usage_errors(self) -> None:
        missing_url = self.cli(
            "add",
            "glimmer",
            "--from-pitwall",
            "llm.glimmer",
            env={**self.env, PITWALL_TOKEN_ENV: "tok"},
        )
        self.assertEqual(2, missing_url.returncode)
        self.assertIn("--pitwall-url", missing_url.stderr)

        missing_token = self.cli(
            "add",
            "glimmer",
            "--from-pitwall",
            "llm.glimmer",
            "--pitwall-url",
            "http://h",
            env=self.env,
        )
        self.assertEqual(2, missing_token.returncode)
        self.assertIn("configured Pitwall token environment variable", missing_token.stderr)
        self.assertNotIn("PITWALL_API_TOKEN", missing_token.stderr)

        conflict = self.cli(
            "add",
            "glimmer",
            "--from-pitwall",
            "llm.glimmer",
            "--base-url",
            "http://h/v1",
            env=self.env,
        )
        self.assertEqual(2, conflict.returncode)
        self.assertIn("mutually exclusive", conflict.stderr)

    def test_server_failure_does_not_write_routes(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (503, {"error": "down"})}) as server:
            env = {**self.env, "PITWALL_API_URL": server.base_url, PITWALL_TOKEN_ENV: "tok"}
            result = self.cli("add", "glimmer", "--from-pitwall", "llm.glimmer", env=env)
        self.assertEqual(1, result.returncode)
        self.assertFalse(self.path.exists())

    def test_readding_same_pitwall_capability_exits_four_without_changes(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as server:
            env = {**self.env, "PITWALL_API_URL": server.base_url, PITWALL_TOKEN_ENV: "tok"}
            added = self.cli("add", "glimmer", "--from-pitwall", "llm.glimmer", env=env)
            self.assertEqual(0, added.returncode, added.stderr)
            document = read_profiles(self.path)
            document["models"]["glimmer"]["args"] = ["--custom"]
            write_profiles(self.path, document)
            before = self.path.read_bytes()

            result = self.cli("add", "glimmer", "--from-pitwall", "llm.glimmer", env=env)

        self.assertEqual(4, result.returncode)
        self.assertIn(
            "already registered from Pitwall; run `pitwall agents profiles refresh glimmer`",
            result.stderr,
        )
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(
            ["--custom"],
            tomllib.loads(before.decode())["agents"]["profiles"]["models"]["glimmer"]["args"],
        )

    def test_readding_different_pitwall_capability_is_a_usage_error(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as server:
            env = {**self.env, "PITWALL_API_URL": server.base_url, PITWALL_TOKEN_ENV: "tok"}
            added = self.cli("add", "glimmer", "--from-pitwall", "llm.glimmer", env=env)
            self.assertEqual(0, added.returncode, added.stderr)
            before = self.path.read_bytes()

            result = self.cli("add", "glimmer", "--from-pitwall", "llm.other", env=env)

        self.assertEqual(2, result.returncode)
        self.assertEqual(before, self.path.read_bytes())


class PitwallRefreshTests(unittest.TestCase):
    setUp = PitwallCliTests.setUp
    tearDown = PitwallCliTests.tearDown
    cli = PitwallCliTests.cli

    def seed(self, origin_url: str, *, origin: bool = True) -> None:
        entry: dict[str, object] = {
            "model": "old-model",
            "endpoint": {
                "baseUrl": "http://old-host/v1/openai/llm.glimmer/v1",
                "apiKeyEnv": PITWALL_TOKEN_ENV,
            },
            "seat": "local",
            "args": ["--foo"],
            "expiresAt": "2020-01-01T00:00:00Z",
        }
        if origin:
            entry["origin"] = {
                "kind": "pitwall",
                "capability": "llm.glimmer",
                "leaseId": "lease_0",
                "url": origin_url,
            }
        write_profiles(self.path, {"models": {"glimmer": entry}})
        self.path.chmod(0o600)

    def test_refresh_updates_metadata_and_preserves_customization(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as server:
            self.seed(server.base_url)
            env = {**self.env, PITWALL_TOKEN_ENV: "tok"}
            result = self.cli("refresh", "glimmer", env=env)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("refreshed", result.stdout)
            as_json = self.cli("refresh", "glimmer", "--json", env=env)
            self.assertEqual(0, as_json.returncode, as_json.stderr)
            self.assertEqual("2026-08-27T10:00:00Z", json.loads(as_json.stdout)["expiresAt"])
        raw = self.path.read_text(encoding="utf-8")
        entry = read_profiles(self.path)["models"]["glimmer"]
        self.assertEqual("meta-models/Muse-Glimmer-30B", entry["model"])
        self.assertEqual("2026-08-27T10:00:00Z", entry["expiresAt"])
        self.assertEqual("lease_1", entry["origin"]["leaseId"])
        self.assertEqual("active", entry["origin"]["state"])
        self.assertEqual(server.base_url, entry["origin"]["url"])
        self.assertEqual(
            server.base_url + "/v1/openai/llm.glimmer/v1", entry["endpoint"]["baseUrl"]
        )
        self.assertEqual(PITWALL_TOKEN_ENV, entry["endpoint"]["apiKeyEnv"])
        self.assertEqual("local", entry["seat"])
        self.assertEqual(["--foo"], entry["args"])
        self.assertNotIn("tok", raw)

    def test_refresh_uses_the_agents_token_and_never_the_broker_token(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as server:
            self.seed(server.base_url)
            before = read_profiles(self.path)
            env = {
                **self.env,
                PITWALL_TOKEN_ENV: "new-token",
                BROKER_TOKEN_ENV: "must-not-be-used",
            }
            result = self.cli("refresh", "glimmer", env=env)
            self.assertEqual(
                "Bearer new-token",
                server.requests[0][1].get("Authorization"),
            )
        self.assertEqual(0, result.returncode, result.stderr)
        after = read_profiles(self.path)
        self.assertEqual(
            before["models"]["glimmer"]["endpoint"]["apiKeyEnv"],
            after["models"]["glimmer"]["endpoint"]["apiKeyEnv"],
        )
        self.assertEqual(PITWALL_TOKEN_ENV, after["models"]["glimmer"]["endpoint"]["apiKeyEnv"])
        self.assertNotIn("new-token", json.dumps(after))
        self.assertNotIn("must-not-be-used", json.dumps(after))

    def test_refresh_without_active_lease_marks_route_stopped(self) -> None:
        stopped = capability_document(
            served_model_id="meta-models/Muse-Glimmer-30B", active_lease=None
        )
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, stopped)}) as server:
            self.seed(server.base_url)
            env = {**self.env, PITWALL_TOKEN_ENV: "tok"}
            result = self.cli("refresh", "glimmer", env=env)
        self.assertEqual(0, result.returncode, result.stderr)
        entry = read_profiles(self.path)["models"]["glimmer"]
        self.assertEqual("stopped", entry["origin"]["state"])
        self.assertNotIn("expiresAt", entry)

    def test_refresh_all_pitwall_continues_after_per_route_error(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as server:
            self.seed(server.base_url)
            document = read_profiles(self.path)
            missing = json.loads(json.dumps(document["models"]["glimmer"]))
            missing["origin"]["capability"] = "llm.missing"
            missing["model"] = "missing-old-model"
            document["models"]["missing"] = missing
            write_profiles(self.path, document)
            env = {**self.env, PITWALL_TOKEN_ENV: "tok"}

            result = self.cli("refresh", "--all-pitwall", env=env)

        self.assertEqual(1, result.returncode)
        self.assertIn("refreshed 'glimmer'", result.stdout)
        self.assertIn("llm.missing", result.stderr)
        routes = read_profiles(self.path)["models"]
        self.assertEqual("active", routes["glimmer"]["origin"]["state"])
        self.assertEqual("missing-old-model", routes["missing"]["model"])
        sync = json.loads(self.sync_path.read_text(encoding="utf-8"))
        self.assertEqual("refresh", sync["routes"]["glimmer"]["source"])
        self.assertRegex(sync["routes"]["glimmer"]["lastUpdatedAt"], r"^\d{4}-\d{2}-\d{2}T.*Z$")
        self.assertNotIn("missing", sync["routes"])
        self.assertEqual(0o600, self.sync_path.stat().st_mode & 0o777)

    def test_refresh_pitwall_url_override_rewrites_endpoint_and_origin(self) -> None:
        with LoopbackServer({"/v1/capabilities/llm.glimmer": (200, CAPABILITY)}) as new_home:
            self.seed("http://old-pitwall:1")
            env = {**self.env, PITWALL_TOKEN_ENV: "tok"}
            result = self.cli("refresh", "glimmer", "--pitwall-url", new_home.base_url, env=env)
            self.assertEqual(0, result.returncode, result.stderr)
        entry = read_profiles(self.path)["models"]["glimmer"]
        self.assertEqual(new_home.base_url, entry["origin"]["url"])
        self.assertEqual(
            new_home.base_url + "/v1/openai/llm.glimmer/v1", entry["endpoint"]["baseUrl"]
        )

    def test_refresh_errors_leave_the_file_alone(self) -> None:
        env = {**self.env, PITWALL_TOKEN_ENV: "tok"}
        missing = self.cli("refresh", "nosuch", env=env)
        self.assertEqual(1, missing.returncode)
        self.assertIn("no route named", missing.stderr)
        with LoopbackServer({}) as server:
            self.seed(server.base_url, origin=False)
            not_pitwall = self.cli("refresh", "glimmer", env=env)
            self.assertEqual(2, not_pitwall.returncode)
            self.assertIn("Pitwall origin", not_pitwall.stderr)
            self.seed(server.base_url)
            before = self.path.read_text(encoding="utf-8")
            no_token = self.cli("refresh", "glimmer", env=self.env)
            self.assertEqual(2, no_token.returncode)
            self.assertIn("configured Pitwall token environment variable", no_token.stderr)
            self.assertNotIn("PITWALL_API_TOKEN", no_token.stderr)
            gone = self.cli("refresh", "glimmer", env=env)  # server has no capability -> 404
            self.assertEqual(1, gone.returncode)
            self.assertIn("llm.glimmer", gone.stderr)
            self.assertEqual(before, self.path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
