"""Codex (ChatGPT plan) usage, read with the login the CLI already holds (stdlib only)."""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib import request

from ..endpoints import ENDPOINT_USER_AGENT
from .accounts import Account
from .rows import Opener, ReadError, Reading, Window, fetch_json, from_epoch, percent

USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
AUTH_FILE = "auth.json"
AUTH_CLAIM = "https://api.openai.com/auth"
EXPIRY_SKEW = 120
FIVE_HOURS_MAX = 10 * 3600
WEEK_MIN = 7 * 24 * 3600
EXPIRED = "login expired; send work to this account once to refresh it"


def claims(token: Any) -> dict[str, Any]:
    """The payload of a JWT, or an empty object. The signature is not checked; nothing is trusted from it."""
    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    segment = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        value = json.loads(base64.urlsafe_b64decode(segment.encode("ascii")))
    except ValueError, UnicodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _seconds(window: Mapping[str, Any]) -> float | None:
    value = window.get("limit_window_seconds")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _window(name: str, raw: Mapping[str, Any] | None) -> Window:
    if raw is None:
        return Window(name, None, None)
    reset = raw.get("reset_at")
    instant = (
        from_epoch(reset)
        if isinstance(reset, (int, float)) and not isinstance(reset, bool)
        else None
    )
    return Window(name, percent(raw.get("used_percent")), instant)


def read(
    account: Account, env: Mapping[str, str], home: Path, now: datetime, opener: Opener
) -> Reading:
    assert account.login_dir is not None
    try:
        document = json.loads((account.login_dir / AUTH_FILE).read_text(encoding="utf-8"))
    except OSError, ValueError:
        raise ReadError(f"cannot read {AUTH_FILE}") from None
    tokens = document.get("tokens") if isinstance(document, dict) else None
    access = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(tokens, dict) or not isinstance(access, str) or not access:
        raise ReadError(f"no access token in {AUTH_FILE}")
    expires = claims(access).get("exp")
    if (
        isinstance(expires, bool)
        or not isinstance(expires, (int, float))
        or now.timestamp() >= expires - EXPIRY_SKEW
    ):
        raise ReadError(EXPIRED)
    identity = claims(tokens.get("id_token")).get(AUTH_CLAIM)
    identity = identity if isinstance(identity, dict) else {}
    account_id = tokens.get("account_id") or identity.get("chatgpt_account_id")
    headers = {
        "Authorization": f"Bearer {access}",
        "Accept": "application/json",
        "User-Agent": ENDPOINT_USER_AGENT,
    }
    if isinstance(account_id, str) and account_id:
        headers["ChatGPT-Account-Id"] = account_id
    payload = fetch_json(request.Request(USAGE_URL, headers=headers, method="GET"), opener)
    limits = payload.get("rate_limit") if isinstance(payload, dict) else None
    if not isinstance(limits, dict):
        raise ReadError("unexpected response")
    primary = (
        limits.get("primary_window") if isinstance(limits.get("primary_window"), dict) else None
    )
    secondary = (
        limits.get("secondary_window") if isinstance(limits.get("secondary_window"), dict) else None
    )
    candidates = [window for window in (primary, secondary) if window is not None]
    five = next((w for w in candidates if (_seconds(w) or float("inf")) <= FIVE_HOURS_MAX), None)
    week = next((w for w in candidates if (_seconds(w) or 0.0) >= WEEK_MIN), None)
    if five is None and primary is not None and primary is not week:
        five = primary
    if week is None and secondary is not None and secondary is not five:
        week = secondary
    plan = payload.get("plan_type") or identity.get("chatgpt_plan_type") or ""
    return Reading(
        tier=str(plan).capitalize(),
        windows=(_window("5h", five), _window("7d", week)),
        limit_reached=limits.get("limit_reached") is True,
    )
