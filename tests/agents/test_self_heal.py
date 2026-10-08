"""Dispatch-time self-heal tests for Pitwall-origin routes."""

from __future__ import annotations

import json
import subprocess
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tests.agents.profiles_fixture import read_profiles
from tests.agents.shim_test_support import ROUTE_SHIM, ShimSandbox
from tests.agents.test_broker_documents import capability_document, serve_document
from tests.hang_guard import HANG_GUARD_SECS

MODEL = "meta-models/Muse-Glimmer-30B"


class PitwallFixture:
    def __init__(
        self,
        *,
        live: bool = False,
        refusal: str | None = None,
        unleased: bool = False,
        model_present: bool = True,
    ) -> None:
        self.live = live
        self.unleased = unleased
        self.model_present = model_present
        self.refusal = refusal
        self.requests: list[tuple[str, str, bytes]] = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802  # reason: http.server API
                fixture.requests.append(("GET", self.path, b""))
                if self.path == "/v1/capabilities/llm.glimmer":
                    lease = (
                        {"lease_id": "lease_2", "expires_at": "2099-08-28T12:00:00Z"}
                        if fixture.live
                        else None
                    )
                    self._reply(
                        200,
                        capability_document(
                            served_model_id=MODEL if fixture.live or fixture.unleased else None,
                            active_lease=lease,
                        ),
                    )
                elif self.path == "/v1/openai/llm.glimmer/v1/models":
                    self._reply(200, {"data": [{"id": MODEL}] if fixture.model_present else []})
                else:
                    self._reply(404, {"error": "not_found"})

            def do_POST(self) -> None:  # noqa: N802  # reason: http.server API
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fixture.requests.append(("POST", self.path, body))
                if self.path != "/v1/serve":
                    self._reply(404, {"error": "not_found"})
                elif fixture.refusal:
                    self._reply(422, {"error": fixture.refusal})
                else:
                    fixture.live = True
                    self._reply(200, serve_document())

            def _reply(self, status: int, value: Any) -> None:
                payload = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_: object) -> None:
                return None

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        port = self.server.server_address[1]
        return f"http://127.0.0.1:{port}"

    def __enter__(self) -> PitwallFixture:
        self.thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.server.shutdown()
        self.server.server_close()


class SelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sandbox = ShimSandbox()
        self.sandbox.install_harness("qwen")
        self.prompt = self.sandbox.prompt()

    def tearDown(self) -> None:
        self.sandbox.cleanup()

    def route(
        self, url: str, *, state: str, expires: str | None = None, auto: bool = False
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "model": MODEL,
            "endpoint": {
                "baseUrl": url + "/v1/openai/llm.glimmer/v1",
                "apiKeyEnv": "PITWALL_API_TOKEN",
            },
            "origin": {
                "kind": "pitwall",
                "capability": "llm.glimmer",
                "leaseId": None,
                "url": url,
                "state": state,
            },
        }
        if expires:
            entry["expiresAt"] = expires
        if auto:
            entry["autoServe"] = {
                "ttlMinutes": 60,
                "idleTimeoutMinutes": 20,
                "maxUsdPerHour": 0.5,
                "readyTimeoutMinutes": 1,
            }
        return {"schemaVersion": 1, "models": {"glimmer": entry}}

    def run_route(self, **extra: str) -> subprocess.CompletedProcess[bytes]:
        env = self.sandbox.environment(PITWALL_API_TOKEN="tok", **extra)
        return self.sandbox.run_route(["glimmer", str(self.prompt)], env=env)

    def test_expired_route_with_live_capability_refreshes_and_dispatches(self) -> None:
        with PitwallFixture(live=True) as pitwall:
            path = self.sandbox.write_routes(
                self.route(pitwall.url, state="active", expires="2020-01-01T00:00:00Z")
            )
            result = self.run_route()
        self.assertEqual(0, result.returncode, result.stderr)
        entry = read_profiles(path)["models"]["glimmer"]
        self.assertEqual(
            ("active", "lease_2", "2099-08-28T12:00:00Z"),
            (entry["origin"]["state"], entry["origin"].get("leaseId"), entry["expiresAt"]),
        )
        self.assertEqual(["GET"], [method for method, _, _ in pitwall.requests])

    def test_stopped_auto_serve_posts_caps_dispatches_and_marks_receipt_revived(self) -> None:
        with PitwallFixture() as pitwall:
            self.sandbox.write_routes(self.route(pitwall.url, state="stopped", auto=True))
            result = self.run_route(SHIM_RESULT="1")
        self.assertEqual(0, result.returncode, result.stderr)
        posts = [
            json.loads(body)
            for method, path, body in pitwall.requests
            if method == "POST" and path == "/v1/serve"
        ]
        self.assertEqual(
            [
                {
                    "capability": "llm.glimmer",
                    "ttl_minutes": 60,
                    "idle_timeout_min": 20,
                    "max_usd_per_hour": "0.5",
                }
            ],
            posts,
        )
        receipt = json.loads(
            next(
                line.removeprefix("SHIM-RESULT ")
                for line in result.stdout.decode().splitlines()
                if line.startswith("SHIM-RESULT ")
            )
        )
        self.assertTrue(receipt["revived"])

    def test_stopped_auto_serve_refusal_exits_78(self) -> None:
        with PitwallFixture(refusal="cap_exceeded") as pitwall:
            self.sandbox.write_routes(self.route(pitwall.url, state="stopped", auto=True))
            result = self.run_route()
        self.assertEqual(78, result.returncode)
        self.assertTrue(
            result.stderr.decode().rstrip().endswith("(not revived: cap_exceeded)"), result.stderr
        )

    def test_stopped_without_auto_serve_exits_78_without_serve(self) -> None:
        with PitwallFixture() as pitwall:
            self.sandbox.write_routes(self.route(pitwall.url, state="stopped"))
            result = self.run_route()
        self.assertEqual(78, result.returncode)
        self.assertFalse(any(method == "POST" for method, _, _ in pitwall.requests))

    def test_concurrent_dispatches_issue_one_serve(self) -> None:
        with PitwallFixture() as pitwall:
            self.sandbox.write_routes(self.route(pitwall.url, state="stopped", auto=True))
            env = self.sandbox.environment(PITWALL_API_TOKEN="tok")
            command = ["/bin/bash", str(ROUTE_SHIM), "glimmer", str(self.prompt)]
            first = self.sandbox.popen(
                command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            second = self.sandbox.popen(
                command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            first_result = first.communicate(timeout=HANG_GUARD_SECS)
            second_result = second.communicate(timeout=HANG_GUARD_SECS)
        self.assertEqual(
            (0, 0), (first.returncode, second.returncode), first_result[1] + second_result[1]
        )
        self.assertEqual(
            1, sum(method == "POST" and path == "/v1/serve" for method, path, _ in pitwall.requests)
        )

    def test_self_heal_honors_the_route_api_key_env(self) -> None:
        with PitwallFixture(live=True) as pitwall:
            data = self.route(pitwall.url, state="active", expires="2020-01-01T00:00:00Z")
            data["models"]["glimmer"]["endpoint"]["apiKeyEnv"] = "MY_PW_TOKEN"
            self.sandbox.write_routes(data)
            env = self.sandbox.environment(MY_PW_TOKEN="tok")
            result = self.sandbox.run_route(["glimmer", str(self.prompt)], env=env)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unleased_capability_dispatches_after_model_probe_on_every_attempt(self) -> None:
        with PitwallFixture(unleased=True) as pitwall:
            path = self.sandbox.write_routes(self.route(pitwall.url, state="unknown"))
            for _ in range(2):
                result = self.run_route()
                self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(2, sum(path.endswith("/models") for _, path, _ in pitwall.requests))
            self.assertFalse(any(method == "POST" for method, _, _ in pitwall.requests))
            self.assertIsNone(read_profiles(path)["models"]["glimmer"]["origin"].get("leaseId"))

    def test_unleased_capability_accepts_explicit_model_when_metadata_omits_it(self) -> None:
        with PitwallFixture() as pitwall:
            self.sandbox.write_routes(self.route(pitwall.url, state="unknown"))
            result = self.run_route()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(any(path.endswith("/models") for _, path, _ in pitwall.requests))
        self.assertFalse(any(method == "POST" for method, _, _ in pitwall.requests))

    def test_unleased_capability_without_expected_model_refuses_without_spending(self) -> None:
        with PitwallFixture(unleased=True, model_present=False) as pitwall:
            self.sandbox.write_routes(self.route(pitwall.url, state="unknown"))
            result = self.run_route()
        self.assertEqual(78, result.returncode)
        self.assertTrue(any(path.endswith("/models") for _, path, _ in pitwall.requests))
        self.assertFalse(any(method == "POST" for method, _, _ in pitwall.requests))

    def test_lost_lease_is_not_reclassified_unleased_on_repeated_dispatch(self) -> None:
        with PitwallFixture(unleased=True) as pitwall:
            data = self.route(pitwall.url, state="active", expires="2020-01-01T00:00:00Z")
            data["models"]["glimmer"]["origin"]["leaseId"] = "old-lease"
            path = self.sandbox.write_routes(data)
            for _ in range(2):
                result = self.run_route()
                self.assertEqual(78, result.returncode)
                self.assertEqual(
                    "stopped", read_profiles(path)["models"]["glimmer"]["origin"]["state"]
                )
        self.assertFalse(
            any(method == "POST" or url.endswith("/models") for method, url, _ in pitwall.requests)
        )

    def test_healthy_active_route_does_no_pitwall_io(self) -> None:
        with PitwallFixture(live=True) as pitwall:
            self.sandbox.write_routes(
                self.route(pitwall.url, state="active", expires="2099-08-28T12:00:00Z")
            )
            result = self.run_route()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], pitwall.requests)


if __name__ == "__main__":
    unittest.main()
