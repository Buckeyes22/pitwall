"""Subscription usage: one row per plan and account (stdlib only)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib import request

from ..setup import environment_with_user_bins, resolve_harness_binary
from . import accounts, cache, claude, codex, glm, minimax, model_studio, rows
from .accounts import Account
from .rows import (
    DETAIL_MAX,
    Opener,
    ReadError,
    Reading,
    Row,
    Unmeasured,
    derive_status,
    iso_utc,
    render_table,
)

__all__ = ["Account", "READERS", "Row", "collect", "iso_utc", "render_table", "rows"]

Reader = Callable[[Account, Mapping[str, str], Path, datetime, Opener], Reading]
READERS: dict[str, Reader] = {
    "claude": claude.read,
    "codex": codex.read,
    "glm": glm.read,
    "minimax": minimax.read,
    "model-studio": model_studio.read,
}
NO_SOURCE = "no usage source"


def _row(account: Account, moment: datetime, *, status: str, detail: str) -> Row:
    return Row(
        plan=account.plan,
        account=account.label,
        routes=account.routes,
        label=accounts.LABELS[account.plan],
        tier="",
        windows=(),
        status=status,
        detail=detail[:DETAIL_MAX],
        observed_at=iso_utc(moment),
    )


def _from_cache(account: Account, entry: Mapping[str, Any], moment: datetime) -> Row:
    failure = entry.get("error")
    stored = entry.get("row")
    if isinstance(stored, dict):
        try:
            row = replace(Row.from_dict(stored), account=account.label, routes=account.routes)
        except KeyError, TypeError, ValueError:
            row = None
        if row is not None:
            return (
                replace(row, status="stale", detail=str(failure)[:DETAIL_MAX]) if failure else row
            )
    return _row(account, moment, status="error", detail=str(failure or "no reading yet"))


def _read(
    account: Account,
    reader: Reader,
    env: Mapping[str, str],
    home: Path,
    moment: datetime,
    opener: Opener,
) -> Row:
    if account.problem:
        return _row(account, moment, status="error", detail=account.problem)
    epoch = moment.timestamp()
    entry = cache.load(env, account.plan, account.label)
    if entry is not None and not cache.due(entry, account.plan, epoch):
        return _from_cache(account, entry, moment)
    last_good = entry["row"] if entry is not None else None
    # The attempt is recorded before the request so a failing source is throttled too.
    cache.store(env, account.plan, account.label, epoch, row=last_good, error="read did not finish")
    try:
        reading = reader(account, env, home, moment, opener)
    except Unmeasured as exc:
        row = _row(account, moment, status="unknown", detail=str(exc))
        cache.store(env, account.plan, account.label, epoch, row=row.to_dict(), error=None)
        return row
    except ReadError as exc:
        failed = cache.store(env, account.plan, account.label, epoch, row=last_good, error=str(exc))
        return _from_cache(account, failed, moment)
    except AttributeError, IndexError, KeyError, TypeError, ValueError:
        failed = cache.store(
            env, account.plan, account.label, epoch, row=last_good, error="unexpected response"
        )
        return _from_cache(account, failed, moment)
    row = Row(
        plan=account.plan,
        account=account.label,
        routes=account.routes,
        label=accounts.LABELS[account.plan],
        tier=reading.tier,
        windows=reading.windows,
        status=derive_status(reading.windows, limit_reached=reading.limit_reached),
        detail=reading.detail[:DETAIL_MAX],
        observed_at=iso_utc(moment),
    )
    cache.store(env, account.plan, account.label, epoch, row=row.to_dict(), error=None)
    return row


def collect(
    env: Mapping[str, str],
    *,
    registry: Mapping[str, Any],
    routes_config: Mapping[str, Any],
    home: Path,
    now: datetime | None = None,
    opener: Opener = request.urlopen,
    installed: Callable[[str], bool] | None = None,
    readers: Mapping[str, Reader] | None = None,
) -> list[Row]:
    moment = now or datetime.now(UTC)
    table = READERS if readers is None else readers
    if installed is None:
        detection = environment_with_user_bins(env, home)

        def installed(harness: str) -> bool:
            return resolve_harness_binary(harness, detection, home) is not None

    rows: list[Row] = []
    for account in accounts.discover(routes_config, registry, env, home, installed=installed):
        reader = table.get(account.plan)
        if reader is None:
            rows.append(_row(account, moment, status="unknown", detail=NO_SOURCE))
        else:
            rows.append(_read(account, reader, env, home, moment, opener))
    return rows
