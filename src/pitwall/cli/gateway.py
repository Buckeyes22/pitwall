"""pitwall gateway and quotas nouns for the free-tier gateway (ADR 0007)."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from pitwall.cli.output import Output, add_json_argument, json_mode
from pitwall.doctor import DoctorCheck, DoctorSection

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LOCK_RELPATH = Path("config/gateway-catalog.lock.json")
_CATALOG_RELPATH = Path("config/gateway-catalog.json")
_REMOTE_TRANSPORT: httpx.BaseTransport | None = None
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _workspace_root() -> Path:
    cwd = Path.cwd()
    if (cwd / "config").is_dir() or (cwd / "seed").is_dir():
        return cwd
    if (_REPO_ROOT / "config").is_dir() or (_REPO_ROOT / "seed").is_dir():
        return _REPO_ROOT
    return Path(__file__).resolve().parents[1] / "gateway_catalog" / "data"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return raw if isinstance(raw, dict) else None


def _api_base() -> str:
    return os.environ.get("PITWALL_API_URL", "").strip().rstrip("/")


def cmd_gateway(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pitwall gateway",
        description="Sync, inspect, and doctor the free-tier gateway catalog.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    sync = subcommands.add_parser(
        "sync",
        help="Sync the catalog from a pinned OmniRoute release, or apply benchmark verdicts.",
    )
    mode = sync.add_mutually_exclusive_group(required=True)
    mode.add_argument("--version", help="Pinned upstream release (e.g. 3.8.51).")
    mode.add_argument(
        "--apply-verdicts",
        type=Path,
        metavar="DOSSIER",
        help="Disable every provider a benchmark dossier marks `kill` in the providers seed.",
    )
    sync.add_argument(
        "--from-json", type=Path, help="Skip the registry download; read an already-extracted JSON."
    )
    sync.add_argument(
        "--seed",
        type=Path,
        help="Providers seed to rewrite with --apply-verdicts "
        "(default: <repo-root>/seed/gateway-providers.yaml).",
    )
    sync.add_argument(
        "--repo-root",
        type=Path,
        help="Checkout to write seeds, catalog, and lock into (default: the enclosing checkout).",
    )

    status = subcommands.add_parser("status", help="Show the catalog lock and live quota state.")
    add_json_argument(status)

    serve = subcommands.add_parser("serve", help="Run the loopback gateway in the foreground.")
    serve.add_argument("--bind", help="Loopback address to bind (default: 127.0.0.1).")
    serve.add_argument("--port", help="Port in [0, 65535] (default: 20130).")

    doctor = subcommands.add_parser("doctor", help="Check gateway supervision preconditions.")
    add_json_argument(doctor)

    args = parser.parse_args(argv)
    if args.command == "serve":
        from pitwall.gateway.app import serve as serve_gateway

        return serve_gateway(bind=args.bind, port=args.port)
    if args.command == "sync":
        return _gateway_sync(args)
    if args.command == "status":
        return _gateway_status(args)
    return _gateway_doctor(args)


def _gateway_sync(args: argparse.Namespace) -> int:
    from pitwall.gateway_catalog import sync as sync_catalog

    if args.apply_verdicts is not None:
        rest = ["--apply-verdicts", str(args.apply_verdicts)]
        if args.seed is not None:
            rest = [*rest, "--seed", str(args.seed)]
    else:
        rest = ["--version", args.version]
    if args.from_json is not None:
        rest = [*rest, "--from-json", str(args.from_json)]
    if args.repo_root is not None:
        rest = [*rest, "--repo-root", str(args.repo_root)]
    return sync_catalog.main(rest)


def _fetch_remote_quotas(base: str) -> list[dict[str, Any]] | None:
    token = os.environ.get("PITWALL_API_TOKEN", "").strip()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        with httpx.Client(transport=_REMOTE_TRANSPORT, timeout=5.0) as client:
            response = client.get(f"{base}/v1/quotas", headers=headers)
            response.raise_for_status()
            payload: Any = response.json()
    except httpx.HTTPError, ValueError:
        return None
    rows = payload.get("quotas") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return None
    return [row for row in rows if isinstance(row, dict)]


def _gateway_status(args: argparse.Namespace) -> int:
    out = Output(json_mode(args))
    lock_path = _workspace_root() / _LOCK_RELPATH
    lock = _read_json(lock_path)
    if lock is None:
        out.print_error(f"no gateway catalog lock at {lock_path}; run `pitwall gateway sync`")
        out.emit()
        return 1
    base = _api_base()
    quotas: list[dict[str, Any]] | None = None
    source = "local"
    api_url: str | None = None
    if base:
        api_url = base
        remote = _fetch_remote_quotas(base)
        if remote is not None:
            quotas = [_remote_row(row) for row in remote]
            source = "api"
    out.add_json("lock", lock)
    out.add_json("quotas", quotas)
    out.add_json("source", source)
    out.add_json("api_url", api_url)
    if not out.json_mode:
        out.print(f"catalog lock: {lock_path}")
        out.print(
            f"upstream omniroute@{lock.get('upstream_version')} "
            f"rows={lock.get('row_count')} pools={lock.get('pool_count')} "
            f"steady_monthly={lock.get('steady_monthly')} avoid={len(lock.get('avoid_list', []))}"
        )
        if quotas is not None:
            out.print(f"live quotas: {len(quotas)} rows from {api_url}/v1/quotas")
        elif api_url is not None:
            out.print(f"live quotas: unreachable at {api_url} (showing local lock only)")
        else:
            out.print("live quotas: PITWALL_API_URL not set (showing local lock only)")
    out.emit()
    return 0


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _gateway_doctor(args: argparse.Namespace) -> int:
    out = Output(json_mode(args))
    root = _workspace_root()
    lock = _read_json(root / _LOCK_RELPATH)
    checks = [
        _catalog_lock_check(root, lock),
        _seeds_check(root, lock),
        _health_check(),
        _front_door_check(),
    ]
    ok = all(check["ok"] for check in checks)
    out.add_json("checks", checks)
    out.add_json("ok", ok)
    if not out.json_mode:
        for check in checks:
            mark = "ok  " if check["ok"] else "FAIL"
            out.print(f"{mark} {check['name']}: {check['detail']}")
        failed = sum(1 for check in checks if not check["ok"])
        out.print(f"gateway doctor: {'all checks passed' if ok else f'{failed} check(s) failed'}")
    out.emit()
    return 0 if ok else 1


def gateway_section() -> DoctorSection:
    """The ``gateway`` section of ``pitwall doctor``.

    The gateway is optional, so an absent catalog or a gateway that is not running warns
    (or skips when no token is configured) and only a non-loopback front door fails.
    """
    root = _workspace_root()
    lock = _read_json(root / _LOCK_RELPATH)
    checks: list[DoctorCheck] = []
    for check in (_catalog_lock_check(root, lock), _seeds_check(root, lock)):
        checks.append(
            DoctorCheck(
                f"gateway.{check['name']}",
                "gateway",
                "ok" if check["ok"] else "warn",
                str(check["detail"]),
                None if check["ok"] else "run `pitwall gateway sync`",
            )
        )
    if os.environ.get("PITWALL_GATEWAY_TOKEN"):
        health = _health_check()
        checks.append(
            DoctorCheck(
                "gateway.gateway_health",
                "gateway",
                "ok" if health["ok"] else "warn",
                str(health["detail"]),
                None if health["ok"] else "start it with `pitwall serve --gateway`",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "gateway.gateway_health",
                "gateway",
                "skip",
                "PITWALL_GATEWAY_TOKEN is unset; the gateway is not configured",
            )
        )
    door = _front_door_check()
    checks.append(
        DoctorCheck(
            "gateway.front_door_loopback",
            "gateway",
            "ok" if door["ok"] else "fail",
            str(door["detail"]),
            None if door["ok"] else "point PITWALL_GATEWAY_URL at a loopback address",
        )
    )
    return DoctorSection("gateway", tuple(checks))


def _catalog_lock_check(root: Path, lock: dict[str, Any] | None) -> dict[str, Any]:
    return _check("catalog_lock", lock is not None, str(root / _LOCK_RELPATH))


def _seeds_check(root: Path, lock: dict[str, Any] | None) -> dict[str, Any]:
    providers = root / "seed" / "gateway-providers.yaml"
    capabilities = root / "seed" / "gateway-capabilities.yaml"
    if lock is None or not providers.is_file() or not capabilities.is_file():
        return _check("seeds_match_lock", False, "seed files or catalog lock missing")
    text = providers.read_text(encoding="utf-8")
    rows = sum(1 for line in text.splitlines() if line.startswith("  - name: "))
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    expected = lock.get("row_count")
    ok = expected is not None and rows == expected
    return _check(
        "seeds_match_lock",
        ok,
        f"{rows} seed rows (sha256:{digest}) vs lock row_count={expected}",
    )


def _health_check() -> dict[str, Any]:
    from pitwall.personal.gateway import DEFAULT_HEALTH_URL, GatewaySupervisor
    from pitwall.personal.state import StateStore

    supervisor = GatewaySupervisor(StateStore(), env=os.environ)
    return _check(
        "gateway_health",
        supervisor.health(),
        f"GET {DEFAULT_HEALTH_URL} with bearer PITWALL_GATEWAY_TOKEN",
    )


def _front_door_check() -> dict[str, Any]:
    raw = os.environ.get("PITWALL_GATEWAY_URL", "").strip()
    if not raw:
        return _check(
            "front_door_loopback", True, "PITWALL_GATEWAY_URL unset; default http://127.0.0.1:20130"
        )
    host = urlparse(raw).hostname or ""
    return _check("front_door_loopback", host in _LOOPBACK_HOSTS, raw)


def cmd_quotas(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="pitwall quotas",
        description="Show the free-pool quota burn-down (provider, pool, headroom, reset, tos, lockout).",
    )
    add_json_argument(parser)
    args = parser.parse_args(argv)
    out = Output(json_mode(args))
    rows, source = _quota_rows()
    if out.json_mode:
        out.set_json({"source": source, "quotas": rows})
        out.emit()
        return 0
    out.print_table(
        "Free-pool quotas",
        ["PROVIDER", "POOL", "FREE TYPE", "USED/BUDGET", "HEADROOM", "RESET", "TOS", "LOCKOUT"],
        [
            [
                row["provider"],
                row["pool"] or "—",
                row["free_type"] or "—",
                _used_budget(row),
                _headroom_bar(row["headroom"]),
                _countdown(row["reset_at"]),
                row["tos"] or "—",
                _lockout_badge(row["lockout"]),
            ]
            for row in rows
        ],
    )
    out.emit()
    return 0


def _quota_rows() -> tuple[list[dict[str, Any]], str]:
    base = _api_base()
    if base:
        remote = _fetch_remote_quotas(base)
        if remote is not None:
            return [_remote_row(row) for row in remote], "api"
    return _local_rows(), "local"


def _local_rows() -> list[dict[str, Any]]:
    from pitwall.routing.lockout import get_lockout_table

    catalog = _read_json(_workspace_root() / _CATALOG_RELPATH) or {}
    snapshot = get_lockout_table().snapshot()
    now = dt.datetime.now(dt.UTC)
    rows: list[dict[str, Any]] = []
    for provider in catalog.get("providers", []):
        if not isinstance(provider, dict):
            continue
        gateway = provider.get("gateway")
        catalog_meta = gateway.get("catalog") if isinstance(gateway, dict) else None
        if not isinstance(catalog_meta, dict):
            continue
        budget = _as_int(catalog_meta.get("monthly_tokens")) or _as_int(
            catalog_meta.get("credit_tokens")
        )
        name = str(provider.get("name", ""))
        rows.append(
            {
                "provider": name,
                "pool": str(catalog_meta.get("pool_key") or ""),
                "free_type": str(catalog_meta.get("free_type") or ""),
                "used": "0",
                "budget": str(budget) if budget else None,
                "headroom": 1.0 if budget else None,
                "reset_at": None,
                "tos": str(catalog_meta.get("tos") or ""),
                "lockout": _local_lockout(name, snapshot, now),
            }
        )
    return rows


def _remote_row(raw: dict[str, Any]) -> dict[str, Any]:
    budget = _as_decimal(raw.get("budget_units"))
    used = _as_decimal(raw.get("used_units"))
    headroom = raw.get("headroom")
    if headroom is None and budget is not None and budget > 0 and used is not None:
        headroom = max(0.0, min(1.0, float((budget - used) / budget)))
    return {
        "provider": str(raw.get("provider_id") or ""),
        "pool": str(raw.get("pool_key") or ""),
        "free_type": str(raw.get("free_type") or ""),
        "used": str(used) if used is not None else "0",
        "budget": str(budget) if budget is not None else None,
        "headroom": float(headroom) if headroom is not None else None,
        "reset_at": raw.get("reset_at"),
        "tos": str(raw.get("tos_verdict") or ""),
        "lockout": raw.get("lockout") if isinstance(raw.get("lockout"), dict) else None,
    }


def _local_lockout(
    provider: str,
    snapshot: Mapping[str, Mapping[str, Any]],
    now: dt.datetime,
) -> dict[str, Any] | None:
    for key, state in snapshot.items():
        if not key.startswith(f"{provider}/"):
            continue
        if state.get("permanent"):
            return dict(state)
        locked_until = state.get("locked_until")
        if locked_until:
            try:
                until = dt.datetime.fromisoformat(str(locked_until))
            except ValueError:
                continue
            if until > now:
                return dict(state)
    return None


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _used_budget(row: Mapping[str, Any]) -> str:
    return f"{row['used']}/{row['budget'] or '—'}"


def _headroom_bar(headroom: float | None) -> str:
    if headroom is None:
        return "—"
    share = max(0.0, min(1.0, headroom))
    filled = round(share * 10)
    return f"[{'#' * filled}{'-' * (10 - filled)}] {round(share * 100)}%"


def _countdown(reset_at: object, now: dt.datetime | None = None) -> str:
    if not reset_at:
        return "—"
    try:
        reset = dt.datetime.fromisoformat(str(reset_at))
    except ValueError:
        return "—"
    if reset.tzinfo is None:
        reset = reset.replace(tzinfo=dt.UTC)
    delta = reset - (now or dt.datetime.now(dt.UTC))
    if delta.total_seconds() <= 0:
        return "now"
    total_minutes = int(delta.total_seconds() // 60)
    days, remainder = divmod(total_minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _lockout_badge(lockout: Mapping[str, Any] | None) -> str:
    if not lockout:
        return "—"
    if lockout.get("permanent"):
        return "permanent"
    until = lockout.get("locked_until")
    if until:
        return f"locked until {until}"
    reason = lockout.get("reason")
    return str(reason) if reason else "—"
