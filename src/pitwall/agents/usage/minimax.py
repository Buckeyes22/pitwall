"""MiniMax Coding Plan usage; the quota is a request count (stdlib only)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import request

from ..endpoints import ENDPOINT_USER_AGENT
from .accounts import Account, api_key
from .rows import Opener, ReadError, Reading, Window, fetch_json, iso_utc, percent

# The coding-plan key is valid on the .io host only.
BASE_URL = "https://api.minimax.io"
BASE_URL_ENV = "MINIMAX_BASE"
PATH = "/v1/token_plan/remains"
_CODING_MODEL = re.compile(r"^MiniMax-M", re.IGNORECASE)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _window(name: str, used: Any, total: Any, remaining_ms: Any, now: datetime) -> Window:
    used_n, total_n, remaining = _number(used), _number(total), _number(remaining_ms)
    pct = (
        percent(used_n / total_n * 100)
        if used_n is not None and total_n is not None and total_n > 0
        else None
    )
    instant = (
        iso_utc(now + timedelta(milliseconds=remaining))
        if remaining is not None and remaining >= 0
        else None
    )
    return Window(name, pct, instant)


def _count(used: Any, total: Any) -> str:
    used_n, total_n = _number(used), _number(total)
    return f"{int(used_n)}/{int(total_n)}" if used_n is not None and total_n is not None else ""


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    key = api_key("minimax", env, home)
    if not key:
        raise ReadError("no MiniMax key available")
    outbound = request.Request(
        (env.get(BASE_URL_ENV) or BASE_URL).rstrip("/") + PATH,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "User-Agent": ENDPOINT_USER_AGENT,
        },
        method="GET",
    )
    payload = fetch_json(outbound, opener)
    if not isinstance(payload, dict):
        raise ReadError("unexpected response")
    models = payload.get("model_remains")
    if not isinstance(models, list) or not models:
        base = payload.get("base_resp")
        code = base.get("status_code") if isinstance(base, dict) else None
        raise ReadError(
            f"vendor status {code}" if isinstance(code, int) and code else "unexpected response"
        )
    entries = [item for item in models if isinstance(item, dict)]
    if not entries:
        raise ReadError("unexpected response")
    chosen = next(
        (item for item in entries if _CODING_MODEL.match(str(item.get("model_name") or ""))),
        entries[0],
    )
    five = _count(
        chosen.get("current_interval_usage_count"), chosen.get("current_interval_total_count")
    )
    week = _count(
        chosen.get("current_weekly_usage_count"), chosen.get("current_weekly_total_count")
    )
    detail = ", ".join(
        text
        for text in (f"5h requests {five}" if five else "", f"7d requests {week}" if week else "")
        if text
    )
    return Reading(
        tier="Coding Plan",
        windows=(
            _window(
                "5h",
                chosen.get("current_interval_usage_count"),
                chosen.get("current_interval_total_count"),
                chosen.get("remains_time"),
                now,
            ),
            _window(
                "7d",
                chosen.get("current_weekly_usage_count"),
                chosen.get("current_weekly_total_count"),
                chosen.get("weekly_remains_time"),
                now,
            ),
        ),
        detail=detail,
    )
