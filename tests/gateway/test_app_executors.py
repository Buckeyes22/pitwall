"""Executor dispatch (executor-dispatch.test.ts) and the 500-body policy (E-02, E-04).

Executors are injected through the app factory; there is no module-level registry.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pitwall.gateway import app as app_module
from pitwall.gateway.app import ExecutorContext
from pitwall.gateway.relay import RelayResult
from pitwall.gateway.routes_table import GatewayRoute
from tests.gateway.test_app_support import (
    CHAT,
    ROUTE_KEY,
    FakeUpstream,
    json_response,
    running_gateway,
)

DB_PASSWORD = "hunter2"  # pragma: allowlist secret


def answer(payload: object) -> RelayResult:
    return RelayResult(200, {"content-type": "application/json"}, json.dumps(payload).encode())


def upstream_echo() -> FakeUpstream:
    return FakeUpstream(
        lambda captured: json_response({"selected": "upstream", "body": captured.json()})
    )


async def request(gateway: Any, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = await gateway.chat(
        {**body, "messages": [{"role": "user", "content": "fixture message"}]}
    )
    return response.status_code, response.json()


@pytest.mark.parity
async def test_registered_model_dispatch_uses_the_replacement_executor() -> None:
    # Source: executor-dispatch.test.ts "registered model dispatch uses the replacement executor and clear restores upstream routing"
    seen: list[ExecutorContext] = []
    obsolete_calls: list[ExecutorContext] = []

    async def obsolete(context: ExecutorContext) -> RelayResult:
        obsolete_calls.append(context)
        return answer({"selected": "obsolete"})

    async def replacement(context: ExecutorContext) -> RelayResult:
        seen.append(context)
        return answer(
            {
                "selected": "replacement",
                "model": context.body["model"],
                "shape": context.inbound_shape,
                "policy": context.policy,
            }
        )

    # Registering the same id again replaces the earlier executor.
    executors = {"custom-model": obsolete}
    executors["custom-model"] = replacement
    upstream = upstream_echo()
    async with running_gateway(upstream, executors=executors) as gateway:
        status, body = await request(gateway, {"model": "custom-model"})
        assert status == 200
        assert body == {
            "selected": "replacement",
            "model": "custom-model",
            "shape": "openai",
            "policy": "off",
        }
        assert obsolete_calls == []
        assert len(seen) == 1
        assert upstream.captured == []
        assert seen[0].body["messages"] == [{"role": "user", "content": "fixture message"}]
        assert (await request(gateway, {"model": "other-model"}))[1]["selected"] == "upstream"
        assert len(seen) == 1

    # Without the executor (the "clear" case) the same model id reaches the upstream.
    async with running_gateway(upstream) as cleared:
        _, body = await request(cleared, {"model": "custom-model"})
    assert body["selected"] == "upstream"
    assert body["body"]["model"] == "custom-model"
    assert len(upstream.captured) == 2
    assert len(seen) == 1


@pytest.mark.parity
async def test_a_missing_or_non_string_model_id_dispatches_as_the_empty_id() -> None:
    # Source: executor-dispatch.test.ts "a missing or non-string model id dispatches as the empty id: upstream unless an executor claims it"
    upstream = upstream_echo()
    async with running_gateway(upstream) as gateway:
        missing = await request(gateway, {})
        numeric = await request(gateway, {"model": 42})
    assert [missing[0], missing[1]["selected"], numeric[0], numeric[1]["selected"]] == [
        200,
        "upstream",
        200,
        "upstream",
    ]
    calls: list[ExecutorContext] = []

    async def empty(context: ExecutorContext) -> RelayResult:
        calls.append(context)
        return answer({"selected": "empty-id"})

    async with running_gateway(upstream, executors={"": empty}) as gateway:
        assert (await request(gateway, {}))[1]["selected"] == "empty-id"
        assert (await request(gateway, {"model": 42}))[1]["selected"] == "empty-id"
        assert (await request(gateway, {"model": "named"}))[1]["selected"] == "upstream"
    assert len(calls) == 2


@pytest.mark.parity
async def test_stack_traces_never_leak() -> None:
    # Source: hardening.test.ts "hardening > stack traces never leak"
    async def boom(_context: ExecutorContext) -> RelayResult:
        raise RuntimeError("boom-from-test-executor: at function (file:///tmp/x.js:10:5)")

    async with running_gateway(executors={"__throw__": boom}) as gateway:
        response = await gateway.chat({"model": "__throw__", "messages": []})
    assert response.status_code == 500
    assert "\n  at " not in response.text
    assert "Traceback" not in response.text
    error = response.json()["error"]
    assert error["type"] == "api_error"
    assert error["request_id"] == response.headers["x-request-id"]


async def test_500_bodies_never_carry_the_exception_message() -> None:
    # E-02: the wire body is a fixed message; the exception text stays in the server log.
    secret_text = f"connection string postgres://user:{DB_PASSWORD}@db/prod"

    async def boom(_context: ExecutorContext) -> RelayResult:
        raise RuntimeError(secret_text)

    async with running_gateway(executors={"m": boom}) as gateway:
        response = await gateway.chat()
    assert response.status_code == 500
    assert DB_PASSWORD not in response.text
    assert "postgres" not in response.text
    error = response.json()["error"]
    assert (error["message"], error["code"]) == ("Internal gateway error.", "internal_server_error")


async def test_an_executor_failure_is_logged_with_its_request_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(_context: ExecutorContext) -> RelayResult:
        raise RuntimeError("logged-detail")

    with caplog.at_level("ERROR", logger="pitwall.gateway.app"):
        async with running_gateway(executors={"m": boom}) as gateway:
            response = await gateway.chat()
    assert response.status_code == 500
    record = caplog.records[-1]
    assert response.headers["x-request-id"] in record.getMessage()
    assert record.exc_info is not None


def test_there_is_no_module_level_executor_registry() -> None:
    # E-04: executors arrive through create_app(), never through module globals.
    names = {name.lower() for name in dir(app_module)}
    assert not {"register_executor", "clear_executors", "registerexecutor"} & names


async def test_executors_are_isolated_per_app() -> None:
    async def only_first(_context: ExecutorContext) -> RelayResult:
        return answer({"selected": "first"})

    async with (
        running_gateway(executors={"m": only_first}) as first,
        running_gateway() as second,
    ):
        assert (await first.chat(CHAT)).json() == {"selected": "first"}
        assert (await second.chat(CHAT)).json()["id"] == "x"


async def test_the_executor_receives_the_routed_target_and_pinned_model() -> None:
    seen: list[ExecutorContext] = []

    async def capture(context: ExecutorContext) -> RelayResult:
        seen.append(context)
        return answer({"ok": True})

    routes = {"gw": GatewayRoute("http://up.test/v1", "pinned-model", "K", ROUTE_KEY, True)}
    async with running_gateway(routes=routes, executors={"pinned-model": capture}) as gateway:
        response = await gateway.chat(
            {"model": "ignored", "messages": []}, **{"x-pitwall-route": "gw"}
        )
    assert response.json() == {"ok": True}
    assert seen[0].body["model"] == "pinned-model"
    assert seen[0].target.base_url == "http://up.test/v1"
    assert seen[0].target.api_key == ROUTE_KEY
    assert seen[0].request_id == response.headers["x-request-id"]
    assert gateway.upstream.captured == []
