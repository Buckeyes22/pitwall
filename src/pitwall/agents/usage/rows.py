"""Usage rows: the shape every reader reduces to, and the status rules (stdlib only)."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib import error, request

STATUSES = ("ok", "warn", "limit", "error", "stale", "unknown")
WARN_AT = 80
LIMIT_AT = 100
DETAIL_MAX = 120
RESPONSE_CAP = 1024 * 1024
Opener = Callable[..., Any]


class ReadError(Exception):
    """A read failed. The message is fixed wording plus a status code, never a response body."""


class Unmeasured(Exception):
    """The plan is configured but its usage cannot be measured."""


@dataclass(frozen=True, slots=True)
class Window:
    name: str
    used_pct: int | None
    resets_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "used_pct": self.used_pct, "resets_at": self.resets_at}


@dataclass(frozen=True, slots=True)
class Reading:
    tier: str
    windows: tuple[Window, ...]
    detail: str = ""
    limit_reached: bool = False


@dataclass(frozen=True, slots=True)
class Row:
    plan: str
    account: str
    routes: tuple[str, ...]
    label: str
    tier: str
    windows: tuple[Window, ...]
    status: str
    detail: str
    observed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan,
            "account": self.account,
            "routes": list(self.routes),
            "label": self.label,
            "tier": self.tier,
            "windows": [window.to_dict() for window in self.windows],
            "status": self.status,
            "detail": self.detail,
            "observed_at": self.observed_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Row:
        windows = tuple(
            Window(str(item["name"]), item.get("used_pct"), item.get("resets_at"))
            for item in value.get("windows", [])
        )
        status = str(value["status"])
        if status not in STATUSES:
            raise ValueError(f"unknown status {status!r}")
        return cls(
            plan=str(value["plan"]),
            account=str(value.get("account", "")),
            routes=tuple(str(name) for name in value.get("routes", [])),
            label=str(value["label"]),
            tier=str(value.get("tier", "")),
            windows=windows,
            status=status,
            detail=str(value.get("detail", "")),
            observed_at=str(value["observed_at"]),
        )


def iso_utc(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def from_epoch(seconds: float) -> str:
    return iso_utc(datetime.fromtimestamp(seconds, tz=UTC))


def percent(value: Any) -> int | None:
    """Round half up to a whole percent; anything that is not a finite number is None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    return int(math.floor(value + 0.5))


def derive_status(windows: Sequence[Window], *, limit_reached: bool = False) -> str:
    worst = max((window.used_pct for window in windows if window.used_pct is not None), default=0)
    if limit_reached or worst >= LIMIT_AT:
        return "limit"
    if worst >= WARN_AT:
        return "warn"
    return "ok"


def fetch_json(outbound: request.Request, opener: Opener, *, timeout: float = 15.0) -> Any:
    try:
        with opener(outbound, timeout=timeout) as response:
            body = response.read(RESPONSE_CAP)
    except error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ReadError(f"HTTP {code}") from None
    except error.URLError, OSError:
        raise ReadError("network failure") from None
    try:
        return json.loads(body.decode("utf-8"))
    except UnicodeDecodeError, ValueError:
        raise ReadError("unexpected response") from None


def _when(resets_at: str | None, now: datetime) -> str:
    if resets_at is None:
        return ""
    try:
        moment = parse_instant(resets_at)
    except ValueError:
        return ""
    if moment.date() == now.date():
        return moment.strftime("%H:%M")
    if 0 <= (moment - now).days < 7:
        return moment.strftime("%a %H:%M")
    return moment.strftime("%Y-%m-%d")


def _window_text(window: Window, now: datetime) -> str:
    used = "—" if window.used_pct is None else f"{window.used_pct}%"
    when = _when(window.resets_at, now)
    verb = "renews" if window.name == "30d" else "resets"
    return f"{window.name} {used}" + (f" {verb} {when}" if when else "")


def render_table(rows: Sequence[Row], *, now: datetime) -> str:
    if not rows:
        return "no subscriptions found on this machine\n"
    header = ("plan", "account", "tier", "usage (times are UTC)", "status", "reached by")
    cells = [
        (
            row.plan,
            row.account or "-",
            row.tier or "-",
            "   ".join(_window_text(window, now) for window in row.windows) or row.detail or "-",
            row.status,
            ("route " + ", ".join(row.routes)) if row.routes else "(default)",
        )
        for row in rows
    ]
    widths = [
        max(len(header[index]), *(len(cell[index]) for cell in cells))
        for index in range(len(header))
    ]
    lines = ["  ".join(text.ljust(widths[index]) for index, text in enumerate(header)).rstrip()]
    lines.extend(
        "  ".join(text.ljust(widths[index]) for index, text in enumerate(cell)).rstrip()
        for cell in cells
    )
    return "\n".join(lines) + "\n"
