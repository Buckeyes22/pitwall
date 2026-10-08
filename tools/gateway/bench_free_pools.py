"""Free-pool benchmark tool for the Phase 0 dossier (research §14; plan Task 17).

Probes each free pool at a fixed requests-per-minute schedule, records request
counts, 429s, and latency percentiles, and emits a keep/kill verdict per pool:

    keep  iff  ok/requests >= 0.95  and  p95_ms <= 8000  and  cost_usd == 0

The tool is hermetic by construction: ``run`` accepts an ``httpx`` transport,
a clock, and a pacing callable so tests run without network or real sleeps.
The live benchmark is operator-executed (plan Step 3) and never runs in pytest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx

Verdict = Literal["keep", "kill"]

KEEP_MIN_OK_RATIO = 0.95
KEEP_MAX_P95_MS = 8000.0
_REQUEST_TIMEOUT_S = 30.0
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CATALOG_PATH = _REPO_ROOT / "config" / "gateway-catalog.json"


@dataclass(frozen=True, slots=True)
class BenchPool:
    """One pool to benchmark: seed name, upstream model id, and OpenAI base URL."""

    provider: str
    model: str
    base_url: str


@dataclass(frozen=True, slots=True)
class BenchRow:
    """One pool's benchmark outcome with its keep/kill verdict."""

    provider: str
    model: str
    requests: int
    ok: int
    http_429: int
    p50_ms: float
    p95_ms: float
    cost_usd: Decimal
    verdict: Verdict


@dataclass(frozen=True, slots=True)
class BenchReport:
    """Benchmark outcome across every probed pool."""

    rows: tuple[BenchRow, ...]


def verdict_for(*, ok: int, requests: int, p95_ms: float, cost_usd: Decimal) -> Verdict:
    """ADR 0007 Phase 0 keep/kill rule applied to one pool's tallies."""

    if requests <= 0 or ok / requests < KEEP_MIN_OK_RATIO:
        return "kill"
    if p95_ms > KEEP_MAX_P95_MS or cost_usd != 0:
        return "kill"
    return "keep"


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile over ascending values."""

    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(q * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


async def run(
    pools: Sequence[BenchPool],
    *,
    minutes: int,
    rpm: int,
    transport: httpx.AsyncBaseTransport | None = None,
    clock: Callable[[], float] | None = None,
    pace: Callable[[float], Awaitable[None]] | None = None,
    gateway_token: str | None = None,
) -> BenchReport:
    """Probe every pool ``minutes * rpm`` times, paced at ``rpm`` requests/minute.

    Pools are probed concurrently, so the whole run lasts ``minutes``. A pool on the
    loopback gateway is addressed the way the broker addresses it: its provider name
    in ``x-pitwall-route`` and ``gateway_token`` as the bearer token.

    ``clock`` defaults to ``time.monotonic`` and ``pace`` to ``asyncio.sleep``;
    hermetic runs inject a fixed clock plus a no-op pace so latency numbers and
    schedules are deterministic without network or real waiting.
    """

    if minutes <= 0:
        raise ValueError("minutes must be positive")
    if rpm <= 0:
        raise ValueError("rpm must be positive")
    measure = time.monotonic if clock is None else clock
    wait = asyncio.sleep if pace is None else pace
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S, transport=transport) as client:
        rows = await asyncio.gather(
            *(
                _bench_pool(
                    client,
                    pool,
                    minutes=minutes,
                    rpm=rpm,
                    measure=measure,
                    wait=wait,
                    headers=_pool_headers(pool, gateway_token),
                )
                for pool in pools
            )
        )
    return BenchReport(rows=tuple(rows))


def _pool_headers(pool: BenchPool, gateway_token: str | None) -> dict[str, str]:
    """Fork headers for a loopback gateway pool; none for a direct keyless upstream."""

    if urlparse(pool.base_url).hostname not in _LOOPBACK_HOSTS:
        return {}
    headers = {"x-pitwall-route": pool.provider}
    if gateway_token:
        headers["Authorization"] = f"Bearer {gateway_token}"
    return headers


async def _bench_pool(
    client: httpx.AsyncClient,
    pool: BenchPool,
    *,
    minutes: int,
    rpm: int,
    measure: Callable[[], float],
    wait: Callable[[float], Awaitable[None]],
    headers: dict[str, str],
) -> BenchRow:
    interval_s = 60.0 / rpm
    url = pool.base_url.rstrip("/") + "/chat/completions"

    def body(index: int) -> dict[str, object]:
        # A fresh prompt per request: an upstream that caches identical prompts would
        # otherwise answer a keyless pool that can no longer generate.
        prompt = f"ping {uuid.uuid4().hex} #{index}"
        return {
            "model": pool.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1,
            "stream": False,
        }

    latencies: list[float] = []
    ok = 0
    http_429 = 0
    started = measure()
    for index in range(minutes * rpm):
        started_at = measure()
        status = 0
        try:
            response = await client.post(url, json=body(index), headers=headers)
            status = response.status_code
        except httpx.HTTPError:
            status = 0
        latencies.append((measure() - started_at) * 1000.0)
        if status == 200:
            ok += 1
        elif status == 429:
            http_429 += 1
        next_at = started + (index + 1) * interval_s
        await wait(max(0.0, next_at - measure()))
    ordered = sorted(latencies)
    p50 = _percentile(ordered, 0.5)
    p95 = _percentile(ordered, 0.95)
    cost_usd = Decimal("0")
    return BenchRow(
        provider=pool.provider,
        model=pool.model,
        requests=len(latencies),
        ok=ok,
        http_429=http_429,
        p50_ms=p50,
        p95_ms=p95,
        cost_usd=cost_usd,
        verdict=verdict_for(ok=ok, requests=len(latencies), p95_ms=p95, cost_usd=cost_usd),
    )


def render_markdown(report: BenchReport) -> str:
    """Render the dossier section: a results table plus a keep/kill line per pool."""

    lines = [
        "## Results",
        "",
        "| Provider | Model | Requests | OK | HTTP 429 | p50 ms | p95 ms | Cost (USD) | Verdict |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report.rows:
        lines.append(
            f"| {row.provider} | {row.model} | {row.requests} | {row.ok} | {row.http_429} "
            f"| {row.p50_ms:.1f} | {row.p95_ms:.1f} | {row.cost_usd} | {row.verdict} |"
        )
    lines.extend(
        [
            "",
            "## Verdicts",
            "",
            "Keep/kill per pool (`kill` rows flip `enabled: false` via "
            "`pitwall gateway sync --apply-verdicts`):",
            "",
        ]
    )
    lines.extend(f"- {row.provider}: {row.verdict}" for row in report.rows)
    return "\n".join(lines) + "\n"


def load_pools(
    catalog_path: Path = _CATALOG_PATH,
    *,
    free_types: Sequence[str] = ("keyless",),
) -> tuple[BenchPool, ...]:
    """Read benchmarkable pools from the synced catalog, filtered by free type."""

    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    wanted = frozenset(free_types)
    pools: list[BenchPool] = []
    for entry in payload["providers"]:
        gateway = entry.get("gateway") or {}
        catalog = gateway.get("catalog") or {}
        if catalog.get("free_type") not in wanted or entry.get("enabled", True) is False:
            continue
        pools.append(
            BenchPool(
                provider=str(entry["name"]),
                model=str(gateway["model_id"]),
                base_url=str(gateway["base_url"]),
            )
        )
    return tuple(pools)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pools",
        default="keyless",
        help="Comma-separated free_type filter over the synced catalog (default: keyless).",
    )
    parser.add_argument("--minutes", type=int, default=10, help="Benchmark length in minutes.")
    parser.add_argument("--rpm", type=int, default=6, help="Requests per minute per pool.")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Write the markdown dossier here instead of stdout.",
    )
    args = parser.parse_args(argv)
    free_types = tuple(part.strip() for part in args.pools.split(",") if part.strip())
    pools = load_pools(free_types=free_types)
    if not pools:
        print(f"no pools matched --pools {args.pools!r} in {_CATALOG_PATH}", file=sys.stderr)
        return 1
    report = asyncio.run(
        run(
            pools,
            minutes=args.minutes,
            rpm=args.rpm,
            gateway_token=os.environ.get("PITWALL_GATEWAY_TOKEN") or None,
        )
    )
    text = render_markdown(report)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {len(report.rows)} pool rows to {args.out}")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
