"""A-21: inbound rate-limit buckets are bounded and idle buckets expire."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.conftest import _env_for_app, _import_app

pytestmark = pytest.mark.anyio


def _scope(ip: str) -> dict[str, object]:
    return {"type": "http", "path": "/v1/x", "headers": [], "client": (ip, 1234)}


async def test_idle_buckets_evicted(
    clear_app_module: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    mod = _import_app(_env_for_app(PITWALL_INBOUND_RATE_LIMIT="5/10s"))
    now = [1000.0]
    monkeypatch.setattr(mod, "time", SimpleNamespace(monotonic=lambda: now[0]))
    middleware = mod.InboundRateLimitMiddleware(
        app=None,
        config=mod.InboundRateLimitConfig(requests=5, window_s=10.0),
        authorizer=mod.BearerTokenAuthorizer(None, None),
        max_buckets=3,
    )

    # The fixed maximum bounds memory: the least recently used bucket is dropped.
    for index in range(10):
        assert await middleware._retry_after_s(_scope(f"10.0.0.{index}")) is None
    assert len(middleware._buckets) == 3

    # Idle expiry: buckets untouched for longer than the window are removed.
    now[0] += 11.0
    assert await middleware._retry_after_s(_scope("10.9.9.9")) is None
    assert len(middleware._buckets) == 1


@pytest.mark.parametrize("window_s", [float("nan"), float("inf"), float("-inf"), 0.0])
async def test_inbound_rate_limit_config_rejects_non_finite_windows(
    clear_app_module: None, window_s: float
) -> None:
    mod = _import_app(_env_for_app())
    with pytest.raises(ValueError, match="window_s"):
        mod.InboundRateLimitConfig(requests=5, window_s=window_s)
