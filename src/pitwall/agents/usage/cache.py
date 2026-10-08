"""One file per account: the last good row, the last failure, and the last attempt (stdlib only)."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..paths import state_root
from ..run_store import atomic_write_json

DEFAULT_INTERVAL = 60.0
INTERVALS = {"claude": 300.0}


def path_for(env: Mapping[str, str], plan: str, account: str) -> Path:
    name = f"{plan}-{account}" if account else plan
    return state_root(env) / "usage" / f"{name}.json"


def load(env: Mapping[str, str], plan: str, account: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path_for(env, plan, account).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    if not isinstance(value, dict):
        return None
    attempted = value.get("attemptedAt")
    if isinstance(attempted, bool) or not isinstance(attempted, (int, float)):
        return None
    row = value.get("row")
    error = value.get("error")
    return {
        "attemptedAt": float(attempted),
        "row": row if isinstance(row, dict) else None,
        "error": error if isinstance(error, str) and error else None,
    }


def due(entry: Mapping[str, Any] | None, plan: str, now_epoch: float) -> bool:
    if entry is None:
        return True
    return now_epoch - float(entry["attemptedAt"]) >= INTERVALS.get(plan, DEFAULT_INTERVAL)


def store(
    env: Mapping[str, str],
    plan: str,
    account: str,
    now_epoch: float,
    *,
    row: Mapping[str, Any] | None,
    error: str | None,
) -> dict[str, Any]:
    entry = {
        "attemptedAt": now_epoch,
        "row": dict(row) if row is not None else None,
        "error": error,
    }
    atomic_write_json(path_for(env, plan, account), entry)
    return entry
