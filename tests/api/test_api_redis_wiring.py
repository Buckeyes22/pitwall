from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_API = _ROOT / "src" / "pitwall" / "api"


def test_the_api_app_attaches_redis_to_app_state() -> None:
    """Five call sites read app.state.redis; something must set it.

    The proxy stamps lease traffic only when app.state.redis is not None, and
    stamp_lease_traffic returns silently when it is. Nothing in the API set the
    attribute, so activity renewal and idle stop could never fire.
    """
    sources = "\n".join(path.read_text() for path in _API.rglob("*.py"))
    assert "app.state.redis =" in sources or "state.redis =" in sources, (
        "no module under src/pitwall/api assigns app.state.redis"
    )


def test_redis_health_reports_the_client_the_request_path_uses() -> None:
    """/health must not pass by building a client no feature uses."""
    source = (_API / "app.py").read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_redis_health":
            body = ast.dump(node)
            assert "from_url" not in body, (
                "_redis_health must ping app.state.redis, not a throwaway client"
            )
            return
    raise AssertionError("_redis_health not found")


class _RecordingRedis:
    def __init__(self) -> None:
        self.sets: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.sets[key] = value

    async def get(self, key: str) -> str | None:
        return self.sets.get(key)

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_proxy_stamps_traffic_when_the_lease_is_armed() -> None:
    import datetime as dt

    from pitwall.leases.activity import read_lease_traffic, stamp_lease_traffic

    fake = _RecordingRedis()
    now = dt.datetime(2026, 8, 30, 12, 0, tzinfo=dt.UTC)
    await stamp_lease_traffic(fake, "lease_abc", now=now)

    assert fake.sets, "a stamp must reach Redis"
    seen = await read_lease_traffic(fake, "lease_abc")
    assert seen.available is True
