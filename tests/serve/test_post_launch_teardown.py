"""Every failure after a paid pod launches tears the pod down and re-raises."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pitwall import serve
from pitwall.api.exceptions import ServeLaunchFailed, ServeVerificationFailed
from pitwall.config import PitwallSettings
from pitwall.models.lookup import VariantInfo
from tests.serve.test_serve_model import (
    Catalogue,
    Repos,
    _capability,
    _install_repos,
    _lease,
    _request,
)

pytestmark = pytest.mark.anyio


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    repos: Repos,
    *,
    verify: Any = None,
    launch_result: dict[str, Any] | None = None,
) -> AsyncMock:
    monkeypatch.delenv("REDIS_URL", raising=False)
    _install_repos(monkeypatch, repos)
    monkeypatch.setattr(
        serve,
        "run_launch",
        AsyncMock(return_value=launch_result or {"lease_id": "lease-serve-1"}),
    )
    monkeypatch.setattr(
        serve,
        "verify_served_model",
        verify if verify is not None else AsyncMock(return_value=["org/model"]),
    )
    teardown = AsyncMock()
    monkeypatch.setattr(serve, "run_teardown", teardown)
    return teardown


def _fp8_info() -> VariantInfo:
    return VariantInfo(
        variant_id="fp8",
        engine="vllm",
        image="example/model-server:test",
        repo="org/model",
        file=None,
        flags=(),
        companions=(),
        evidence=None,
        openai_chat=True,
        env={},
        container_disk_gb=40,
        startup_min=15,
        gated=False,
    )


async def _serve(**kwargs: Any) -> Any:
    return await serve.serve_model(
        object(),
        _request(**kwargs.pop("request", {})),
        base_url="http://127.0.0.1:8080",
        settings=PitwallSettings(),
        **kwargs,
    )


def _reason(teardown: AsyncMock) -> str:
    teardown.assert_awaited_once()
    assert teardown.await_args.args == ("lease-serve-1",)
    return str(teardown.await_args.kwargs["reason"])


async def test_lease_not_persisted_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    teardown = _wire(monkeypatch, Repos(_capability(), None, None))

    with pytest.raises(ServeLaunchFailed, match="was not persisted"):
        await _serve()

    assert _reason(teardown) == "serve_lease_not_persisted_failed"


async def test_no_proxy_url_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    lease = _lease().model_copy(update={"runpod_pod_id": None})
    teardown = _wire(monkeypatch, Repos(_capability(), None, lease))

    with pytest.raises(ServeLaunchFailed, match="no valid pod proxy URL"):
        await _serve()

    assert _reason(teardown) == "serve_proxy_url_failed"


async def test_warm_cache_patch_failure_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    repos = Repos(_capability(), None, _lease())
    teardown = _wire(monkeypatch, repos)
    original_patch = repos.provider_patch

    async def failing_patch(provider_id: str, **kwargs: Any) -> Any:
        if "warm_cache" in kwargs.get("config", {}):
            raise RuntimeError("database went away")
        return await original_patch(provider_id, **kwargs)

    monkeypatch.setattr(repos, "provider_patch", failing_patch)

    with pytest.raises(RuntimeError, match="database went away"):
        await _serve(
            request={"network_volume_id": "volume-cache"},
            catalogue=Catalogue(_fp8_info()),
            warm_only=True,
        )

    assert _reason(teardown) == "serve_warm_cache_failed"


async def test_cancelled_during_verify_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    teardown = _wire(
        monkeypatch,
        Repos(_capability(), None, _lease()),
        verify=AsyncMock(side_effect=asyncio.CancelledError),
    )

    with pytest.raises(asyncio.CancelledError):
        await _serve()

    assert _reason(teardown) == "serve_verify_cancelled"


async def test_unexpected_verify_error_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    teardown = _wire(
        monkeypatch,
        Repos(_capability(), None, _lease()),
        verify=AsyncMock(side_effect=ValueError("boom")),
    )

    with pytest.raises(ValueError, match="boom"):
        await _serve()

    assert _reason(teardown) == "serve_verify_failed"


async def test_verification_failure_keeps_mismatch_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    teardown = _wire(
        monkeypatch,
        Repos(_capability(), None, _lease()),
        verify=AsyncMock(side_effect=ServeVerificationFailed("org/model", [])),
    )

    with pytest.raises(ServeVerificationFailed):
        await _serve()

    assert _reason(teardown) == "served_model_mismatch"


async def test_teardown_failure_does_not_mask_original_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teardown = _wire(
        monkeypatch,
        Repos(_capability(), None, _lease()),
        verify=AsyncMock(side_effect=ValueError("boom")),
    )
    teardown.side_effect = RuntimeError("teardown broke")

    with pytest.raises(ValueError, match="boom"):
        await _serve()
