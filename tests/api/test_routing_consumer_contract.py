"""Executable Pitwall contract consumed by `pitwall agents` (hermetic)."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import re
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from pitwall.core.enums import (
    CapabilityClass,
    CapabilitySource,
    LeaseRenewalPolicy,
    LeaseState,
    ProviderType,
)
from pitwall.core.models import Capability, Lease, LeaseEndpoints, LeaseReadiness, Provider
from pitwall.cost.budget_limits import BudgetLimits
from tests.api._route_helpers import iter_effective_routes
from tests.conftest import make_asyncpg_pool

_NOW = datetime(2026, 5, 28, 12, 0, 0, tzinfo=UTC)
_EXPIRES_AT = datetime(2026, 5, 28, 14, 0, 0, 123456, tzinfo=UTC)
_CAPABILITY_NAME = "llm.glimmer_1-2"
_SERVED_MODEL_ID = "muse-glimmer-30b"
_AUTH = {"Authorization": "Bearer routing-token"}


@pytest.mark.anyio
async def test_lease_delivery_pins_envelope_keys_header_and_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from examples.verify_webhook import verify
    from pitwall.leases import events
    from pitwall.webhook_dispatcher import dispatcher

    captured: dict[str, Any] = {}

    async def post(
        target: Any,
        body: bytes,
        headers: dict[str, str],
        timeout: float,
    ) -> int:
        del target, timeout
        captured.update(body=body, headers=headers)
        return 204

    target = SimpleNamespace(url="https://receiver.example/pitwall")
    monkeypatch.setattr(dispatcher, "resolve_webhook_target", AsyncMock(return_value=target))
    monkeypatch.setattr(dispatcher, "post_resolved_webhook", post)
    event = events.build_lease_expiring_event(
        lease=_active_lease(),
        capability_name=_CAPABILITY_NAME,
        minutes_left=15,
    )

    outcome = await dispatcher.attempt_delivery(
        "https://receiver.example/pitwall",
        event,
        "routing-secret",
        retry_delays=(0,),
        delivery_id=event["delivery_id"],
        loopback_allowlist=(),
    )

    assert outcome.success is True
    body = json.loads(captured["body"])
    assert set(body) == {
        "version",
        "event",
        "delivery_id",
        "occurred_at",
        "capability",
        "data",
    }
    assert body["delivery_id"] == outcome.delivery_id
    assert body["event"] == "lease.expiring"
    assert body["data"] == {
        "capability": _CAPABILITY_NAME,
        "lease_id": _active_lease().id,
        "expires_at": _active_lease().expires_at.isoformat(),
        "minutes_left": 15,
    }
    headers = captured["headers"]
    assert "X-Pitwall-Signature" in headers
    assert verify(
        captured["body"],
        headers["X-Pitwall-Signature"],
        "routing-secret",
    )


class _AsyncByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = tuple(chunks)

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


class _FailingAsyncByteStream(_AsyncByteStream):
    def __init__(self, chunks: list[bytes], fail_after: int) -> None:
        super().__init__(chunks)
        self._fail_after = fail_after

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self._chunks):
            if index >= self._fail_after:
                raise httpx.ReadError(
                    "connection lost", request=httpx.Request("POST", "https://upstream")
                )
            yield chunk


class _ASGIChunkPreservingStream(httpx.AsyncByteStream):
    def __init__(
        self, body_queue: asyncio.Queue[bytes | None], app_task: asyncio.Task[Any]
    ) -> None:
        self._body_queue = body_queue
        self._app_task = app_task

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            body = await self._body_queue.get()
            if body is None:
                await self._app_task
                return
            yield body

    async def aclose(self) -> None:
        if not self._app_task.done():
            await self._app_task


class _ASGIChunkPreservingTransport(httpx.AsyncBaseTransport):
    def __init__(self, app: Any) -> None:
        self._app = app

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        request_body = request.stream.__aiter__()
        body_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        response_started = asyncio.Event()
        response_complete = asyncio.Event()
        response_status: int | None = None
        response_headers: list[tuple[bytes, bytes]] = []
        request_complete = False

        async def receive() -> dict[str, Any]:
            nonlocal request_complete
            if request_complete:
                await response_complete.wait()
                return {"type": "http.disconnect"}
            try:
                body = await request_body.__anext__()
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": body, "more_body": True}

        async def send(message: dict[str, Any]) -> None:
            nonlocal response_status, response_headers
            if message["type"] == "http.response.start":
                response_status = message["status"]
                response_headers = list(message.get("headers", []))
                response_started.set()
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and request.method != "HEAD":
                    await body_queue.put(body)
                if not message.get("more_body", False):
                    response_complete.set()
                    await body_queue.put(None)

        app_task = asyncio.create_task(
            self._app(
                {
                    "type": "http",
                    "asgi": {"version": "3.0"},
                    "http_version": "1.1",
                    "method": request.method,
                    "headers": [(key.lower(), value) for key, value in request.headers.raw],
                    "scheme": request.url.scheme,
                    "path": request.url.path,
                    "raw_path": request.url.raw_path.split(b"?")[0],
                    "query_string": request.url.query,
                    "server": (request.url.host, request.url.port),
                    "client": ("127.0.0.1", 123),
                    "root_path": "",
                },
                receive,
                send,
            )
        )
        await response_started.wait()
        assert response_status is not None
        return httpx.Response(
            response_status,
            headers=response_headers,
            stream=_ASGIChunkPreservingStream(body_queue, app_task),
        )


@pytest.fixture(autouse=True)
def _clear_app_module() -> None:
    _purge_api_modules()
    yield
    _purge_api_modules()


def _purge_api_modules() -> None:
    for key in [key for key in sys.modules if key.startswith("pitwall.api")]:
        del sys.modules[key]


def _import_app() -> Any:
    old = os.environ.copy()
    environment = {
        "RUNPOD_API_KEY": "test-key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "PITWALL_API_SCOPED_TOKENS": json.dumps(
            {"routing-token": ["read", "spend"], "read-only-token": ["read"]}
        ),
    }
    os.environ.update(environment)
    for key in ("PITWALL_API_TOKEN", "PITWALL_ADMIN_SECRET", "PITWALL_INBOUND_RATE_LIMIT"):
        os.environ.pop(key, None)
    try:
        return importlib.import_module("pitwall.api.app")
    finally:
        os.environ.clear()
        os.environ.update(old)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("refusal", "expected"),
    [
        (
            "cap_exceeded",
            {
                "error": "cap_exceeded",
                "gpu_class": "NVIDIA L4",
                "price_usd_per_hour": "1.25",
                "max_usd_per_hour": "1.00",
            },
        ),
        (
            "price_unknown",
            {
                "error": "price_unknown",
                "gpu_class": "NVIDIA L4",
                "max_usd_per_hour": "1.00",
            },
        ),
        (
            "budget_exhausted",
            {
                "error": "budget_exhausted",
                "reason": "monthly_budget",
                "snapshot": {
                    "monthly_budget_usd": "10",
                    "per_request_max_usd": "5",
                    "mtd_spend_usd": "10",
                    "estimate_usd": "1",
                    "budget_remaining_usd": "0",
                },
            },
        ),
        ("kill_switch_engaged", {"error": "kill_switch_engaged"}),
        ("no_serve_history", {"error": "no_serve_history"}),
    ],
)
async def test_post_serve_refusals_are_top_level_422_contracts(
    monkeypatch: pytest.MonkeyPatch,
    refusal: str,
    expected: dict[str, Any],
) -> None:
    app_mod = _import_app()
    import pitwall.api.routes.serve as route
    from pitwall.api.admin.kill_switch import KillSwitchEngaged
    from pitwall.api.exceptions import (
        ServeCapExceeded,
        ServeNoServeHistory,
        ServePriceUnknown,
    )
    from pitwall.cost import BudgetRejected, BudgetSnapshot

    errors = {
        "cap_exceeded": ServeCapExceeded(
            gpu_class="NVIDIA L4",
            price_usd_per_hour=Decimal("1.25"),
            max_usd_per_hour=Decimal("1.00"),
        ),
        "price_unknown": ServePriceUnknown(
            gpu_class="NVIDIA L4",
            max_usd_per_hour=Decimal("1.00"),
        ),
        "budget_exhausted": BudgetRejected(
            "monthly_budget",
            BudgetSnapshot(
                monthly_budget_usd=Decimal("10"),
                per_request_max_usd=Decimal("5"),
                mtd_spend_usd=Decimal("10"),
                estimate_usd=Decimal("1"),
                budget_remaining_usd=Decimal("0"),
            ),
        ),
        "kill_switch_engaged": KillSwitchEngaged(),
        "no_serve_history": ServeNoServeHistory(_CAPABILITY_NAME),
    }
    monkeypatch.setattr(route, "serve_model", AsyncMock(side_effect=errors[refusal]))
    app_mod.app.state.pool = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/serve",
            headers=_AUTH,
            json={"capability": _CAPABILITY_NAME},
        )

    assert response.status_code == 422
    assert response.json() == expected


@pytest.mark.anyio
async def test_capability_only_serve_request_and_idempotent_created_are_pinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mod = _import_app()
    import pitwall.api.routes.serve as route

    captured: dict[str, Any] = {}

    async def replay(
        pool: Any,
        request: Any,
        **kwargs: Any,
    ) -> Any:
        del pool, kwargs
        captured["request"] = request
        from pitwall.serve import ServeResult

        return ServeResult(
            capability=_CAPABILITY_NAME,
            lease_id="lease-routing-1",
            expires_at=_EXPIRES_AT.isoformat(),
            model_id=_SERVED_MODEL_ID,
            proxy_base_url=f"http://test/v1/openai/{_CAPABILITY_NAME}/v1",
            engine="vllm",
            variant="fp8",
            gpu_count=2,
            workload_id=None,
            template_id=None,
            provider_id="prov-routing",
            dry_run=False,
            created=False,
            cost_estimate_usd=None,
        )

    monkeypatch.setattr(route, "serve_model", replay)
    app_mod.app.state.pool = object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/serve",
            headers=_AUTH,
            json={
                "capability": _CAPABILITY_NAME,
                "idle_timeout_min": 20,
                "max_usd_per_hour": "1.25",
            },
        )

    assert response.status_code == 200
    assert captured["request"].model_dump(
        mode="json",
        exclude_unset=True,
    ) == {
        "capability_name": _CAPABILITY_NAME,
        "idle_timeout_min": 20,
        "max_usd_per_hour": "1.25",
    }
    assert response.json()["created"] is False


def _capability(*, served_model_id: str | None = _SERVED_MODEL_ID) -> Capability:
    return Capability(
        id="cap_routing_contract",
        name=_CAPABILITY_NAME,
        version="1.0.0",
        class_=CapabilityClass.LLM,
        cost_mode="per_second",
        source=CapabilitySource.API,
        served_model_id=served_model_id,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _active_lease() -> Lease:
    return Lease(
        id="lease-routing-1",
        provider_id="prov-routing",
        runpod_pod_id="pod-routing-1",
        state=LeaseState.ACTIVE,
        created_at=_NOW,
        expires_at=_EXPIRES_AT,
        renewal_policy=LeaseRenewalPolicy.ACTIVITY,
        endpoints=LeaseEndpoints(http={"8000": "https://pod-routing-1-8000.proxy.runpod.net"}),
        readiness=LeaseReadiness(
            runtime_seen_at=_NOW,
            port_mappings_seen_at=_NOW,
            probe_passed_at=_NOW,
            probe_method="runpod_proxy",
        ),
        last_traffic_at=datetime(2026, 5, 28, 12, 5, tzinfo=UTC),
        idle_timeout_min=20,
        max_usd_per_hour=Decimal("2.5000"),
    )


def _pod_lease_provider(*, armed: bool) -> Provider:
    config: dict[str, object] = {
        "ports": {"http": [8000]},
        "openai_proxy_port": 8000,
        "cost": {"per_second_active": "0.002"},
    }
    if armed:
        config.update({"active_pod_id": "pod-routing-1", "active_lease_id": "lease-routing-1"})
    return Provider(
        id="prov-routing",
        capability_id="cap_routing_contract",
        name=f"serve-{_CAPABILITY_NAME}",
        provider_type=ProviderType.POD_LEASE,
        config=config,
        priority=0,
        enabled=True,
        health_status="healthy" if armed else "unhealthy",
        updated_at=_NOW,
    )


@pytest.mark.anyio
async def test_ready_replay_emits_created_false_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pitwall import serve as service

    published = AsyncMock()
    monkeypatch.setattr(service, "publish_lease_event", published)
    capability = _capability()
    lease = _active_lease()
    provider = _pod_lease_provider(armed=True)
    capability_repo = AsyncMock()
    capability_repo.get_by_name.return_value = capability
    provider_repo = AsyncMock()
    provider_repo.get_by_name.return_value = provider
    lease_repo = AsyncMock()
    lease_repo.latest_active_for_provider.return_value = lease
    monkeypatch.setattr(service, "CapabilityRepository", lambda _pool: capability_repo)
    monkeypatch.setattr(service, "ProviderRepository", lambda _pool: provider_repo)
    monkeypatch.setattr(service, "LeaseRepository", lambda _pool: lease_repo)

    # The replay publishes lease.ready only after the pod lists the served model.
    with respx.mock:
        models = respx.get("https://pod-routing-1-8000.proxy.runpod.net/v1/models").mock(
            return_value=httpx.Response(200, json={"data": [{"id": _SERVED_MODEL_ID}]})
        )
        result = await service.serve_model(
            MagicMock(),
            service.ServeRequest(
                capability_name=_CAPABILITY_NAME,
                model=_SERVED_MODEL_ID,
                served_model_name=_SERVED_MODEL_ID,
                gpu_class="NVIDIA L4",
                image="example/model:test",
                rate_per_second=Decimal("0.001"),
            ),
            base_url="http://test",
            settings=service.PitwallSettings(),
            redis=MagicMock(),
        )

    assert models.called
    assert result.created is False
    event = published.await_args.args[2]
    assert event["event"] == "lease.ready"
    assert event["data"]["created"] is False


def _setup(
    capability: Capability, lease: Lease | None, provider: Provider
) -> tuple[Any, AsyncMock]:
    app_mod = _import_app()
    from pitwall.api.capability_routes import (
        _lease_repo,
        _pool,
        _repo,
    )
    from pitwall.api.capability_routes import (
        _provider_repo as _metadata_provider_repo,
    )
    from pitwall.api.routes.openai import (
        _budget_gate,
        _capability_repo,
        _provider_repo,
        _workload_repo,
    )

    pool = make_asyncpg_pool()
    capability_repo = AsyncMock()
    capability_repo.get_by_name.side_effect = lambda name: (
        capability if name == capability.name else None
    )
    capability_repo.list.return_value = [capability]
    lease_repo = AsyncMock()
    lease_repo.latest_active_for_capability.return_value = lease
    lease_repo.latest_active_for_capabilities.return_value = (
        {capability.id: lease} if lease is not None else {}
    )
    provider_repo = AsyncMock()
    provider_repo.list.return_value = [provider]
    budget_gate = AsyncMock()
    budget_gate.monthly_budget_usd = Decimal("100")
    budget_gate.per_request_max_usd = Decimal("10")
    budget_gate.effective_limits.return_value = BudgetLimits(
        Decimal("100"), Decimal("10"), "environment"
    )
    budget_gate.current_mtd_spend.return_value = Decimal("0")
    workload_repo = AsyncMock()
    workload_repo.insert.side_effect = lambda workload: workload
    workload_repo.guarded_transition.return_value = MagicMock()

    app_mod.app.state.pool = pool
    app_mod.app.dependency_overrides[_repo] = lambda: capability_repo
    app_mod.app.dependency_overrides[_lease_repo] = lambda: lease_repo
    app_mod.app.dependency_overrides[_metadata_provider_repo] = lambda: provider_repo
    app_mod.app.dependency_overrides[_pool] = lambda: pool
    app_mod.app.dependency_overrides[_capability_repo] = lambda: capability_repo
    app_mod.app.dependency_overrides[_provider_repo] = lambda: provider_repo
    app_mod.app.dependency_overrides[_budget_gate] = lambda: budget_gate
    app_mod.app.dependency_overrides[_workload_repo] = lambda: workload_repo
    return app_mod, capability_repo


@pytest.mark.anyio
async def test_routing_metadata_contract_round_trips_safe_name_and_404() -> None:
    app_mod, capability_repo = _setup(
        _capability(), _active_lease(), _pod_lease_provider(armed=True)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get(f"/v1/capabilities/{_CAPABILITY_NAME}", headers=_AUTH)
        unknown = await client.get("/v1/capabilities/unknown-routing", headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == _CAPABILITY_NAME
    assert body["served_model_id"] == _SERVED_MODEL_ID
    assert body["active_lease"] == {
        "lease_id": "lease-routing-1",
        "state": "active",
        "expires_at": "2026-05-28T14:00:00.123456+00:00",
    }
    assert body["idle_timeout_min"] == 20
    assert body["last_traffic_at"] == "2026-05-28T12:05:00Z"
    assert body["renewal_policy"] == "activity"
    assert body["max_usd_per_hour"] == "2.5000"
    parsed_expiry = re.sub(
        r"\.\d+(?=(?:Z|[+-]\d{2}:\d{2})$)", "", body["active_lease"]["expires_at"]
    )
    assert datetime.fromisoformat(parsed_expiry) == _EXPIRES_AT.replace(microsecond=0)
    capability_repo.get_by_name.assert_any_await(_CAPABILITY_NAME)
    assert unknown.status_code == 404
    assert unknown.headers["content-type"].startswith("application/json")
    assert unknown.json()["error"] == "capability_not_found"


@pytest.mark.anyio
async def test_routing_metadata_contract_allows_absent_model_and_lease() -> None:
    app_mod, _capability_repo = _setup(
        _capability(served_model_id=None), None, _pod_lease_provider(armed=False)
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get(f"/v1/capabilities/{_CAPABILITY_NAME}", headers=_AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["served_model_id"] is None
    assert body["active_lease"] is None
    assert {
        name: body[name]
        for name in (
            "idle_timeout_min",
            "last_traffic_at",
            "renewal_policy",
            "max_usd_per_hour",
        )
    } == {
        "idle_timeout_min": None,
        "last_traffic_at": None,
        "renewal_policy": None,
        "max_usd_per_hour": None,
    }


def _self_hosted_metadata_provider() -> Provider:
    return Provider(
        id="prov-selfhosted-contract",
        capability_id="cap_routing_contract",
        name="selfhosted-contract",
        provider_type=ProviderType.PUBLIC_ENDPOINT,
        config={
            "openai_base_url": "http://localhost:9292/v1",
            "self_hosted": {
                "readiness": {"kind": "llama-swap"},
                "models": [
                    {
                        "id": _SERVED_MODEL_ID,
                        "slot_group": "gpu0",
                        "exclusive": True,
                        "context_length": 32768,
                        "tool_calling": "disabled",
                    }
                ],
                "idle_unload_s": 1800,
            },
            "self_hosted_state": {
                "readiness": "ready",
                "resident": [_SERVED_MODEL_ID],
                "observed_at": _NOW.isoformat(),
                "cold_start_s": {"p50": 12.5, "p95": 21.0},
                "tool_calling": "enabled",
            },
        },
        priority=0,
        enabled=True,
        health_status="healthy",
        updated_at=_NOW,
    )


_SELF_HOSTED_FIELDS = (
    "provider_kind",
    "readiness",
    "resident",
    "cold_start_s",
    "slot_group",
    "exclusive",
    "context_length",
    "tool_calling",
    "idle_unload_s",
)


@pytest.mark.anyio
async def test_non_self_hosted_metadata_is_null_on_list_and_detail() -> None:
    app_mod, _ = _setup(_capability(), _active_lease(), _pod_lease_provider(armed=True))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        detail = await client.get(f"/v1/capabilities/{_CAPABILITY_NAME}", headers=_AUTH)
        listing = await client.get("/v1/capabilities", headers=_AUTH)

    assert detail.status_code == 200
    assert listing.status_code == 200
    expected = dict.fromkeys(_SELF_HOSTED_FIELDS)
    assert {field: detail.json()[field] for field in _SELF_HOSTED_FIELDS} == expected
    item = listing.json()["items"][0]
    assert {field: item[field] for field in _SELF_HOSTED_FIELDS} == expected


@pytest.mark.anyio
async def test_self_hosted_metadata_comes_from_profile_and_observed_state() -> None:
    app_mod, _ = _setup(_capability(), None, _self_hosted_metadata_provider())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        detail = await client.get(f"/v1/capabilities/{_CAPABILITY_NAME}", headers=_AUTH)
        listing = await client.get("/v1/capabilities", headers=_AUTH)

    expected = {
        "provider_kind": "self_hosted",
        "readiness": {"kind": "llama-swap", "state": "ready"},
        "resident": True,
        "cold_start_s": {"p50": 12.5, "p95": 21.0},
        "slot_group": "gpu0",
        "exclusive": True,
        "context_length": 32768,
        "tool_calling": "enabled",
        "idle_unload_s": 1800,
    }
    assert {field: detail.json()[field] for field in _SELF_HOSTED_FIELDS} == expected
    item = listing.json()["items"][0]
    assert {field: item[field] for field in _SELF_HOSTED_FIELDS} == expected


@pytest.mark.anyio
async def test_partial_cold_start_is_null_until_both_measurements_exist() -> None:
    from pitwall.api.capability_routes import _provider_repo as metadata_provider_repo

    provider = _self_hosted_metadata_provider()
    partial = provider.model_copy(
        update={
            "config": {
                **provider.config,
                "self_hosted_state": {
                    **provider.config["self_hosted_state"],
                    "cold_start_s": {"p50": None, "p95": None},
                },
            }
        }
    )
    app_mod, _ = _setup(_capability(), None, partial)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        unmeasured = await client.get(f"/v1/capabilities/{_CAPABILITY_NAME}", headers=_AUTH)
        provider_repo = app_mod.app.dependency_overrides[metadata_provider_repo]()
        provider_repo.list.return_value = [_self_hosted_metadata_provider()]
        measured = await client.get(f"/v1/capabilities/{_CAPABILITY_NAME}", headers=_AUTH)

    assert unmeasured.status_code == 200
    assert unmeasured.json()["cold_start_s"] is None
    assert measured.status_code == 200
    assert measured.json()["cold_start_s"] == {"p50": 12.5, "p95": 21.0}


@pytest.mark.anyio
@pytest.mark.parametrize("providers", ["ordinary-first", "self-hosted-first"])
async def test_metadata_selects_highest_priority_self_hosted_provider_independent_of_order(
    providers: str,
) -> None:
    app_mod, _ = _setup(_capability(), None, _pod_lease_provider(armed=False))
    from pitwall.api.capability_routes import _provider_repo as metadata_provider_repo

    ordinary = _pod_lease_provider(armed=False)
    tied = _self_hosted_metadata_provider().model_copy(
        update={"id": "prov-selfhosted-z", "priority": 1}
    )
    selected_config = _self_hosted_metadata_provider().config
    selected = _self_hosted_metadata_provider().model_copy(
        update={
            "id": "prov-selfhosted-a",
            "priority": 1,
            "config": {
                **selected_config,
                "self_hosted": {
                    **selected_config["self_hosted"],
                    "models": [
                        {
                            "id": _SERVED_MODEL_ID,
                            "context_length": 16384,
                        }
                    ],
                },
            },
        }
    )
    rows = [ordinary, tied, selected]
    if providers == "self-hosted-first":
        rows.reverse()
    provider_repo = app_mod.app.dependency_overrides[metadata_provider_repo]()
    provider_repo.list.return_value = rows
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        detail = await client.get(f"/v1/capabilities/{_CAPABILITY_NAME}", headers=_AUTH)
        listing = await client.get("/v1/capabilities", headers=_AUTH)

    assert detail.status_code == 200
    assert detail.json()["provider_kind"] == "self_hosted"
    assert detail.json()["context_length"] == 16384
    assert listing.json()["items"][0]["provider_kind"] == "self_hosted"
    assert listing.json()["items"][0]["context_length"] == 16384
    assert provider_repo.list.await_args_list[0].kwargs["limit"] > 1


@respx.mock
@pytest.mark.anyio
async def test_routing_proxy_contract_passes_model_id_and_returns_503_after_teardown() -> None:
    app_mod, _capability_repo = _setup(
        _capability(), _active_lease(), _pod_lease_provider(armed=True)
    )
    from pitwall.api.routes.openai import _provider_repo

    route = respx.get("https://pod-routing-1-8000.proxy.runpod.net/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": _SERVED_MODEL_ID}]})
    )
    provider_repo = app_mod.app.dependency_overrides[_provider_repo]()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        response = await client.get(f"/v1/openai/{_CAPABILITY_NAME}/v1/models", headers=_AUTH)
        provider_repo.list.return_value = [_pod_lease_provider(armed=False)]
        torn_down = await client.get(f"/v1/openai/{_CAPABILITY_NAME}/v1/models", headers=_AUTH)

    assert response.status_code == 200
    assert response.json() == {"data": [{"id": _SERVED_MODEL_ID}]}
    assert route.called
    assert torn_down.status_code == 503


@respx.mock
@pytest.mark.anyio
async def test_proxy_streams_sse_chunks_unbuffered() -> None:
    app_mod, _capability_repo = _setup(
        _capability(), _active_lease(), _pod_lease_provider(armed=True)
    )
    chunks = [
        b'data: {"delta":"one"}\n\n',
        b'data: {"delta":"two"}\n\n',
        b'data: {"delta":"three"}\n\n',
        b"data: [DONE]\n\n",
    ]
    respx.post("https://pod-routing-1-8000.proxy.runpod.net/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_AsyncByteStream(chunks),
        )
    )

    async with (
        httpx.AsyncClient(
            transport=_ASGIChunkPreservingTransport(app_mod.app), base_url="http://test"
        ) as client,
        client.stream(
            "POST",
            f"/v1/openai/{_CAPABILITY_NAME}/v1/chat/completions",
            json={"model": _SERVED_MODEL_ID, "messages": [], "stream": True},
            headers=_AUTH,
        ) as response,
    ):
        received = [chunk async for chunk in response.aiter_raw()]

    assert response.headers["content-type"].startswith("text/event-stream")
    assert received == chunks
    assert len(received) > 1


@respx.mock
@pytest.mark.anyio
async def test_proxy_stamps_active_lease_after_upstream_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_mod, _capability_repo = _setup(
        _capability(), _active_lease(), _pod_lease_provider(armed=True)
    )
    stamped = AsyncMock()
    monkeypatch.setattr("pitwall.api.routes.openai.stamp_lease_traffic", stamped)
    redis = object()
    app_mod.app.state.redis = redis
    respx.get("https://pod-routing-1-8000.proxy.runpod.net/v1/models").mock(
        return_value=httpx.Response(200, json={"data": [{"id": _SERVED_MODEL_ID}]})
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            f"/v1/openai/{_CAPABILITY_NAME}/v1/models",
            headers=_AUTH,
        )

    assert response.status_code == 200
    stamped.assert_awaited_once()
    assert stamped.await_args.args == (redis, "lease-routing-1")
    assert stamped.await_args.kwargs["now"].tzinfo is UTC


@respx.mock
@pytest.mark.anyio
async def test_proxy_sse_mid_stream_failure_emits_terminal_error_event() -> None:
    app_mod, _capability_repo = _setup(
        _capability(), _active_lease(), _pod_lease_provider(armed=True)
    )
    first_chunk = b'data: {"delta":"one"}\n\n'
    terminal_error = b'data: {"error":"upstream stream failure"}\n\n'
    respx.post("https://pod-routing-1-8000.proxy.runpod.net/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=_FailingAsyncByteStream([first_chunk, b"unused"], fail_after=1),
        )
    )

    async with (
        httpx.AsyncClient(
            transport=_ASGIChunkPreservingTransport(app_mod.app), base_url="http://test"
        ) as client,
        client.stream(
            "POST",
            f"/v1/openai/{_CAPABILITY_NAME}/v1/chat/completions",
            json={"model": _SERVED_MODEL_ID, "messages": [], "stream": True},
            headers=_AUTH,
        ) as response,
    ):
        received = b"".join([chunk async for chunk in response.aiter_raw()])

    assert response.headers["content-type"].startswith("text/event-stream")
    assert received == first_chunk + terminal_error


@pytest.mark.anyio
async def test_routing_flow_requires_one_token_with_read_and_spend_scopes() -> None:
    app_mod, _capability_repo = _setup(
        _capability(), _active_lease(), _pod_lease_provider(armed=False)
    )
    routes = {route.path for route in iter_effective_routes(app_mod.app.routes)}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_mod.app), base_url="http://test"
    ) as client:
        metadata = await client.get(f"/v1/capabilities/{_CAPABILITY_NAME}", headers=_AUTH)
        proxy = await client.get(f"/v1/openai/{_CAPABILITY_NAME}/v1/models", headers=_AUTH)
        read_only_proxy = await client.get(
            f"/v1/openai/{_CAPABILITY_NAME}/v1/models",
            headers={"Authorization": "Bearer read-only-token"},
        )

    assert metadata.status_code == 200
    assert proxy.status_code == 503
    assert read_only_proxy.status_code == 403
    assert read_only_proxy.json()["required_scope"] == "spend"
    assert "/v1/leases/{lease_id}/renew" in routes
