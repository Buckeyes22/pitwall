"""GET /v1/models (models.test.ts, models-lifecycle.test.ts, and the models cases elsewhere)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from pitwall.gateway.app import ExecutorContext
from pitwall.gateway.config import resolve_config
from pitwall.gateway.relay import RelayResult
from pitwall.gateway.routes_table import GatewayRoute
from tests.gateway.test_app_support import (
    AUTH,
    ROUTE_KEY,
    UPSTREAM_SECRET,
    FakeUpstream,
    json_response,
    running_gateway,
)

CATALOG = {"object": "list", "data": [{"id": "qwen", "object": "model"}]}


async def local_executor(_context: ExecutorContext) -> RelayResult:
    return RelayResult(200, {"content-type": "application/json"}, b"{}")


def models_upstream(
    payload: object, status: int = 200, content_type: str = "application/json"
) -> FakeUpstream:
    return FakeUpstream(lambda _captured: json_response(payload, status, content_type))


@pytest.mark.parity
async def test_anonymous_callers_get_401_and_the_upstream_is_never_contacted() -> None:
    # Source: models.test.ts "GET /v1/models upstream listing > anonymous callers get 401"
    upstream = models_upstream(CATALOG)
    async with running_gateway(upstream) as gateway:
        response = await gateway.http.get("/v1/models")
    assert response.status_code == 401
    error = response.json()["error"]
    assert (error["type"], error["code"]) == ("authentication_error", "invalid_api_key")
    assert upstream.captured == []


@pytest.mark.parity
async def test_invalid_bearer_token_gets_401_and_the_upstream_is_never_contacted() -> None:
    # Source: models.test.ts "GET /v1/models upstream listing > invalid bearer token gets 401"
    upstream = models_upstream(CATALOG)
    async with running_gateway(upstream) as gateway:
        response = await gateway.http.get("/v1/models", headers={"authorization": "Bearer wrong"})
    assert response.status_code == 401
    assert upstream.captured == []


@pytest.mark.parity
async def test_lists_upstream_ids_alongside_local_executors_and_deduplicates() -> None:
    # Source: models.test.ts "lists the configured upstream ids alongside local executors and deduplicates"
    upstream = models_upstream(
        {
            "object": "list",
            "data": [
                {"id": "qwen2.5-14b-instruct", "object": "model", "created": 1, "owned_by": "vllm"},
                {"id": "local-only", "object": "model", "created": 2, "owned_by": "upstream"},
                {"id": "qwen2.5-14b-instruct", "object": "model", "created": 3, "owned_by": "vllm"},
            ],
        }
    )
    async with running_gateway(
        upstream,
        executors={"local-only": local_executor},
        upstream_base_url="http://upstream.invalid/v1",
    ) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["object"] == "list"
    assert sorted(model["id"] for model in body["data"]) == ["local-only", "qwen2.5-14b-instruct"]
    local = [model for model in body["data"] if model["id"] == "local-only"]
    assert len(local) == 1
    assert local[0]["owned_by"] == "pitwall-gateway"
    assert len(upstream.captured) == 1
    assert upstream.captured[0].method == "GET"
    assert upstream.captured[0].url == "http://upstream.invalid/v1/models"


@pytest.mark.parity
async def test_sends_the_configured_upstream_bearer_key_and_never_the_client_token() -> None:
    # Source: models.test.ts "sends the configured upstream bearer key and never the client token"
    upstream = models_upstream(
        {"object": "list", "data": [{"id": "qwen", "object": "model", "owned_by": "vllm"}]}
    )
    async with running_gateway(upstream, upstream_api_key=UPSTREAM_SECRET) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 200
    assert len(upstream.captured) == 1
    authorization = upstream.captured[0].headers["authorization"]
    assert authorization == f"Bearer {UPSTREAM_SECRET}"
    assert authorization != "Bearer t"
    assert UPSTREAM_SECRET not in upstream.captured[0].url


@pytest.mark.parity
async def test_never_forwards_the_client_token_when_no_upstream_key_is_configured() -> None:
    # Source: models.test.ts "never forwards the client token when no upstream key is configured"
    upstream = models_upstream(
        {"object": "list", "data": [{"id": "qwen", "object": "model", "owned_by": "vllm"}]}
    )
    async with running_gateway(upstream) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 200
    assert len(upstream.captured) == 1
    assert "authorization" not in upstream.captured[0].headers


@pytest.mark.parity
async def test_a_non_2xx_upstream_listing_is_a_structured_502_not_an_empty_list() -> None:
    # Source: models.test.ts "a non-2xx upstream listing is a structured 502, not an empty list"
    upstream = models_upstream({"error": "no catalog"}, status=404)
    async with running_gateway(upstream) as gateway:
        response = await gateway.get("/v1/models")
    body = response.json()
    assert response.status_code == 502
    assert body["error"]["code"] == "bad_gateway"
    assert body["error"]["request_id"] == response.headers["x-request-id"]
    assert len(body["error"]["request_id"]) == 36
    assert "data" not in body


@pytest.mark.parity
@pytest.mark.parametrize(
    "payload",
    [
        {"object": "list"},
        {"object": "list", "data": "nope"},
        {"object": "list", "data": [{"id": 7}]},
        {"object": "list", "data": [{"object": "model"}]},
        "not json at all",
    ],
)
async def test_a_malformed_upstream_catalog_is_a_structured_502_not_an_empty_list(
    payload: Any,
) -> None:
    # Source: models.test.ts "a malformed upstream catalog is a structured 502, not an empty list"
    content_type = "text/plain" if isinstance(payload, str) else "application/json"
    upstream = models_upstream(payload, content_type=content_type)
    async with running_gateway(upstream) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 502, json.dumps(payload)
    assert response.json()["error"]["code"] == "bad_gateway"
    assert "data" not in response.json()


@pytest.mark.parity
async def test_an_upstream_transport_failure_is_a_structured_502_not_an_empty_list() -> None:
    # Source: models.test.ts "an upstream transport failure is a structured 502, not an empty list"
    def fail(_captured: Any) -> httpx.Response:
        raise httpx.ConnectError("fetch failed")

    async with running_gateway(FakeUpstream(fail)) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "bad_gateway"
    assert "data" not in response.json()


@pytest.mark.parity
async def test_an_upstream_timeout_is_a_structured_504_not_an_empty_list() -> None:
    # Source: models.test.ts "an upstream timeout is a structured 504, not an empty list"
    async def stall(_captured: Any) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with running_gateway(FakeUpstream(stall), upstream_timeout_s=0.025) as gateway:
        response = await gateway.get("/v1/models")
    error = response.json()["error"]
    assert response.status_code == 504
    assert (error["type"], error["code"]) == ("gateway_timeout", "gateway_timeout")
    assert "data" not in response.json()


@pytest.mark.parity
async def test_a_genuine_empty_upstream_catalog_is_a_healthy_empty_list() -> None:
    # Source: models.test.ts "a genuine empty upstream catalog is a healthy empty list"
    async with running_gateway(models_upstream({"object": "list", "data": []})) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 200
    assert response.json() == {"object": "list", "data": []}


@pytest.mark.parity
async def test_model_list_deadline_covers_a_stalled_response_body() -> None:
    # Source: models-lifecycle.test.ts "model-list deadline covers a stalled response body"
    upstream = FakeUpstream()

    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b'{"data":'
            await asyncio.Event().wait()

        async def aclose(self) -> None:
            upstream.stream_closed.set()

    upstream.responder = lambda _captured: httpx.Response(
        200, headers={"content-type": "application/json"}, stream=Stalled()
    )
    async with running_gateway(upstream, upstream_timeout_s=0.05) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "gateway_timeout"
    assert upstream.stream_closed.is_set()


@pytest.mark.parity
async def test_rejected_model_list_response_releases_its_upstream_body() -> None:
    # Source: models-lifecycle.test.ts "rejected model-list response releases its upstream body"
    upstream = FakeUpstream()
    upstream.responder = lambda _captured: upstream.sse(_forever, status=503)

    async def _forever() -> AsyncIterator[bytes]:
        await asyncio.Event().wait()
        yield b""

    async with running_gateway(upstream) as gateway:
        response = await gateway.get("/v1/models")
        assert response.status_code == 502
    assert upstream.stream_closed.is_set()


@pytest.mark.parity
async def test_get_models_lists_the_registered_executors_and_the_upstream_catalog() -> None:
    # Source: shim.test.ts "GET /v1/models lists the registered executors and the upstream catalog"
    upstream = models_upstream(
        {
            "object": "list",
            "data": [
                {"id": "alpha", "object": "model"},
                {"id": "upstream-model", "object": "model"},
            ],
        }
    )
    async with running_gateway(
        upstream, executors={"alpha": local_executor, "beta": local_executor}
    ) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 200
    assert sorted(model["id"] for model in response.json()["data"]) == [
        "alpha",
        "beta",
        "upstream-model",
    ]
    assert len(upstream.captured) == 1


def route_table() -> dict[str, GatewayRoute]:
    return {
        "gw-a": GatewayRoute("http://upstream-a.test/v1", "m-a"),
        "gw-b": GatewayRoute("http://upstream-b.test/v1", "m-b", "KEY_B", ROUTE_KEY, True),
    }


@pytest.mark.parity
async def test_models_lists_the_routes_without_contacting_an_upstream() -> None:
    # Source: routes.test.ts "GET /v1/models lists the routes without contacting an upstream"
    upstream = FakeUpstream()
    async with running_gateway(upstream, routes=route_table()) as gateway:
        response = await gateway.get("/v1/models")
    assert response.status_code == 200
    assert sorted(model["id"] for model in response.json()["data"]) == ["gw-a", "gw-b"]
    assert upstream.captured == []


async def test_route_table_mode_merges_executor_ids_with_route_names() -> None:
    # E-03: the route-table catalog is the table (plus local executors), never an upstream call.
    upstream = FakeUpstream()
    async with running_gateway(
        upstream, routes=route_table(), executors={"gw-a": local_executor, "extra": local_executor}
    ) as gateway:
        response = await gateway.get("/v1/models")
    data = response.json()["data"]
    assert [model["id"] for model in data] == ["gw-a", "extra", "gw-b"]
    assert {model["owned_by"] for model in data} == {"pitwall-gateway"}
    assert upstream.captured == []


async def test_route_table_from_a_file_lists_its_routes(tmp_path: Path) -> None:
    path = tmp_path / "routes.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "routes": {"only": {"base_url": "http://only.test/v1", "model_id": "m"}},
            }
        )
    )
    config = resolve_config({"PITWALL_GATEWAY_TOKEN": "t", "PITWALL_GATEWAY_ROUTES": str(path)})
    assert config.routes is not None
    async with running_gateway(routes=config.routes) as gateway:
        response = await gateway.get("/v1/models", **AUTH)
    assert [model["id"] for model in response.json()["data"]] == ["only"]
