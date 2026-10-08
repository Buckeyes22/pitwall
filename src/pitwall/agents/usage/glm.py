"""GLM Coding Plan usage from the Z.ai quota endpoint (stdlib only)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request

from ..endpoints import ENDPOINT_USER_AGENT
from .accounts import Account, api_key
from .rows import Opener, ReadError, Reading, Window, fetch_json, from_epoch, percent

QUOTA_URL = "https://api.z.ai/api/monitor/usage/quota/limit"
QUOTA_URL_ENV = "GLM_QUOTA_URL"
FIVE_HOUR_CODES = (3, 5)
WEEKLY_CODES = (6, 1)


def _window(name: str, raw: Mapping[str, Any] | None) -> Window:
    if raw is None:
        return Window(name, None, None)
    reset = raw.get("nextResetTime")
    instant = (
        from_epoch(reset / 1000)
        if isinstance(reset, (int, float)) and not isinstance(reset, bool)
        else None
    )
    return Window(name, percent(raw.get("percentage")), instant)


def _coded(
    windows: Sequence[Mapping[str, Any]], codes: tuple[int, int]
) -> Mapping[str, Any] | None:
    return next((w for w in windows if (w.get("unit"), w.get("number")) == codes), None)


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    key = api_key("glm", env, home)
    if not key:
        raise ReadError("no GLM key available")
    outbound = request.Request(
        env.get(QUOTA_URL_ENV) or QUOTA_URL,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": ENDPOINT_USER_AGENT,
        },
        method="GET",
    )
    payload = fetch_json(outbound, opener)
    data = payload.get("data") if isinstance(payload, dict) else None
    limits = data.get("limits") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(limits, list):
        raise ReadError("unexpected response")
    entries = [item for item in limits if isinstance(item, dict)]
    tokens = [item for item in entries if item.get("type") == "TOKENS_LIMIT"]
    # The unit and number codes identify the windows. Reset order is only a fallback: shortly before
    # the weekly boundary the weekly window resets first, and reset order would swap the two.
    by_reset = sorted(tokens, key=lambda item: item.get("nextResetTime") or 0)
    five = _coded(tokens, FIVE_HOUR_CODES) or (by_reset[0] if by_reset else None)
    week = _coded(tokens, WEEKLY_CODES) or next(
        (item for item in by_reset if item is not five), None
    )
    tools = next((item for item in entries if item.get("type") == "TIME_LIMIT"), None)
    tools_pct = percent(tools.get("percentage")) if tools is not None else None
    level = data.get("level")
    return Reading(
        tier=str(level).capitalize() if isinstance(level, str) and level else "Coding Plan",
        windows=(_window("5h", five), _window("7d", week)),
        detail=f"tools {tools_pct}%" if tools_pct is not None else "",
    )
