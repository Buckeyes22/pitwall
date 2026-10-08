"""Explicit reset times win over exponential backoff for every non-permanent reason."""

from __future__ import annotations

import datetime as dt

from pitwall.routing.lockout import LockoutKey, LockoutTable, model_lockout_key

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
KEY = LockoutKey("prov", "qwen3.8-flash")


def test_rate_limit_with_reset_uses_the_reset() -> None:
    state = LockoutTable().record_failure(
        KEY, now=NOW, reason="rate_limit_exceeded", reset_at=NOW + dt.timedelta(seconds=60)
    )
    assert state.locked_until == NOW + dt.timedelta(seconds=60)


def test_rate_limit_without_reset_keeps_backoff() -> None:
    state = LockoutTable().record_failure(KEY, now=NOW, reason="rate_limit_exceeded")
    assert state.locked_until == NOW + dt.timedelta(seconds=120)


def test_model_studio_providers_have_a_lockout_key() -> None:
    provider = {"id": "prov", "config": {"model_studio": {"model": "qwen3.8-flash"}}}
    assert model_lockout_key(provider) == KEY
