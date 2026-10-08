"""Tests for lb_endpoint_hibernate_sweep — daily sweep that does NOT auto-hibernate.

Per the L14 invariant: workersMin > 0 on a hibernated LB endpoint triggers an alert;
the sweep must NOT auto-hibernate (operator decision; alert is the action). The sweep
legitimately reads endpoints and tracks warm duration; the invariant under
test is that it never issues a workersMin-mutating RunPod call.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pitwall.reconciler import _lb_endpoint_hibernate_sweep

pytestmark = pytest.mark.anyio


def _warm_provider() -> dict:
    return {"id": "prov-1", "name": "lb-1", "runpod_endpoint_id": "ep-1"}


async def test_sweep_runs_without_error() -> None:
    """The sweep must complete without raising even with an empty context."""
    ctx: dict = {}
    await _lb_endpoint_hibernate_sweep(ctx)
    await _lb_endpoint_hibernate_sweep(ctx)
    assert True


async def test_sweep_does_not_auto_hibernate(monkeypatch) -> None:
    """L14: against a warm (workersMin>0) endpoint the sweep must NOT auto-hibernate.

    Reaching the end of the run with only the canonical read helper mocked proves the
    sweep only reads/tracks and never called the mutating hibernate helper.
    """
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)  # no prior obs -> no warm-duration -> no alert
    redis.set = AsyncMock()
    ctx: dict = {"db_pool": MagicMock(), "redis": redis}

    with (
        patch(
            "pitwall.reconciler.fetch_lb_providers_for_hibernate_sweep",
            AsyncMock(return_value=[_warm_provider()]),
        ),
        patch(
            "pitwall.reconciler.get_endpoint",
            AsyncMock(
                return_value=MagicMock(
                    scaling=MagicMock(workers_min=3),
                )
            ),
        ) as get_endpoint,
        patch("pitwall.runpod_client.endpoints.hibernate_endpoint") as hibernate_endpoint,
    ):
        await _lb_endpoint_hibernate_sweep(ctx)
    get_endpoint.assert_awaited_once_with("ep-1", timeout_s=30.0)
    hibernate_endpoint.assert_not_called()
    redis.set.assert_awaited_once()


async def test_sweep_skips_when_no_pool() -> None:
    """The sweep must not fail when db_pool is absent from context."""
    ctx: dict = {}
    await _lb_endpoint_hibernate_sweep(ctx)
    assert True


async def test_sweep_skips_when_redis_missing(monkeypatch) -> None:
    """The sweep must not fail when redis is absent from context."""
    monkeypatch.setenv("RUNPOD_API_KEY", "test-key")
    ctx: dict = {"db_pool": MagicMock()}  # no "redis" key
    with (
        patch(
            "pitwall.reconciler.fetch_lb_providers_for_hibernate_sweep",
            AsyncMock(return_value=[_warm_provider()]),
        ),
        patch(
            "pitwall.reconciler.get_endpoint",
            AsyncMock(
                return_value=MagicMock(
                    scaling=MagicMock(workers_min=3),
                )
            ),
        ),
    ):
        await _lb_endpoint_hibernate_sweep(ctx)
