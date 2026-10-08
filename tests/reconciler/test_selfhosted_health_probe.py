from __future__ import annotations

import asyncio
import json
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

import pitwall.reconciler as reconciler
from pitwall.providers.selfhosted import SelfHostedState
from tests.fakes.swapper import FakeClock, FakeSwapperApp, FakeSwapperModel

_NOW = datetime(2026, 8, 29, 12, tzinfo=UTC)
_BASE_URL = "http://localhost:9292/v1"
_ORIGIN = "http://localhost:9292"


class _Acquire(AbstractAsyncContextManager["_Connection"]):
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _Connection:
        return self.connection

    async def __aexit__(self, *args: object) -> None:
        return None


class _Connection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def fetch(self, _sql: str, provider_types: list[str]) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.rows
            if row["provider_type"] in provider_types and row["enabled"]
        ]

    async def execute(self, _sql: str, *args: object) -> str:
        provider_id, status, failures, trips, until, state_json, expected_updated_at = args
        row = next(item for item in self.rows if item["id"] == provider_id)
        if expected_updated_at is not None and row["updated_at"] != expected_updated_at:
            return "UPDATE 0"  # compare-and-set miss, as Postgres reports it
        row.update(
            health_status=status,
            consecutive_failures=failures,
            cooldown_trips=trips,
            cooldown_until=until,
            updated_at=row["updated_at"] + timedelta(microseconds=1),
        )
        if state_json is not None:
            row["config"] = dict(row["config"])
            row["config"]["self_hosted_state"] = json.loads(str(state_json))
        return "UPDATE 1"


class _HealthPool:
    def __init__(self, row: dict[str, Any] | list[dict[str, Any]]) -> None:
        self.connection = _Connection(row if isinstance(row, list) else [row])

    def acquire(self) -> _Acquire:
        return _Acquire(self.connection)


def _row(
    *,
    provider_id: str = "provider-selfhosted",
    api_key_env: str = "PITWALL_TEST_SELFHOSTED_KEY",
) -> dict[str, Any]:
    return {
        "id": provider_id,
        "capability_id": "capability-chat",
        "name": "self-hosted fixture",
        "provider_type": "public_endpoint",
        "runpod_endpoint_id": "endpoint-fixture",
        "served_model_id": "<model-id>",
        "config": {
            "openai_base_url": _BASE_URL,
            "api_key_env": api_key_env,
            "self_hosted": {
                "readiness": {"kind": "llama-swap"},
                "cold_start_timeout_s": 330,
                "models": [{"id": "<model-id>", "tool_calling": "enabled"}],
            },
        },
        "priority": 1,
        "enabled": True,
        "health_status": "healthy",
        "consecutive_failures": 0,
        "cooldown_trips": 0,
        "cooldown_until": None,
        "updated_at": _NOW,
    }


def _settings() -> SimpleNamespace:
    return SimpleNamespace(pitwall_endpoint_probe_timeout_s=10)


async def _persisted(pool: _HealthPool) -> dict[str, Any]:
    rows = await reconciler.fetch_providers_for_health_probe(pool)
    return rows[0]


@pytest.mark.anyio
async def test_dead_endpoint_trips_cooldown_then_probe_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "<api-key>")
    pool = _HealthPool(_row())

    def refuse(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("endpoint unavailable")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as dead:
        for offset in range(3):
            await reconciler._health_probe(
                {
                    "db_pool": pool,
                    "http_client": dead,
                    "settings": _settings(),
                    "now": _NOW + timedelta(seconds=offset),
                }
            )

    cooling = await _persisted(pool)
    assert cooling["health_status"] == "unhealthy"
    assert cooling["consecutive_failures"] == 3
    assert cooling["cooldown_until"] == _NOW + timedelta(minutes=5, seconds=2)

    app = FakeSwapperApp(
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>", load_delay_s=0)},
        api_key="<api-key>",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=_ORIGIN) as live:
        await live.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer <api-key>"},
            json={"model": "<model-id>", "messages": []},
        )
        await reconciler._health_probe(
            {
                "db_pool": pool,
                "http_client": live,
                "settings": _settings(),
                "now": _NOW + timedelta(minutes=5, seconds=3),
            }
        )

    recovered = await _persisted(pool)
    assert recovered["health_status"] == "healthy"
    assert recovered["consecutive_failures"] == 0
    assert recovered["cooldown_until"] is None
    state = recovered["config"]["self_hosted_state"]
    assert state["resident"] == ["<model-id>"]
    assert state["tool_calling"] == "enabled"
    assert set(state) == set(SelfHostedState.__annotations__) - {
        "warming_since",
        "warming_timeout_charged",
        "concurrency_limited",
    }


@pytest.mark.anyio
async def test_starting_is_warming_without_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "<api-key>")
    clock = FakeClock()
    app = FakeSwapperApp(
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>", load_delay_s=10)},
        api_key="<api-key>",
        clock=clock,
    )
    pool = _HealthPool(_row())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_ORIGIN
    ) as client:
        warm = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer <api-key>"},
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()
        await reconciler._health_probe(
            {
                "db_pool": pool,
                "http_client": client,
                "settings": _settings(),
                "now": _NOW,
            }
        )
        state = await _persisted(pool)
        assert state["health_status"] == "warming"
        assert state["consecutive_failures"] == 0
        assert state["cooldown_until"] is None
        assert state["config"]["self_hosted_state"]["resident"] == []
        assert state["config"]["self_hosted_state"]["warming_since"] == _NOW.isoformat()
        assert state["config"]["self_hosted_state"]["warming_timeout_charged"] is False
        clock.advance(10)
        assert (await warm).status_code == 200


@pytest.mark.anyio
async def test_warming_timeout_is_charged_once_per_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "<api-key>")
    row = _row()
    timeout_s = 10
    row["config"]["self_hosted"]["cold_start_timeout_s"] = timeout_s
    clock = FakeClock()
    app = FakeSwapperApp(
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>", load_delay_s=300)},
        api_key="<api-key>",
        clock=clock,
    )
    pool = _HealthPool(row)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_ORIGIN
    ) as client:
        warm = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer <api-key>"},
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()
        for offset in (0, timeout_s // 2, timeout_s + 1, timeout_s + 60, timeout_s + 120):
            await reconciler._health_probe(
                {
                    "db_pool": pool,
                    "http_client": client,
                    "settings": _settings(),
                    "now": _NOW + timedelta(seconds=offset),
                }
            )

        charged = await _persisted(pool)
        assert charged["health_status"] == "warming"
        assert charged["consecutive_failures"] == 1
        charged_marker = charged["config"]["self_hosted_state"]
        assert charged_marker["warming_since"] == _NOW.isoformat()
        assert charged_marker["warming_timeout_charged"] is True

        clock.advance(300)
        assert (await warm).status_code == 200
        await reconciler._health_probe(
            {
                "db_pool": pool,
                "http_client": client,
                "settings": _settings(),
                "now": _NOW + timedelta(seconds=timeout_s + 121),
            }
        )
        ready = await _persisted(pool)
        assert ready["health_status"] == "healthy"
        assert ready["consecutive_failures"] == 0
        assert "warming_since" not in ready["config"]["self_hosted_state"]
        assert "warming_timeout_charged" not in ready["config"]["self_hosted_state"]

        app.configure_edit(FakeSwapperModel(id="<model-id>", load_delay_s=300))
        second_warm = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer <api-key>"},
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()
        episode_start = _NOW + timedelta(seconds=timeout_s + 122)
        for offset in (0, timeout_s + 1):
            await reconciler._health_probe(
                {
                    "db_pool": pool,
                    "http_client": client,
                    "settings": _settings(),
                    "now": episode_start + timedelta(seconds=offset),
                }
            )
        charged_again = await _persisted(pool)
        assert charged_again["consecutive_failures"] == 1
        assert charged_again["config"]["self_hosted_state"]["warming_timeout_charged"] is True
        second_warm.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second_warm


@pytest.mark.anyio
async def test_provider_level_empty_catalogue_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "<api-key>")
    row = _row()
    row["served_model_id"] = None
    row["config"].pop("self_hosted")
    pool = _HealthPool(row)
    app = FakeSwapperApp(catalogue={}, api_key="<api-key>")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_ORIGIN
    ) as client:
        await reconciler._health_probe(
            {"db_pool": pool, "http_client": client, "settings": _settings(), "now": _NOW}
        )
    state = await _persisted(pool)
    assert state["health_status"] == "healthy"
    assert state["consecutive_failures"] == 0


@pytest.mark.anyio
async def test_starting_model_that_becomes_absent_records_failure_and_clears_episode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "<api-key>")
    clock = FakeClock()
    app = FakeSwapperApp(
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>", fail_start_after_s=1)},
        api_key="<api-key>",
        clock=clock,
    )
    pool = _HealthPool(_row())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_ORIGIN
    ) as client:
        warm = asyncio.create_task(
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer <api-key>"},
                json={"model": "<model-id>", "messages": []},
            )
        )
        await clock.checkpoint()
        await reconciler._health_probe(
            {"db_pool": pool, "http_client": client, "settings": _settings(), "now": _NOW}
        )
        clock.advance(1)
        assert (await warm).status_code == 503
        await reconciler._health_probe(
            {
                "db_pool": pool,
                "http_client": client,
                "settings": _settings(),
                "now": _NOW + timedelta(seconds=1),
            }
        )
    state = await _persisted(pool)
    assert state["health_status"] == "warming"
    assert state["consecutive_failures"] == 1
    assert "warming_since" not in state["config"]["self_hosted_state"]
    assert "warming_timeout_charged" not in state["config"]["self_hosted_state"]


@pytest.mark.anyio
async def test_provider_probe_exception_is_isolated_to_that_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "<api-key>")
    first = _row(provider_id="provider-first")
    second = _row(provider_id="provider-second")
    pool = _HealthPool([first, second])
    app = FakeSwapperApp(catalogue={}, api_key="<api-key>")
    original_probe = reconciler._probe_public_endpoint

    async def probe_with_first_failure(
        prov: dict[str, Any],
        *,
        client: httpx.AsyncClient,
        settings: Any,
        now: datetime,
    ) -> tuple[reconciler.ProbeClassification, reconciler.ReadinessObservation]:
        if prov["id"] == "provider-first":
            raise RuntimeError("malformed provider fixture")
        return await original_probe(prov, client=client, settings=settings, now=now)

    monkeypatch.setattr(reconciler, "_probe_public_endpoint", probe_with_first_failure)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_ORIGIN
    ) as client:
        await reconciler._health_probe(
            {"db_pool": pool, "http_client": client, "settings": _settings(), "now": _NOW}
        )

    assert first["consecutive_failures"] == 1
    assert second["health_status"] == "healthy"
    assert second["consecutive_failures"] == 0


@pytest.mark.anyio
async def test_401_is_misconfigured_without_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PITWALL_TEST_SELFHOSTED_KEY", "<wrong-key>")
    app = FakeSwapperApp(
        catalogue={"<model-id>": FakeSwapperModel(id="<model-id>")},
        api_key="<api-key>",
    )
    pool = _HealthPool(_row())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=_ORIGIN
    ) as client:
        await reconciler._health_probe(
            {
                "db_pool": pool,
                "http_client": client,
                "settings": _settings(),
                "now": _NOW,
            }
        )

    state = await _persisted(pool)
    assert state["health_status"] == "misconfigured"
    assert state["consecutive_failures"] == 0
    assert state["cooldown_until"] is None


@pytest.mark.anyio
async def test_fetch_filter_is_parameterized_for_both_provider_types() -> None:
    pool = _HealthPool(_row())
    rows = await reconciler.fetch_providers_for_health_probe(
        pool,
        provider_types=("public_endpoint",),
    )
    assert [row["id"] for row in rows] == ["provider-selfhosted"]
