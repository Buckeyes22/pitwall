"""Claude subscription usage, read with the login the CLI already holds (stdlib only)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request

from ..endpoints import ENDPOINT_USER_AGENT
from .accounts import Account
from .rows import Opener, ReadError, Reading, Window, fetch_json, iso_utc, parse_instant, percent

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
BETA = "oauth-2025-04-20"
CREDENTIALS = ".credentials.json"
EXPIRED = "login expired; send work to this account once to refresh it"


def _tier(oauth: Mapping[str, Any]) -> str:
    rate = str(oauth.get("rateLimitTier") or "")
    kind = str(oauth.get("subscriptionType") or "")
    if "20x" in rate:
        return "Max 20x"
    if "5x" in rate:
        return "Max 5x"
    return {"max": "Max", "pro": "Pro"}.get(kind, kind)


def _window(name: str, raw: Any) -> Window:
    if not isinstance(raw, dict):
        return Window(name, None, None)
    resets = raw.get("resets_at")
    try:
        instant = iso_utc(parse_instant(resets)) if isinstance(resets, str) else None
    except ValueError:
        instant = None
    return Window(name, percent(raw.get("utilization")), instant)


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    assert account.login_dir is not None
    try:
        document = json.loads((account.login_dir / CREDENTIALS).read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise ReadError(f"cannot read {CREDENTIALS}") from None
    oauth = document.get("claudeAiOauth", document) if isinstance(document, dict) else None
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    if not isinstance(oauth, dict) or not isinstance(token, str) or not token:
        raise ReadError(f"no access token in {CREDENTIALS}")
    expires = oauth.get("expiresAt")
    if (
        isinstance(expires, (int, float))
        and not isinstance(expires, bool)
        and expires / 1000 <= now.timestamp()
    ):
        raise ReadError(EXPIRED)
    outbound = request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": BETA,
            "Accept": "application/json",
            "User-Agent": ENDPOINT_USER_AGENT,
        },
        method="GET",
    )
    payload = fetch_json(outbound, opener)
    if not isinstance(payload, dict):
        raise ReadError("unexpected response")
    extras = []
    for key, name in (("seven_day_opus", "opus 7d"), ("seven_day_sonnet", "sonnet 7d")):
        raw = payload.get(key)
        value = percent(raw.get("utilization")) if isinstance(raw, dict) else None
        if value is not None:
            extras.append(f"{name} {value}%")
    return Reading(
        tier=_tier(oauth),
        windows=(_window("5h", payload.get("five_hour")), _window("7d", payload.get("seven_day"))),
        detail=", ".join(extras),
    )
