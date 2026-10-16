"""Aggregate finished dispatch usage by launching session."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .paths import ledger_path
from .token_usage import TokenUsage


@dataclass(slots=True)
class SessionTotals:
    session: str
    dispatches: int
    with_usage: int
    without_usage: int
    usage: TokenUsage | None
    first_ts: str
    last_ts: str
    by_model: dict[tuple[str, str], TokenUsage] = field(default_factory=dict)
    model_dispatches: dict[tuple[str, str], int] = field(default_factory=dict)
    cost_known: int = 0


def load_finished(env: Mapping[str, str]) -> list[dict[str, Any]]:
    """Load valid finished rows from the configured observations ledger."""
    path = ledger_path(env)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("event") == "finished":
            rows.append(value)
    return rows


def summarize(
    rows: list[dict[str, Any]], *, session: str | None = None, since: str | None = None
) -> list[SessionTotals]:
    """Summarize finished ledger records, newest session first."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("event") != "finished":
            continue
        key = row.get("session")
        key = key if isinstance(key, str) and key else "unattributed"
        timestamp = row.get("ts")
        timestamp = timestamp if isinstance(timestamp, str) else ""
        if session is not None and key != session:
            continue
        if since is not None and timestamp[:10] < since:
            continue
        grouped.setdefault(key, []).append(row)

    totals: list[SessionTotals] = []
    for key, group in grouped.items():
        usage_total: TokenUsage | None = None
        by_model: dict[tuple[str, str], TokenUsage] = {}
        model_dispatches: dict[tuple[str, str], int] = {}
        with_usage = 0
        cost_known = 0
        timestamps: list[str] = []
        for row in group:
            timestamp = row.get("ts")
            if isinstance(timestamp, str) and timestamp:
                timestamps.append(timestamp)
            raw_usage = row.get("usage")
            try:
                usage = (
                    TokenUsage.from_public_dict(raw_usage) if isinstance(raw_usage, dict) else None
                )
            except ValueError:
                usage = None
            if usage is None:
                continue
            with_usage += 1
            usage_total = usage if usage_total is None else usage_total + usage
            shim = row.get("shim")
            model = row.get("model")
            model_key = (
                shim if isinstance(shim, str) and shim else "unknown",
                model if isinstance(model, str) and model else "unknown",
            )
            by_model[model_key] = (
                usage if model_key not in by_model else by_model[model_key] + usage
            )
            model_dispatches[model_key] = model_dispatches.get(model_key, 0) + 1
            if usage.cost_usd is not None:
                cost_known += 1
        first_ts = min(timestamps, default="")
        last_ts = max(timestamps, default="")
        totals.append(
            SessionTotals(
                session=key,
                dispatches=len(group),
                with_usage=with_usage,
                without_usage=len(group) - with_usage,
                usage=usage_total,
                first_ts=first_ts,
                last_ts=last_ts,
                by_model=by_model,
                model_dispatches=model_dispatches,
                cost_known=cost_known,
            )
        )
    return sorted(totals, key=lambda item: (item.last_ts, item.session), reverse=True)


def _public_totals(totals: SessionTotals) -> dict[str, Any]:
    by_model = [
        {
            "shim": shim,
            "model": model,
            "dispatches": totals.model_dispatches[(shim, model)],
            "usage": usage.to_public_dict(),
        }
        for (shim, model), usage in sorted(totals.by_model.items())
    ]
    return {
        "session": totals.session,
        "dispatches": totals.dispatches,
        "withUsage": totals.with_usage,
        "withoutUsage": totals.without_usage,
        "usage": None if totals.usage is None else totals.usage.to_public_dict(),
        "firstTs": totals.first_ts,
        "lastTs": totals.last_ts,
        "byModel": by_model,
    }


def _number(value: int | float | None) -> str:
    return "unknown" if value is None else f"{value:,}"


def render_text(totals: list[SessionTotals]) -> str:
    """Render a compact human-readable report."""
    blocks: list[str] = []
    for item in totals:
        lines = [
            f"{item.session}  dispatches {item.dispatches} "
            f"({item.with_usage} with usage, {item.without_usage} without)  "
            f"{item.first_ts or '-'} → {item.last_ts or '-'}"
        ]
        usage = item.usage
        if usage is None:
            lines.append("  no token usage recorded")
        else:
            cost = "unknown" if usage.cost_usd is None else f"${usage.cost_usd:.2f}"
            lines.append(
                f"  tokens in {_number(usage.input_tokens)}  "
                f"cache-read {_number(usage.cache_read_tokens)}  "
                f"cache-write {_number(usage.cache_write_tokens)}  "
                f"out {_number(usage.output_tokens)}  total {_number(usage.total_tokens)}  "
                f"cost {cost} (known for {item.cost_known} of {item.with_usage})"
            )
        for (shim, model), model_usage in sorted(item.by_model.items()):
            count = item.model_dispatches[(shim, model)]
            lines.append(f"  {shim} {model}  {count}  total {_number(model_usage.total_tokens)}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)
