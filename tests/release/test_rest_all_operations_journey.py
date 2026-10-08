"""J35: every OpenAPI operation, through a real API server, with its scope boundary.

One ``pitwall-api`` process runs against a freshly seeded disposable database, with one
bearer token per API scope and the admin secret. Every operation in the live OpenAPI
document has a fixture. Before the operation runs, an anonymous request and a request
carrying a token without the operation's scope are both refused (401 and 403), so a
refusal never has a side effect. Then the operation runs with its own scope. It either
succeeds with the pinned status and top-level keys, or, when it needs a live provider or
refuses by design, returns the pinned status and typed code. Operations that act on a
record get a fresh one from a named setup step. Outbound HTTP goes to a dead loopback
proxy; the OpenAI passthrough reaches a local fake upstream.
"""

from __future__ import annotations

import base64
import json
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.hang_guard import HANG_GUARD_SECS

pytestmark = [pytest.mark.release, pytest.mark.integration, pytest.mark.journey_harness]

FIXTURES: dict[str, dict[str, Any]] = json.loads(
    (Path(__file__).parent / "rest_operation_fixtures.json").read_text()
)
ADMIN_SECRET = "journey-admin-secret"
TOKENS = {
    "read": "journey-read-token",
    "spend": "journey-spend-token",
    "lease:mutate": "journey-lease-token",
    "webhook:admin": "journey-webhook-token",
    "server:admin": "journey-admin-token",
}
# No route disengages the kill switch, so engaging it is the last operation.
_LAST = ("POST /v1/admin/kill-switch",)


def operations(spec: dict[str, Any]) -> list[str]:
    return sorted(
        f"{method.upper()} {path}"
        for path, item in spec["paths"].items()
        for method in item
        if method in {"get", "post", "put", "patch", "delete"}
    )


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakeUpstream:
    """An OpenAI-compatible upstream that answers every method on every path."""

    def __init__(self) -> None:
        upstream = self
        self.requests: list[tuple[str, str]] = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return None

            def _answer(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                upstream.requests.append((self.command, self.path))
                payload = json.dumps(
                    {"object": "list", "data": [], "method": self.command, "path": self.path}
                ).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _answer

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@dataclass
class Api:
    client: httpx.Client
    env: dict[str, str]
    upstream: FakeUpstream

    def admin(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {TOKENS['server:admin']}",
            "X-Pitwall-Secret": ADMIN_SECRET,
        }
        response = self.client.request(method, path, headers=headers, **kwargs)
        assert response.status_code < 300, (method, path, response.text[:300])
        return response


_SEED = """
import asyncio, datetime as dt, sys
from pitwall.core.enums import LeaseRenewalPolicy, LeaseState, WorkloadState
from pitwall.core.models import Lease, LeaseEndpoints, LeaseReadiness, Workload
from pitwall.db import close_pool, get_pool
from pitwall.db.repository import LeaseRepository, WorkloadRepository

async def main(kind, record_id, state):
    pool = await get_pool()
    now = dt.datetime.now(dt.UTC)
    try:
        if kind == "lease":
            await LeaseRepository(pool).create(Lease(
                id=record_id, provider_id="prov_demo_runpod_lb", runpod_pod_id="pod" + record_id[-10:],
                state=LeaseState.ACTIVE, created_at=now, expires_at=now + dt.timedelta(hours=1),
                renewal_policy=LeaseRenewalPolicy.MANUAL,
                endpoints=LeaseEndpoints(http={"8000": "https://pod-8000.proxy.runpod.net"}),
                readiness=LeaseReadiness(runtime_seen_at=now, port_mappings_seen_at=now,
                                         probe_passed_at=now, probe_method="runpod_proxy"),
            ))
        else:
            done = state == "completed"
            await WorkloadRepository(pool).insert(Workload(
                id=record_id, capability_id="cap_embedding_demo", provider_id="prov_demo_runpod_lb",
                type="async_job", state=WorkloadState(state), submitted_at=now,
                completed_at=now if done else None,
                result={"embeddings": [[0.1]]} if done else None,
            ))
    finally:
        await close_pool()

asyncio.run(main(*sys.argv[1:4]))
"""


def _seed(env: dict[str, str], kind: str, record_id: str, state: str = "") -> None:
    """Create a record through the real repositories, as the broker itself would."""
    subprocess.run(
        [sys.executable, "-c", _SEED, kind, record_id, state],
        env=env,
        check=True,
        capture_output=True,
        timeout=60,
    )


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


def _setup_capability(
    api: Api, *, class_: str = "embedding", cost_mode: str = "per_request"
) -> dict[str, str]:
    name = f"j35.cap.{_suffix()}"
    body = {"name": name, "version": "1.0.0", "class": class_, "cost_mode": cost_mode}
    created = api.admin("POST", "/v1/admin/capabilities", json=body).json()
    return {"capability_id": created["id"], "capability_name": name}


def _setup_provider(api: Api) -> dict[str, str]:
    context = _setup_capability(api)
    body = {
        "capability_id": context["capability_id"],
        "name": f"j35-prov-{_suffix()}",
        "provider_type": "serverless_lb",
        "runpod_endpoint_id": f"ep{_suffix()}",
    }
    created = api.admin("POST", "/v1/admin/providers", json=body).json()
    return {**context, "provider_id": created["id"]}


def _setup_openai_upstream(api: Api) -> dict[str, str]:
    # A zero-cost provider routes only for a zero-cost capability (the planner's cost gate).
    context = _setup_capability(api, class_="llm", cost_mode="zero")
    body = {
        "capability_id": context["capability_id"],
        "name": f"j35-openai-{_suffix()}",
        "provider_type": "openai_gateway",
        "adapter_id": "openai_gateway",
        "health_status": "healthy",
        "config": {"cost": {"mode": "zero"}, "openai_base_url": api.upstream.url},
    }
    created = api.admin("POST", "/v1/admin/providers", json=body).json()
    return {**context, "provider_id": created["id"]}


def _setup_webhook(api: Api) -> dict[str, str]:
    # A public IP literal: target validation needs no DNS, and creation sends nothing.
    body = {"consumer": f"j35-{_suffix()}", "webhook_url": "https://1.1.1.1/pitwall-j35"}
    headers = {"Authorization": f"Bearer {TOKENS['webhook:admin']}"}
    created = api.client.post("/v1/webhook-subscriptions", json=body, headers=headers)
    assert created.status_code == 201, created.text[:300]
    return {"subscription_id": created.json()["id"]}


def _setup_lease(api: Api) -> dict[str, str]:
    lease_id = f"lease_j35_{_suffix()}"
    _seed(api.env, "lease", lease_id)
    return {"lease_id": lease_id}


def _setup_workload(state: str) -> Callable[[Api], dict[str, str]]:
    def setup(api: Api) -> dict[str, str]:
        workload_id = f"wkl_j35_{_suffix()}"
        _seed(api.env, "workload", workload_id, state)
        return {"workload_id": workload_id}

    return setup


SETUPS: dict[str, Callable[[Api], dict[str, str]]] = {
    "capability": _setup_capability,
    "provider": _setup_provider,
    "openai_upstream": _setup_openai_upstream,
    "webhook": _setup_webhook,
    "lease": _setup_lease,
    "completed_workload": _setup_workload("completed"),
    "queued_workload": _setup_workload("queued"),
}


def _fill(value: Any, context: dict[str, str]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return context[value[1:]]
    if isinstance(value, dict):
        return {key: _fill(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, context) for item in value]
    return value


def _denied_scope(scope: str) -> str:
    return "spend" if scope == "read" else "read"


@dataclass
class Outcome:
    status: int
    body: Any
    anonymous: int | None
    wrong_scope: int | None


def call(api: Api, operation: str) -> Outcome:
    method, template = operation.split(" ", 1)
    fixture = FIXTURES[operation]
    context = SETUPS[fixture["setup"]](api) if fixture.get("setup") else {}
    path_params = {key: str(_fill(v, context)) for key, v in fixture.get("path", {}).items()}
    url = template.format(**path_params)
    kwargs: dict[str, Any] = {"params": _fill(fixture.get("query"), context)}
    if "body" in fixture:
        kwargs["json"] = _fill(fixture["body"], context)
    admin = {"X-Pitwall-Secret": ADMIN_SECRET} if template.startswith("/v1/admin") else {}
    scope = fixture["scope"]
    anonymous = wrong_scope = None
    if scope != "none":
        anonymous = api.client.request(method, url, headers=admin, **kwargs).status_code
        wrong = {"Authorization": f"Bearer {TOKENS[_denied_scope(scope)]}", **admin}
        wrong_scope = api.client.request(method, url, headers=wrong, **kwargs).status_code
    headers = {**admin}
    if scope != "none":
        headers["Authorization"] = f"Bearer {TOKENS[scope]}"
    response = api.client.request(method, url, headers=headers, **kwargs)
    try:
        body = response.json()
    except ValueError:
        body = None
    return Outcome(response.status_code, body, anonymous, wrong_scope)


@contextmanager
def _running_api(journey_env: dict[str, str], *, budget: bool) -> Iterator[Api]:
    upstream = FakeUpstream()
    port = _free_port()
    env = {
        **journey_env,
        "PITWALL_API_PORT": str(port),
        "PITWALL_API_SCOPED_TOKENS": json.dumps({token: [s] for s, token in TOKENS.items()}),
        "PITWALL_ADMIN_SECRET": ADMIN_SECRET,
        # A fixed, test-only 32-byte key: webhook subscriptions store encrypted secrets.
        "PITWALL_WEBHOOK_ENCRYPTION_KEYS": json.dumps(
            {"j35": base64.urlsafe_b64encode(b"j" * 32).decode()}
        ),
        "PITWALL_WEBHOOK_ENCRYPTION_CURRENT_KEY": "j35",
    }
    if budget:
        # A renewal reserves its extension against the budget, so the broker needs one for
        # the renew success path; without it renew answers budget_not_configured.
        env["PITWALL_MONTHLY_BUDGET_USD"] = "1000"
        env["PITWALL_PER_REQUEST_MAX_USD"] = "100"
    server = subprocess.Popen(
        [str(Path(sys.executable).parent / "pitwall-api")],
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=60, trust_env=False) as client:
            deadline = time.monotonic() + HANG_GUARD_SECS
            while True:
                try:
                    if client.get("/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                assert server.poll() is None, "pitwall-api exited during startup"
                assert time.monotonic() < deadline, "pitwall-api never became healthy"
                time.sleep(0.2)
            yield Api(client, env, upstream)
    finally:
        server.terminate()
        try:
            server.wait(timeout=HANG_GUARD_SECS)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()
        upstream.close()


@pytest.fixture(scope="module")
def api(journey_env: dict[str, str]) -> Iterator[Api]:
    with _running_api(journey_env, budget=True) as running:
        yield running


@pytest.fixture(scope="module")
def outcomes(api: Api) -> dict[str, Outcome]:
    order = [op for op in sorted(FIXTURES) if op not in _LAST] + list(_LAST)
    return {operation: call(api, operation) for operation in order}


def test_every_operation_has_a_fixture(api: Api) -> None:
    """Every operation the running server publishes, not a committed copy, has a fixture."""
    live = api.client.get("/openapi.json", headers={"Authorization": f"Bearer {TOKENS['read']}"})
    assert live.status_code == 200
    assert sorted(FIXTURES) == operations(live.json())
    for operation, fixture in FIXTURES.items():
        assert fixture["scope"] in {"none", *TOKENS}, operation
        assert not ("expect_keys" in fixture and "expect_error" in fixture), operation
        assert fixture.get("setup") in {None, *SETUPS}, operation


@pytest.mark.parametrize("operation", sorted(FIXTURES))
def test_operation(operation: str, outcomes: dict[str, Outcome]) -> None:
    fixture = FIXTURES[operation]
    outcome = outcomes[operation]
    assert outcome.status == fixture["status"], (operation, outcome.body)
    if "expect_error" in fixture:
        assert isinstance(outcome.body, dict), operation
        assert outcome.body.get("error") == fixture["expect_error"], (operation, outcome.body)
    for key in fixture.get("expect_keys", []):
        assert isinstance(outcome.body, dict) and key in outcome.body, (operation, key)
    if fixture["scope"] != "none":
        assert outcome.anonymous == 401, (operation, outcome.anonymous)
        assert outcome.wrong_scope == 403, (operation, outcome.wrong_scope)


def test_renew_without_a_budget_is_a_typed_refusal(journey_env: dict[str, str]) -> None:
    """A broker with no budget refuses a renewal with 503 budget_not_configured, not a 500.

    The renewal reserves its extension against the budget, so it is refused before any write:
    the lease keeps its expiry.
    """
    with _running_api(journey_env, budget=False) as api:
        lease_id = _setup_lease(api)["lease_id"]
        headers = {"Authorization": f"Bearer {TOKENS['lease:mutate']}"}
        reader = {"Authorization": f"Bearer {TOKENS['read']}"}
        before = api.client.get(f"/v1/leases/{lease_id}", headers=reader)
        assert before.status_code == 200, before.text[:300]
        response = api.client.post(
            f"/v1/leases/{lease_id}/renew", json={"extends_minutes": 10}, headers=headers
        )
        assert response.status_code == 503, response.text[:300]
        body = response.json()
        assert body["error"] == "budget_not_configured"
        assert "PITWALL_MONTHLY_BUDGET_USD" in body["remedy"]
        after = api.client.get(f"/v1/leases/{lease_id}", headers=reader)
        assert after.json()["expires_at"] == before.json()["expires_at"]
