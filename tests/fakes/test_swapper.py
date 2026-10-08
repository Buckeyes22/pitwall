from __future__ import annotations

import asyncio

import httpx
import pytest

from tests.fakes.swapper import FakeClock, FakeSwapperApp, FakeSwapperModel

_BASE_URL = "http://localhost:9292"
_HEADERS = {"Authorization": "Bearer <api-key>"}


def _model(
    model_id: str,
    *,
    slot_group: str | None = "slot-a",
    exclusive: bool = False,
    tool_calling: bool = True,
    load_delay_s: float = 10,
    fail_start_after_s: float | None = None,
) -> FakeSwapperModel:
    return FakeSwapperModel(
        id=model_id,
        slot_group=slot_group,
        exclusive=exclusive,
        tool_calling=tool_calling,
        load_delay_s=load_delay_s,
        fail_start_after_s=fail_start_after_s,
    )


def _client(app: FakeSwapperApp) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=_BASE_URL,
        headers=_HEADERS,
    )


@pytest.mark.anyio
async def test_first_request_reports_starting_then_ready_without_real_sleep() -> None:
    clock = FakeClock()
    app = FakeSwapperApp(
        catalogue={"<model-id>": _model("<model-id>")},
        api_key="<api-key>",
        clock=clock,
    )
    async with _client(app) as client:
        request = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()

        models = await client.get("/v1/models")
        running = await client.get("/running")
        assert [item["id"] for item in models.json()["data"]] == ["<model-id>"]
        assert running.json() == [{"model": "<model-id>", "state": "starting"}]

        clock.advance(10)
        response = await request
        assert response.status_code == 200
        assert (await client.get("/running")).json() == [{"model": "<model-id>", "state": "ready"}]


@pytest.mark.anyio
async def test_bad_bearer_is_unauthorized() -> None:
    app = FakeSwapperApp(
        catalogue={"<model-id>": _model("<model-id>")},
        api_key="<api-key>",
    )
    async with _client(app) as client:
        response = await client.get(
            "/v1/models",
            headers={"Authorization": "Bearer <wrong-key>"},
        )
    assert response.status_code == 401


@pytest.mark.anyio
async def test_catalogue_can_change_from_empty_to_populated() -> None:
    app = FakeSwapperApp(catalogue={}, api_key="<api-key>")
    async with _client(app) as client:
        assert (await client.get("/v1/models")).json() == {
            "object": "list",
            "data": [],
        }
        app.catalogue["<model-id>"] = _model("<model-id>", load_delay_s=0)
        response = await client.post(
            "/v1/chat/completions",
            json={"model": "<model-id>", "messages": []},
        )
        assert response.status_code == 200
        assert [row["id"] for row in (await client.get("/v1/models")).json()["data"]] == [
            "<model-id>"
        ]


@pytest.mark.anyio
async def test_slot_peer_load_evicts_ready_peer() -> None:
    app = FakeSwapperApp(
        catalogue={
            "<model-a>": _model("<model-a>", load_delay_s=0),
            "<model-b>": _model("<model-b>", load_delay_s=0),
        },
        api_key="<api-key>",
    )
    async with _client(app) as client:
        for model_id in ("<model-a>", "<model-b>"):
            assert (
                await client.post(
                    "/v1/chat/completions",
                    json={"model": model_id, "messages": []},
                )
            ).status_code == 200
        assert (await client.get("/running")).json() == [{"model": "<model-b>", "state": "ready"}]


@pytest.mark.anyio
async def test_exclusive_load_evicts_all_but_reload_is_asymmetric() -> None:
    app = FakeSwapperApp(
        catalogue={
            "<model-a>": _model("<model-a>", slot_group="slot-a", load_delay_s=0),
            "<model-b>": _model("<model-b>", slot_group="slot-b", load_delay_s=0),
            "<model-x>": _model("<model-x>", slot_group=None, exclusive=True, load_delay_s=0),
        },
        api_key="<api-key>",
    )
    async with _client(app) as client:
        for model_id in ("<model-a>", "<model-b>", "<model-x>", "<model-a>"):
            response = await client.post(
                "/v1/chat/completions",
                json={"model": model_id, "messages": []},
            )
            assert response.status_code == 200
        assert (await client.get("/running")).json() == [
            {"model": "<model-a>", "state": "ready"},
            {"model": "<model-x>", "state": "ready"},
        ]


@pytest.mark.anyio
async def test_force_unload_and_config_edit_both_reset_to_cold() -> None:
    original = _model("<model-id>", load_delay_s=0)
    app = FakeSwapperApp(catalogue={original.id: original}, api_key="<api-key>")
    async with _client(app) as client:
        payload = {"model": "<model-id>", "messages": []}
        assert (await client.post("/v1/chat/completions", json=payload)).status_code == 200
        assert (await client.post("/api/models/unload/%3Cmodel-id%3E")).status_code == 204
        assert (await client.get("/running")).json() == []

        assert (await client.post("/v1/chat/completions", json=payload)).status_code == 200
        app.configure_edit(_model("<model-id>", load_delay_s=0))
        assert (await client.get("/running")).json() == []


@pytest.mark.anyio
async def test_disabled_tool_calling_rejects_auto_tool_choice() -> None:
    app = FakeSwapperApp(
        catalogue={"<model-id>": _model("<model-id>", tool_calling=False)},
        api_key="<api-key>",
    )
    async with _client(app) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": "<model-id>",
                "messages": [],
                "tools": [{"type": "function", "function": {"name": "probe"}}],
                "tool_choice": "auto",
            },
        )
    assert response.status_code == 400


@pytest.mark.anyio
async def test_concurrency_limit_returns_429_while_first_request_blocks() -> None:
    clock = FakeClock()
    app = FakeSwapperApp(
        catalogue={"<model-id>": _model("<model-id>")},
        api_key="<api-key>",
        clock=clock,
        concurrency_limit=1,
    )
    async with _client(app) as client:
        first = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()
        second = await client.post(
            "/v1/chat/completions",
            json={"model": "<model-id>", "messages": []},
        )
        assert second.status_code == 429
        clock.advance(10)
        assert (await first).status_code == 200


@pytest.mark.anyio
async def test_start_failure_returns_5xx_before_normal_load_delay() -> None:
    clock = FakeClock()
    app = FakeSwapperApp(
        catalogue={"<model-id>": _model("<model-id>", load_delay_s=300, fail_start_after_s=7)},
        api_key="<api-key>",
        clock=clock,
    )
    async with _client(app) as client:
        request = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()
        clock.advance(7)
        response = await request
    assert response.status_code == 503
