"""Task 10: /v1/quotas and /v1/admin/quotas/refresh route contracts.

Read scope gates the listing; SERVER_ADMIN scope gates the refresh. Both routes
read from a QuotaRepository injected via ``app.state.quota_repository`` (the
same seam used by ``tests/api/test_cost_read_routes.py`` for the cost read
model) and fall back to ``QuotaRepository(pool)`` when nothing is bound.
"""

from __future__ import annotations

import datetime as dt
import importlib
import json
import os
import sys
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from pitwall.db.quota_repository import ModelIdMapping
from pitwall.routing.quota import QuotaRecord

pytestmark = pytest.mark.anyio


def _purge_api_modules() -> None:
    for key in [key for key in sys.modules if key.startswith("pitwall.api")]:
        del sys.modules[key]


def _scoped_env() -> dict[str, str]:
    return {
        "RUNPOD_API_KEY": "test-key",
        "DATABASE_URL": "postgresql://u:p@localhost/db",
        "REDIS_URL": "redis://localhost:6379/0",
        "PITWALL_ADMIN_SECRET": "test-admin-secret",
        "PITWALL_API_SCOPED_TOKENS": json.dumps(
            {
                "read-token": ["read"],
                "admin-token": ["server:admin"],
                "spend-token": ["read", "spend"],
            }
        ),
    }


def _import_app(env: dict[str, str]) -> Any:
    old = os.environ.copy()
    os.environ.update(env)
    for key in (
        "RUNPOD_API_KEY",
        "DATABASE_URL",
        "REDIS_URL",
        "PITWALL_ADMIN_SECRET",
        "PITWALL_API_TOKEN",
        "PITWALL_INBOUND_RATE_LIMIT",
    ):
        if key not in env:
            os.environ.pop(key, None)
    try:
        return importlib.import_module("pitwall.api.app")
    finally:
        os.environ.clear()
        os.environ.update(old)


def _record(**overrides: Any) -> QuotaRecord:
    now = dt.datetime.now(dt.UTC)
    base: dict[str, Any] = {
        "provider_id": "prov_gw_alpha",
        "pool_key": "alpha-pool",
        "free_type": "recurring-monthly",
        "window_start": now - dt.timedelta(days=10),
        "reset_at": now + dt.timedelta(days=20),
        "budget_units": Decimal("5000000"),
        "used_units": Decimal("1000000"),
        "tos_verdict": "ok",
        "evidence": {},
        "updated_at": now - dt.timedelta(minutes=1),
    }
    base.update(overrides)
    return QuotaRecord(**base)


def test_quota_fixture_window_remains_current_after_2026(monkeypatch: pytest.MonkeyPatch) -> None:
    class FutureDatetime(dt.datetime):
        @classmethod
        def now(cls, tz: dt.tzinfo | None = None) -> dt.datetime:
            return cls(2035, 2, 1, tzinfo=dt.UTC)

    frozen_now = FutureDatetime.now(dt.UTC)
    monkeypatch.setattr(
        sys.modules[__name__],
        "dt",
        SimpleNamespace(datetime=FutureDatetime, UTC=dt.UTC, timedelta=dt.timedelta),
    )
    record = _record()
    assert record.window_start < frozen_now < record.reset_at


class _FakeQuotaRepo:
    """Stub matching the QuotaRepository surface the routes consume."""

    def __init__(
        self,
        records: list[QuotaRecord] | None = None,
        *,
        model_ids: list[ModelIdMapping] | None = None,
    ) -> None:
        self._records = list(records or [])
        self._model_ids = list(model_ids or [])
        self.upsert_calls: list[QuotaRecord] = []
        self.refresh_count = 0

    async def list_all(self) -> tuple[QuotaRecord, ...]:
        return tuple(self._records)

    async def list_model_ids(self) -> tuple[ModelIdMapping, ...]:
        return tuple(self._model_ids)

    async def upsert(self, record: QuotaRecord) -> None:
        self.upsert_calls.append(record)

    async def upsert_model_id(self, model_id: str, capability: str, provider: str) -> None:
        self._model_ids.append(ModelIdMapping(model_id, capability, provider))

    async def add_usage(self, provider_id: str, pool_key: str, units: Decimal) -> None:
        return None

    async def record_sample(
        self,
        provider_id: str,
        sampled_at: dt.datetime,
        used_units: Decimal,
        reset_at: dt.datetime | None,
    ) -> None:
        self.refresh_count += 1


@pytest.fixture(autouse=True)
def _clear_app_modules() -> None:
    _purge_api_modules()
    yield
    _purge_api_modules()


def _build_app(repo: _FakeQuotaRepo) -> Any:
    env = _scoped_env()
    mod = _import_app(env)
    mod.app.state.pool = MagicMock()
    mod.app.state.quota_repository = repo
    return mod


async def test_quotas_read_requires_read_scope_and_lists_headroom() -> None:
    repo = _FakeQuotaRepo(records=[_record()])
    mod = _build_app(repo)
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/quotas",
            headers={"Authorization": "Bearer read-token"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert list(body) == ["quotas"]
    [row] = body["quotas"]
    assert row["tos_verdict"] == "ok"
    assert row["provider_id"] == "prov_gw_alpha"
    # (5_000_000 - 1_000_000) / 5_000_000 == 0.8
    assert row["headroom"] == pytest.approx(0.8)
    assert row["lockout"] is None


async def test_quotas_read_rejects_token_without_read_scope() -> None:
    mod = _build_app(_FakeQuotaRepo(records=[_record()]))
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # No bearer at all -> 401
        no_token = await client.get("/v1/quotas")
        # admin token alone has the required read scope through ALL_API_SCOPES? It must.
        # Use a scoped-token that explicitly grants no read scope: build env without read
        admin_token = "admin-token"
        response = await client.get(
            "/v1/quotas",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert no_token.status_code == 401
    # admin-token holds server:admin, not read by default — _required_scope returns
    # READ_SCOPE for /v1/quotas, so this MUST 403.
    assert response.status_code == 403


async def test_admin_refresh_needs_server_admin_scope() -> None:
    mod = _build_app(_FakeQuotaRepo(records=[_record(), _record(pool_key="beta-pool")]))
    transport = httpx.ASGITransport(app=mod.app)
    secret_headers = {"X-Pitwall-Secret": "test-admin-secret"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post(
            "/v1/admin/quotas/refresh",
            headers={"Authorization": "Bearer read-token", **secret_headers},
        )
        allowed = await client.post(
            "/v1/admin/quotas/refresh",
            headers={"Authorization": "Bearer admin-token", **secret_headers},
        )
    assert denied.status_code == 403, denied.text
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["refreshed"] == 2


async def test_quotas_lockout_is_reported_when_a_pool_is_exhausted() -> None:
    exhausted = _record(
        provider_id="prov_gw_b",
        pool_key="beta-pool",
        used_units=Decimal("5000000"),
        budget_units=Decimal("5000000"),
        free_type="recurring-monthly",
    )
    mod = _build_app(_FakeQuotaRepo(records=[exhausted]))
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/v1/quotas",
            headers={"Authorization": "Bearer read-token"},
        )
    assert response.status_code == 200
    [row] = response.json()["quotas"]
    assert row["lockout"] == {"reason": "quota-exhausted"}
    assert row["headroom"] == pytest.approx(0.0)


async def test_quotas_report_no_lockout_once_the_window_has_rolled() -> None:
    rolled = _record(
        provider_id="prov_gw_c",
        pool_key="gamma-pool",
        used_units=Decimal("5000000"),
        budget_units=Decimal("5000000"),
        free_type="recurring-monthly",
        reset_at=dt.datetime(2000, 1, 1, tzinfo=dt.UTC),
    )
    mod = _build_app(_FakeQuotaRepo(records=[rolled]))
    transport = httpx.ASGITransport(app=mod.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/v1/quotas", headers={"Authorization": "Bearer read-token"})
    assert response.status_code == 200
    [row] = response.json()["quotas"]
    assert row["lockout"] is None
