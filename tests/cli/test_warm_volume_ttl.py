"""warm-volume must ask for a lease that outlasts the model's startup budget."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.cli import output as cli_output
from pitwall.cli import warm_volume as cli_warm_volume
from pitwall.models import load_catalogue

_MODEL = "ornith-ai/Ornith-1.5-35B-A3B-GGUF"


async def test_warm_volume_requests_a_ttl_beyond_the_startup_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[Any] = []

    class _Result:
        def to_dict(self) -> dict[str, Any]:
            return {"ok": True}

    async def serve_model(_pool: Any, request: Any, **_kwargs: Any) -> _Result:
        seen.append(request)
        return _Result()

    async def get_pool() -> None:
        return None

    monkeypatch.setattr("pitwall.serve.serve_model", serve_model)
    monkeypatch.setattr("pitwall.db.get_pool", get_pool)
    monkeypatch.setattr(cli_warm_volume, "guard_cli_pre_spend", lambda *_a, **_k: None)

    args = cli_warm_volume._parse_warm_volume_args(
        [
            "--model",
            _MODEL,
            "--volume-id",
            "vol1",
            "--gpu-class",
            "NVIDIA GeForce RTX 4090",
            "--dry-run",
            "--json",
        ]
    )
    assert await cli_warm_volume._warm_volume_async(args, cli_output.Output(True)) == 0

    variant = load_catalogue().get(_MODEL).resolve_variant(None)
    assert seen[0].ttl_minutes > variant.startup_min
