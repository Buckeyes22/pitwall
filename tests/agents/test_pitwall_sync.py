"""Tests for Pitwall webhook automation commands."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib import error, request

from tests.agents.profiles_fixture import read_profiles, write_profiles
from tests.agents.shim_test_support import PITWALL
from tests.agents.test_broker_documents import subscription_document
from tests.hang_guard import HANG_GUARD_SECS

ROOT = Path(__file__).resolve().parents[2]
BROKER_TOKEN_ENV = "PITWALL_API_TOKEN"  # the broker's own variable; agents never read it

pitwall_sync = importlib.import_module("pitwall.agents.broker")
pitwall_module = importlib.import_module("pitwall.agents.broker")
registry_module = importlib.import_module("pitwall.agents.registry")
routes_module = importlib.import_module("pitwall.agents.profiles")
http_support = importlib.import_module("tests.agents.http_test_support")

LoopbackServer = http_support.LoopbackServer
apply_event = pitwall_sync.apply_event
verify_signature = pitwall_sync.verify_signature

NOW = 1_787_930_900
SECRET = "webhook-test-secret"


def envelope(event: str, delivery_id: str, data: dict[str, object]) -> dict[str, object]:
    return {
        "version": "1",
        "event": event,
        "delivery_id": delivery_id,
        "occurred_at": "2026-08-28T12:00:00Z",
        "capability": "llm.glimmer",
        "data": data,
    }


def signature(body: bytes, timestamp: int = NOW) -> str:
    digest = hmac.new(SECRET.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


class SignatureTests(unittest.TestCase):
    def test_pinned_envelope_signature_accepts_and_rejects_tampering_and_age(self) -> None:
        event = envelope(
            "lease.ready",
            "delivery-1",
            {
                "capability": "llm.glimmer",
                "lease_id": "lease-2",
                "served_model_id": "meta-models/Muse-Glimmer-30B",
                "variant": "fp8",
                "expires_at": "2026-08-28T13:00:00Z",
                "proxy_base_url": "http://127.0.0.1:8000/v1/openai/llm.glimmer/v1",
                "idle_timeout_min": 20,
                "created": True,
            },
        )
        self.assertNotIn("workload_id", event)
        self.assertNotIn("consumer", event)
        body = json.dumps(event, separators=(",", ":")).encode()

        self.assertTrue(verify_signature(body, signature(body), SECRET, now=lambda: NOW))
        self.assertFalse(verify_signature(body + b" ", signature(body), SECRET, now=lambda: NOW))
        self.assertFalse(
            verify_signature(body, signature(body, NOW - 301), SECRET, now=lambda: NOW)
        )
        self.assertFalse(verify_signature(body, "bad", SECRET, now=lambda: NOW))


class SecretPrecedenceTests(unittest.TestCase):
    def test_explicit_webhook_indirection_is_first_and_fail_closed(self) -> None:
        env = {
            pitwall_sync.PITWALL_WEBHOOK_SECRET_ENV: "EXPLICIT_WEBHOOK_SECRET",
            "EXPLICIT_WEBHOOK_SECRET": "explicit-secret",
            pitwall_sync.DEFAULT_WEBHOOK_SECRET_ENV: "new-secret",
            pitwall_sync.LEGACY_WEBHOOK_SECRET_ENV: "legacy-secret",
        }
        self.assertEqual(
            ("explicit-secret", "EXPLICIT_WEBHOOK_SECRET"),
            pitwall_sync.resolve_webhook_secret(env),
        )
        del env["EXPLICIT_WEBHOOK_SECRET"]
        self.assertEqual(
            ("", "EXPLICIT_WEBHOOK_SECRET"),
            pitwall_sync.resolve_webhook_secret(env),
        )

    def test_default_webhook_secret_precedes_legacy_and_missing_names_new_default(self) -> None:
        self.assertEqual(
            ("new-secret", pitwall_sync.DEFAULT_WEBHOOK_SECRET_ENV),
            pitwall_sync.resolve_webhook_secret(
                {
                    pitwall_sync.DEFAULT_WEBHOOK_SECRET_ENV: "new-secret",
                    pitwall_sync.LEGACY_WEBHOOK_SECRET_ENV: "legacy-secret",
                }
            ),
        )
        self.assertEqual(
            ("legacy-secret", pitwall_sync.LEGACY_WEBHOOK_SECRET_ENV),
            pitwall_sync.resolve_webhook_secret(
                {pitwall_sync.LEGACY_WEBHOOK_SECRET_ENV: "legacy-secret"}
            ),
        )
        self.assertEqual(
            ("", pitwall_sync.DEFAULT_WEBHOOK_SECRET_ENV),
            pitwall_sync.resolve_webhook_secret({}),
        )


class ApplyEventTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.env = {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state"),
            "PITWALL_API_URL": "http://127.0.0.1:8000",
        }
        self.path = root / "config" / "pitwall" / "pitwall.toml"
        self.sync_path = root / "state" / "pitwall" / "agents" / "pitwall-sync.json"
        self.registry = registry_module.load_registry()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def seed(self, *, auto_register: bool = False) -> None:
        document: dict[str, object] = {
            "schemaVersion": 1,
            "models": {
                "glimmer": {
                    "model": "old/model",
                    "endpoint": {
                        "baseUrl": "http://old/v1",
                        "apiKeyEnv": "PITWALL_API_TOKEN",
                    },
                    "args": ["--keep"],
                    "expiresAt": "2026-08-28T12:30:00Z",
                    "origin": {
                        "kind": "pitwall",
                        "capability": "llm.glimmer",
                        "leaseId": "lease-1",
                        "url": "http://127.0.0.1:8000",
                        "state": "active",
                    },
                }
            },
        }
        if auto_register:
            document["models"] = {}
            document["pitwall"] = {"autoRegister": {"llm.glimmer": "glimmer"}}
        write_profiles(self.path, document)

    def read_entry(self) -> dict[str, object]:
        return read_profiles(self.path)["models"]["glimmer"]

    def test_ready_refreshes_matching_route_using_top_level_capability(self) -> None:
        self.seed()
        event = envelope(
            "lease.ready",
            "ready-1",
            {
                "capability": "llm.different",
                "lease_id": "lease-2",
                "served_model_id": "new/model",
                "expires_at": "2026-08-28T14:00:00Z",
                "proxy_base_url": "http://127.0.0.1:8000/proxy/glimmer/v1",
                "variant": "fp8",
                "idle_timeout_min": 20,
                "created": True,
            },
        )

        self.assertEqual("applied:glimmer", apply_event(event, self.env, self.registry))
        entry = self.read_entry()
        self.assertEqual("new/model", entry["model"])
        self.assertEqual("2026-08-28T14:00:00Z", entry["expiresAt"])
        self.assertEqual("http://127.0.0.1:8000/proxy/glimmer/v1", entry["endpoint"]["baseUrl"])
        self.assertEqual("lease-2", entry["origin"]["leaseId"])
        self.assertEqual("active", entry["origin"]["state"])
        self.assertEqual(["--keep"], entry["args"])
        sync = json.loads(self.sync_path.read_text(encoding="utf-8"))
        self.assertEqual("receiver", sync["routes"]["glimmer"]["source"])
        self.assertIn("ready-1", sync["deliveryIds"])

    def test_renew_stop_and_expiring_transitions(self) -> None:
        self.seed()
        renewed = envelope(
            "lease.renewed",
            "renewed-1",
            {"lease_id": "lease-1", "expires_at": "2026-08-28T15:00:00Z", "renewed_by": "activity"},
        )
        self.assertEqual("applied:glimmer", apply_event(renewed, self.env, self.registry))
        self.assertEqual("2026-08-28T15:00:00Z", self.read_entry()["expiresAt"])

        expiring = envelope(
            "lease.expiring",
            "expiring-1",
            {
                "capability": "llm.glimmer",
                "lease_id": "lease-1",
                "expires_at": "2026-08-28T15:00:00.123456+00:00",
                "minutes_left": 15,
            },
        )
        before = self.path.read_bytes()
        self.assertEqual("applied:glimmer", apply_event(expiring, self.env, self.registry))
        self.assertEqual(before, self.path.read_bytes())

        for event_name in ("lease.stopped", "lease.terminated"):
            with self.subTest(event=event_name):
                stopped = envelope(
                    event_name, event_name, {"lease_id": "lease-1", "reason": "idle"}
                )
                self.assertEqual("applied:glimmer", apply_event(stopped, self.env, self.registry))
                entry = self.read_entry()
                self.assertEqual("stopped", entry["origin"]["state"])
                self.assertNotIn("expiresAt", entry)

    def test_auto_register_creates_mapped_route_and_validates_mapping(self) -> None:
        self.seed(auto_register=True)
        event = envelope(
            "lease.ready",
            "auto-1",
            {
                "lease_id": "lease-2",
                "served_model_id": "new/model",
                "expires_at": "2026-08-28T14:00:00Z",
                "proxy_base_url": "http://127.0.0.1:8000/proxy/glimmer/v1",
                "variant": "fp8",
                "idle_timeout_min": 20,
                "created": True,
            },
        )

        self.assertEqual("applied:glimmer", apply_event(event, self.env, self.registry))
        config = routes_module.load_profiles(self.env, registry=self.registry)
        entry = config["models"]["glimmer"]
        self.assertEqual("new/model", entry["model"])
        self.assertEqual(pitwall_module.PITWALL_TOKEN_ENV, entry["endpoint"]["apiKeyEnv"])
        self.assertEqual("llm.glimmer", entry["origin"]["capability"])
        self.assertEqual("active", entry["origin"]["state"])

        invalid = read_profiles(self.path)
        invalid["pitwall"]["autoRegister"] = {"llm.glimmer": "bad route name"}
        with self.assertRaisesRegex(routes_module.ProfilesError, "autoRegister"):
            routes_module.validate_profiles(invalid, registry=self.registry)

    def test_delivery_id_is_rejected_from_memory_and_persistent_sidecar(self) -> None:
        self.seed()
        event = envelope(
            "lease.expiring",
            "duplicate-1",
            {
                "capability": "llm.glimmer",
                "lease_id": "lease-1",
                "expires_at": "2026-08-28T15:00:00.123456+00:00",
                "minutes_left": 15,
            },
        )
        self.assertEqual("applied:glimmer", apply_event(event, self.env, self.registry))
        self.assertEqual("replayed", apply_event(event, self.env, self.registry))
        pitwall_sync._RECENT_DELIVERIES.clear()
        pitwall_sync._RECENT_DELIVERY_SET.clear()
        self.assertEqual("replayed", apply_event(event, self.env, self.registry))

    def test_persistent_delivery_cache_keeps_last_one_hundred_ids(self) -> None:
        self.seed()
        for index in range(101):
            event = envelope("future.event", f"bounded-{index}", {})
            self.assertEqual("ignored", apply_event(event, self.env, self.registry))
        ids = json.loads(self.sync_path.read_text(encoding="utf-8"))["deliveryIds"]
        self.assertEqual(100, len(ids))
        self.assertNotIn("bounded-0", ids)
        self.assertEqual("bounded-100", ids[-1])


class PitwallCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        temporary_dir = root / "tmp"
        temporary_dir.mkdir()
        self.env = {
            "HOME": str(root / "home"),
            "PYTHONPYCACHEPREFIX": str(root / "pycache"),
            "TMPDIR": str(temporary_dir),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_STATE_HOME": str(root / "state"),
            "PATH": os.environ.get("PATH", ""),
        }

    def tearDown(self) -> None:
        self.directory.cleanup()

    def cli(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                str(PITWALL),
                "agents",
                "broker",
                *args,
            ],
            env=env or self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=HANG_GUARD_SECS,
        )

    def test_receiver_refuses_non_loopback_host(self) -> None:
        result = self.cli("receiver", "--host", "0.0.0.0")
        self.assertEqual(2, result.returncode)
        self.assertIn("loopback", result.stderr)

    def test_receiver_health_endpoint_and_replay_rejection(self) -> None:
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        listener.close()
        env = {**self.env, "PITWALL_WEBHOOK_SECRET": SECRET}
        process = subprocess.Popen(
            [
                str(PITWALL),
                "agents",
                "broker",
                "receiver",
                "--port",
                str(port),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            opener = request.build_opener(request.ProxyHandler({}))
            health = f"http://127.0.0.1:{port}/health"
            # macOS 15+ can stall HTTPServer's getfqdn() call for about 35s
            # between bind() and listen(); retain a bounded readiness proof.
            deadline = time.monotonic() + HANG_GUARD_SECS
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    stderr = process.communicate(timeout=HANG_GUARD_SECS)[1]
                    self.fail(f"receiver exited before becoming healthy: {stderr.strip()}")
                try:
                    with opener.open(health, timeout=0.5) as response:
                        self.assertEqual(200, response.status)
                        break
                except (error.URLError, TimeoutError) as exc:
                    last_error = exc
                    time.sleep(0.05)
            else:
                self.fail(f"receiver did not become healthy: {last_error}")

            body = json.dumps(
                envelope(
                    "lease.expiring",
                    "receiver-duplicate",
                    {
                        "capability": "llm.glimmer",
                        "lease_id": "lease-1",
                        "expires_at": "2026-08-28T15:00:00.123456+00:00",
                        "minutes_left": 15,
                    },
                ),
                separators=(",", ":"),
            ).encode()
            delivery = request.Request(
                f"http://127.0.0.1:{port}/pitwall",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Pitwall-Signature": signature(body, int(time.time())),
                },
                method="POST",
            )
            with opener.open(delivery, timeout=HANG_GUARD_SECS) as response:
                self.assertEqual(202, response.status)
            with self.assertRaises(error.HTTPError) as replayed:
                opener.open(delivery, timeout=HANG_GUARD_SECS)
            self.assertEqual(409, replayed.exception.code)
            replayed.exception.close()
        finally:
            process.terminate()
            process.wait(timeout=HANG_GUARD_SECS)
            if process.stderr is not None:
                process.stderr.close()

    def test_subscribe_posts_event_types_and_surfaces_loopback_remedy(self) -> None:
        refusal = {"error": "webhook_target_not_allowed"}
        with LoopbackServer({"/v1/webhook-subscriptions": (422, refusal)}) as server:
            env = {
                **self.env,
                "PITWALL_API_URL": server.base_url,
                pitwall_module.PITWALL_SUBSCRIPTION_TOKEN_ENV: "subscription-token",
                pitwall_module.PITWALL_TOKEN_ENV: "routing-token-must-not-be-used",
                BROKER_TOKEN_ENV: "legacy-token-must-not-be-used",
            }
            result = self.cli(
                "subscribe", "--receiver-url", "http://127.0.0.1:8765/pitwall", env=env
            )
        self.assertEqual(1, result.returncode)
        self.assertIn("PITWALL_WEBHOOK_LOOPBACK_ALLOWLIST=127.0.0.1:8765", result.stderr)
        self.assertIn("webhook:admin", result.stderr)
        self.assertEqual(
            "Bearer subscription-token",
            server.requests[0][1].get("Authorization"),
        )
        self.assertEqual(
            ["lease.ready", "lease.renewed", "lease.stopped", "lease.expiring"],
            json.loads(server.request_bodies[0])["event_types"],
        )

    def test_subscribe_writes_protected_handoff_and_install_writes_no_secret(self) -> None:
        response = {**subscription_document(), "signing_secret": "generated-secret"}
        with LoopbackServer({"/v1/webhook-subscriptions": (201, response)}) as server:
            env = {
                **self.env,
                "PITWALL_API_URL": server.base_url,
                pitwall_module.PITWALL_SUBSCRIPTION_TOKEN_ENV: "tok",
                "PITWALL_WEBHOOK_SECRET_ENV": "MY_WEBHOOK_SECRET",
            }
            subscribed = self.cli(
                "subscribe", "--receiver-url", "http://127.0.0.1:8765/pitwall", env=env
            )
        self.assertEqual(0, subscribed.returncode, subscribed.stderr)
        self.assertNotIn("generated-secret", subscribed.stdout)
        credential_path = Path(
            subscribed.stdout.split("one-time credential file ", 1)[1].split(" (mode 0600)", 1)[0]
        )
        self.assertEqual(0o600, credential_path.stat().st_mode & 0o777)
        self.assertEqual(
            "export MY_WEBHOOK_SECRET=generated-secret\n",
            credential_path.read_text(encoding="utf-8"),
        )
        self.assertNotIn("deprecated", subscribed.stderr)
        self.assertNotIn("tok", subscribed.stderr)
        self.assertEqual(
            "http://127.0.0.1:8765/pitwall",
            json.loads(server.request_bodies[0])["webhook_url"],
        )

        installed = self.cli("receiver", "--install")
        self.assertEqual(0, installed.returncode, installed.stderr)
        unit = Path(self.env["HOME"]) / ".config/systemd/user/pitwall-agents-broker.service"
        if sys.platform == "darwin":
            self.assertFalse(unit.exists())
            self.assertIn("Install a LaunchAgent", installed.stdout)
            self.assertNotIn("generated-secret", installed.stdout)
            self.assertIn("must not be placed in the plist", installed.stdout)
            command_path = Path(
                installed.stdout.split("protected command file ", 1)[1].split(" (mode 0600)", 1)[0]
            )
            self.assertEqual(0o600, command_path.stat().st_mode & 0o777)
            self.assertIn(
                "broker receiver",
                command_path.read_text(encoding="utf-8"),
            )
        else:
            content = unit.read_text(encoding="utf-8")
            self.assertIn("broker receiver", content)
            self.assertNotIn("generated-secret", content)
            self.assertIn("run `systemctl --user enable", installed.stdout)

    def test_subscribe_default_handoff_uses_new_receiver_secret_name(self) -> None:
        response = {**subscription_document(), "signing_secret": "generated-secret"}
        with LoopbackServer({"/v1/webhook-subscriptions": (201, response)}) as server:
            env = {
                **self.env,
                "PITWALL_API_URL": server.base_url,
                pitwall_module.PITWALL_SUBSCRIPTION_TOKEN_ENV: "subscription-token",
            }
            subscribed = self.cli(
                "subscribe", "--receiver-url", "http://127.0.0.1:8765/pitwall", env=env
            )
        self.assertEqual(0, subscribed.returncode, subscribed.stderr)
        self.assertNotIn("generated-secret", subscribed.stdout)
        credential_path = Path(
            subscribed.stdout.split("one-time credential file ", 1)[1].split(" (mode 0600)", 1)[0]
        )
        self.assertEqual(0o600, credential_path.stat().st_mode & 0o777)
        self.assertEqual(
            f"export {pitwall_sync.DEFAULT_WEBHOOK_SECRET_ENV}=generated-secret\n",
            credential_path.read_text(encoding="utf-8"),
        )

    def test_subscribe_does_not_reuse_normal_routing_token(self) -> None:
        env = {
            **self.env,
            "PITWALL_API_URL": "http://127.0.0.1:9",
            pitwall_module.PITWALL_TOKEN_ENV: "routing-token-must-not-be-used",
        }
        subscribed = self.cli(
            "subscribe", "--receiver-url", "http://127.0.0.1:8765/pitwall", env=env
        )
        self.assertEqual(2, subscribed.returncode)
        self.assertIn(pitwall_module.PITWALL_SUBSCRIPTION_TOKEN_ENV, subscribed.stderr)
        self.assertNotIn("routing-token-must-not-be-used", subscribed.stderr)
        self.assertEqual("", subscribed.stdout)

    def test_interval_parser(self) -> None:
        self.assertEqual(300.0, pitwall_sync.parse_interval("5m"))
        with self.assertRaisesRegex(ValueError, "interval"):
            pitwall_sync.parse_interval("nope")


if __name__ == "__main__":
    unittest.main()
