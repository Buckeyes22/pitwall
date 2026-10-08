"""``usage serve``: scheduled sampling, burn rate, and the desk meter contract (stdlib only)."""

from __future__ import annotations

import hmac
import json
import socket
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from .rows import Row, iso_utc, parse_instant

TOKEN_ENV = "PITWALL_AGENTS_USAGE_TOKEN"
LOOPBACK = ("127.0.0.1", "::1", "localhost")
MIN_INTERVAL = 30.0
RATE_WINDOW = 300.0
MAX_BASELINE = 600.0
SAMPLES_MAX = 40
HISTORY_INTERVAL = 300.0
HISTORY_MAX = 24
RATE_FLOOR = 0.05
LEGACY_ROWS = 7
LEGACY_ACCOUNT = 3
LEGACY_DETAIL = 80
LEGACY_STATUSES = ("ok", "warn", "limit", "error", "stale")
MEASURED = ("ok", "warn", "limit")
CollectRows = Callable[[datetime], Sequence[Row]]
Clock = Callable[[], datetime]


class ServeConfigError(Exception):
    """The serve mode was asked to start in a way it refuses."""


def row_key(row: Row) -> str:
    return f"{row.plan}-{row.account}" if row.account else row.plan


def _used(row: Row, window: str) -> int | None:
    return next((item.used_pct for item in row.windows if item.name == window), None)


@dataclass(frozen=True, slots=True)
class Sample:
    at: float
    values: Mapping[str, int | None]


class Sampler:
    """Readings over time, kept in memory. A restart starts the history again."""

    def __init__(self) -> None:
        self._samples: dict[str, list[Sample]] = {}
        self._history: dict[str, list[Sample]] = {}

    def observe(self, rows: Sequence[Row], now_epoch: float) -> None:
        for row in rows:
            if row.status not in MEASURED:
                continue
            sample = Sample(now_epoch, {item.name: item.used_pct for item in row.windows})
            samples = self._samples.setdefault(row_key(row), [])
            samples.append(sample)
            del samples[:-SAMPLES_MAX]
            history = self._history.setdefault(row_key(row), [])
            if not history or now_epoch - history[-1].at >= HISTORY_INTERVAL:
                history.append(sample)
                del history[:-HISTORY_MAX]

    def rate(self, row: Row, window: str, now_epoch: float) -> float | None:
        """Percent-points per minute against a baseline 5 to 10 minutes old, or None."""
        current = _used(row, window)
        if row.status not in MEASURED or current is None:
            return None
        aged = [
            s
            for s in self._samples.get(row_key(row), [])
            if RATE_WINDOW <= now_epoch - s.at <= MAX_BASELINE
        ]
        if not aged:
            return None
        baseline = aged[-1]
        earlier = baseline.values.get(window)
        if earlier is None:
            return None
        value = round((current - earlier) / ((now_epoch - baseline.at) / 60.0), 2)
        return value if value >= 0 else None

    def eta_minutes(self, row: Row, window: str, now_epoch: float) -> int | None:
        rate = self.rate(row, window, now_epoch)
        current = _used(row, window)
        if rate is None or current is None or rate <= RATE_FLOOR:
            return None
        return max(0, int(round((100 - current) / rate)))

    def history(self, row: Row, window: str = "5h") -> list[int]:
        values = (sample.values.get(window) for sample in self._history.get(row_key(row), []))
        return [value for value in values if value is not None]


def _minutes_until(resets_at: str | None, now: datetime) -> int | None:
    if resets_at is None:
        return None
    try:
        moment = parse_instant(resets_at)
    except ValueError:
        return None
    return max(0, int(round((moment - now).total_seconds() / 60.0)))


def _tags(rows: Sequence[Row]) -> dict[str, str]:
    """Account tags for the desk meter: three characters, never empty or repeated within a plan."""
    tags: dict[str, str] = {}
    plans: dict[str, list[Row]] = {}
    for row in rows:
        plans.setdefault(row.plan, []).append(row)
    for group in plans.values():
        if len(group) == 1:
            continue
        cut = [(row.account[:LEGACY_ACCOUNT] or "1") for row in group]
        if len(set(cut)) < len(cut):
            cut = [str(index + 1) for index in range(len(group))]
        for row, tag in zip(group, cut, strict=False):
            tags[row_key(row)] = tag
    return tags


def legacy_payload(rows: Sequence[Row], sampler: Sampler, now: datetime) -> dict[str, Any]:
    """The payload the desk meter firmware reads. Field names and types are frozen."""
    epoch = now.timestamp()
    shown = [row for row in rows if row.status in LEGACY_STATUSES][:LEGACY_ROWS]
    tags = _tags(shown)
    harnesses: list[dict[str, Any]] = []
    for row in shown:
        short = next((item for item in row.windows if item.name == "5h"), None)
        long_name = "7d" if any(item.name == "7d" for item in row.windows) else "30d"
        long = next((item for item in row.windows if item.name == long_name), None)
        harness: dict[str, Any] = {"name": row_key(row), "label": row.label}
        if row_key(row) in tags:
            harness["account"] = tags[row_key(row)]
        extra: dict[str, Any] = {}
        if row.detail:
            extra["error" if row.status in ("error", "stale") else "note"] = row.detail[
                :LEGACY_DETAIL
            ]
        harness.update(
            {
                "tier": row.tier,
                "s_pct": short.used_pct if short else None,
                "s_reset_min": _minutes_until(short.resets_at, now) if short else None,
                "w_pct": long.used_pct if long else None,
                "w_reset_min": _minutes_until(long.resets_at, now) if long else None,
                "status": row.status,
                "extra": extra,
                "s_rate": sampler.rate(row, "5h", epoch),
                "w_rate": sampler.rate(row, long_name, epoch),
                "s_eta_min": sampler.eta_minutes(row, "5h", epoch),
                "w_eta_min": sampler.eta_minutes(row, long_name, epoch),
                "s_hist": sampler.history(row),
            }
        )
        harnesses.append(harness)
    return {"updated": int(epoch), "providers": harnesses}


def plans_payload(rows: Sequence[Row], sampler: Sampler, now: datetime) -> dict[str, Any]:
    epoch = now.timestamp()
    plans: list[dict[str, Any]] = []
    for row in rows:
        value = row.to_dict()
        for window in value["windows"]:
            window["rate"] = sampler.rate(row, window["name"], epoch)
            window["eta_min"] = sampler.eta_minutes(row, window["name"], epoch)
            window["history"] = (
                sampler.history(row, window["name"]) if window["name"] == "5h" else []
            )
        plans.append(value)
    return {"observed_at": iso_utc(now), "plans": plans}


class UsageState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rows: list[Row] = []
        self._sampler = Sampler()

    def update(self, rows: Sequence[Row], now: datetime) -> None:
        with self._lock:
            self._sampler.observe(rows, now.timestamp())
            self._rows = list(rows)

    def legacy(self, now: datetime) -> dict[str, Any]:
        with self._lock:
            return legacy_payload(self._rows, self._sampler, now)

    def plans(self, now: datetime) -> dict[str, Any]:
        with self._lock:
            return plans_payload(self._rows, self._sampler, now)


def refresh_once(state: UsageState, collect_rows: CollectRows, clock: Clock) -> bool:
    """One refresh. A failure keeps the rows already held and reports False."""
    now = clock()
    try:
        rows = collect_rows(now)
    except Exception:  # reason: the loop must outlive any one bad refresh
        return False
    state.update(rows, now)
    return True


def resolve_token(host: str, env: Mapping[str, str]) -> str | None:
    token = env.get(TOKEN_ENV, "").strip()
    if host not in LOOPBACK and not token:
        raise ServeConfigError(f"binding {host} needs a bearer token; set {TOKEN_ENV}")
    return token or None


class _IPv6Server(ThreadingHTTPServer):
    address_family = socket.AF_INET6


def make_server(
    host: str, port: int, token: str | None, state: UsageState, clock: Clock
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                self._send(200, {"ok": True})
            elif path in ("/usage", "/plans"):
                if not self._authorized():
                    self._send(401, {"error": "unauthorized"})
                elif path == "/usage":
                    self._send(200, state.legacy(clock()))
                else:
                    self._send(200, state.plans(clock()))
            else:
                self._send(404, {"error": "not_found"})

        def _authorized(self) -> bool:
            if token is None:
                return True
            offered = self.headers.get("Authorization", "")
            return hmac.compare_digest(offered.encode("utf-8"), f"Bearer {token}".encode())

        def _send(self, status: int, value: Mapping[str, Any]) -> None:
            # Always a whole body with its length: the desk meter stream-parses and cannot read chunks.
            payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_: object) -> None:
            return None

    server_class = _IPv6Server if ":" in host else ThreadingHTTPServer
    return server_class((host, port), Handler)


def run(
    host: str,
    port: int,
    interval: float,
    env: Mapping[str, str],
    collect_rows: CollectRows,
    *,
    clock: Clock = lambda: datetime.now(UTC),
) -> None:
    """Serve until interrupted. Raises ServeConfigError before binding when refused."""
    if interval < MIN_INTERVAL:
        raise ServeConfigError(f"--interval must be at least {MIN_INTERVAL:.0f} seconds")
    token = resolve_token(host, env)
    state = UsageState()
    refresh_once(state, collect_rows, clock)
    server = make_server(host, port, token, state, clock)
    stop = threading.Event()

    def loop() -> None:
        while not stop.wait(interval):
            refresh_once(state, collect_rows, clock)

    worker = threading.Thread(target=loop, name="usage-refresh", daemon=True)
    worker.start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
