"""Free-pool benchmark harness: hermetic counts, latency, verdicts, dossier wiring (Task 17)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tests.hang_guard import HANG_GUARD_SECS
from tools.gateway.bench_free_pools import (
    BenchPool,
    BenchReport,
    BenchRow,
    load_pools,
    render_markdown,
    run,
    verdict_for,
)
from tools.gateway.sync_catalog import apply_verdicts
from tools.gateway.sync_catalog import main as sync_main


class FixedClock:
    """Deterministic clock: every read advances a fixed tick; latency equals tick + injection."""

    def __init__(self, tick_s: float) -> None:
        self.tick_s = tick_s
        self.now_s = 0.0

    def __call__(self) -> float:
        self.now_s += self.tick_s
        return self.now_s

    def advance(self, seconds: float) -> None:
        self.now_s += seconds


def _pools() -> list[BenchPool]:
    return [
        BenchPool(provider="gw-alpha-a1", model="alpha/a1", base_url="https://bench.test/v1"),
        BenchPool(provider="gw-beta-b1", model="beta/b1", base_url="https://bench.test/v1"),
        BenchPool(provider="gw-gamma-g1", model="gamma/g1", base_url="https://bench.test/v1"),
    ]


def _ok_body(model: str) -> dict[str, object]:
    return {
        "choices": [{"message": {"content": "pong"}}],
        "model": model,
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _handler(
    clock: FixedClock,
    *,
    four_twenty_nine_after: dict[str, int] | None = None,
    inject_ms: dict[str, float] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    counts: dict[str, int] = {}
    limits = four_twenty_nine_after or {}
    injections = inject_ms or {}

    def handler(request: httpx.Request) -> httpx.Response:
        model = str(json.loads(request.content)["model"])
        served = counts.get(model, 0)
        counts[model] = served + 1
        if injections.get(model):
            clock.advance(injections[model] / 1000.0)
        limit = limits.get(model)
        if limit is not None and served >= limit:
            return httpx.Response(429, json={"error": {"message": "rate limited"}})
        return httpx.Response(200, json=_ok_body(model))

    return handler


async def _run_hermetic(
    pools: list[BenchPool],
    handler: Callable[[httpx.Request], httpx.Response],
    clock: FixedClock,
) -> tuple[BenchReport, list[float]]:
    delays: list[float] = []

    async def pace(seconds: float) -> None:
        delays.append(seconds)
        await asyncio.sleep(0)

    report = await run(
        pools,
        minutes=2,
        rpm=6,
        transport=httpx.MockTransport(handler),
        clock=clock,
        pace=pace,
    )
    return report, delays


async def test_run_counts_requests_ok_and_429_and_keeps_free_cost_at_zero() -> None:
    clock = FixedClock(tick_s=0.2)
    report, _delays = await _run_hermetic(
        _pools(),
        _handler(clock, four_twenty_nine_after={"beta/b1": 10, "gamma/g1": 0}),
        clock,
    )

    assert report.rows[0].provider == "gw-alpha-a1"
    assert report.rows[0].requests == 12
    assert report.rows[0].ok == 12
    assert report.rows[0].http_429 == 0
    assert report.rows[0].verdict == "keep"
    assert report.rows[0].cost_usd == Decimal("0")
    # beta serves 10 oks then rate limits: 10/12 is below the 0.95 keep bar.
    assert report.rows[1].requests == 12
    assert report.rows[1].ok == 10
    assert report.rows[1].http_429 == 2
    assert report.rows[1].verdict == "kill"
    # gamma never serves a 200.
    assert report.rows[2].ok == 0
    assert report.rows[2].http_429 == 12
    assert report.rows[2].verdict == "kill"


async def test_run_measures_latency_percentiles_from_the_injected_clock() -> None:
    clock = FixedClock(tick_s=0.2)
    flat = [BenchPool(provider="gw-alpha-a1", model="alpha/a1", base_url="https://bench.test/v1")]
    report, _delays = await _run_hermetic(flat, _handler(clock), clock)
    assert report.rows[0].p50_ms == pytest.approx(200.0)
    assert report.rows[0].p95_ms == pytest.approx(200.0)

    slow = [BenchPool(provider="gw-beta-b1", model="beta/b1", base_url="https://bench.test/v1")]
    report, _delays = await _run_hermetic(
        slow, _handler(clock, inject_ms={"beta/b1": 400.0}), clock
    )
    assert report.rows[0].p50_ms == pytest.approx(600.0)
    assert report.rows[0].p95_ms == pytest.approx(600.0)


async def test_run_spaces_requests_on_the_rpm_schedule_without_real_sleeps() -> None:
    clock = FixedClock(tick_s=0.2)
    flat = [BenchPool(provider="gw-alpha-a1", model="alpha/a1", base_url="https://bench.test/v1")]
    _report, delays = await _run_hermetic(flat, _handler(clock), clock)

    assert len(delays) == 12
    assert all(delay >= 0.0 for delay in delays)


def test_verdict_keep_requires_ok_ratio_p95_ceiling_and_zero_cost() -> None:
    assert verdict_for(ok=19, requests=20, p95_ms=8000.0, cost_usd=Decimal("0")) == "keep"
    assert verdict_for(ok=20, requests=20, p95_ms=0.0, cost_usd=Decimal("0")) == "keep"
    assert verdict_for(ok=18, requests=20, p95_ms=10.0, cost_usd=Decimal("0")) == "kill"
    assert verdict_for(ok=19, requests=20, p95_ms=8000.001, cost_usd=Decimal("0")) == "kill"
    assert verdict_for(ok=20, requests=20, p95_ms=10.0, cost_usd=Decimal("0.01")) == "kill"
    assert verdict_for(ok=0, requests=0, p95_ms=0.0, cost_usd=Decimal("0")) == "kill"


def test_render_markdown_has_result_table_and_a_keep_kill_line_per_pool() -> None:
    report = BenchReport(
        rows=(
            BenchRow(
                provider="gw-alpha-a1",
                model="alpha/a1",
                requests=12,
                ok=12,
                http_429=0,
                p50_ms=200.0,
                p95_ms=210.0,
                cost_usd=Decimal("0"),
                verdict="keep",
            ),
            BenchRow(
                provider="gw-beta-b1",
                model="beta/b1",
                requests=12,
                ok=10,
                http_429=2,
                p50_ms=300.0,
                p95_ms=340.0,
                cost_usd=Decimal("0"),
                verdict="kill",
            ),
        )
    )

    markdown = render_markdown(report)

    assert (
        "| Provider | Model | Requests | OK | HTTP 429 | p50 ms | p95 ms | Cost (USD) | Verdict |"
        in markdown
    )
    assert "| gw-alpha-a1 | alpha/a1 | 12 | 12 | 0 | 200.0 | 210.0 | 0 | keep |" in markdown
    assert "- gw-alpha-a1: keep" in markdown
    assert "- gw-beta-b1: kill" in markdown


def test_load_pools_filters_catalog_rows_by_free_type(tmp_path: Path) -> None:
    catalog = {
        "providers": [
            {
                "name": "gw-alpha-a1",
                "enabled": True,
                "gateway": {
                    "base_url": "https://alpha.example/v1",
                    "model_id": "alpha/a1",
                    "catalog": {"free_type": "keyless"},
                },
            },
            {
                "name": "gw-delta-d1",
                "enabled": True,
                "gateway": {
                    "base_url": "https://delta.example/v1",
                    "model_id": "delta/d1",
                    "catalog": {"free_type": "one-time-initial"},
                },
            },
            {
                "name": "gw-omega-o1",
                "enabled": False,
                "gateway": {
                    "base_url": "https://omega.example/v1",
                    "model_id": "omega/o1",
                    "catalog": {"free_type": "keyless"},
                },
            },
        ]
    }
    path = tmp_path / "gateway-catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")

    pools = load_pools(path, free_types=("keyless",))

    assert pools == (
        BenchPool(provider="gw-alpha-a1", model="alpha/a1", base_url="https://alpha.example/v1"),
    )


SEED_YAML = """providers:
  - name: gw-alpha-a1
    capability: "coding.chat"
    endpoint_id: "alpha-a1"
    provider_type: "openai_gateway"
    adapter: "openai_gateway"
    region: "GLOBAL"
    priority: 50
    enabled: true
    cost:
      mode: zero
    fallback_chain: []
    gateway:
      base_url: "https://alpha.example/v1"
      model_id: "alpha/a1"
      catalog:
        free_type: "keyless"
  - name: gw-beta-b1
    capability: "coding.chat"
    endpoint_id: "beta-b1"
    provider_type: "openai_gateway"
    adapter: "openai_gateway"
    region: "GLOBAL"
    priority: 50
    enabled: true
    cost:
      mode: zero
    fallback_chain: []
    gateway:
      base_url: "https://beta.example/v1"
      model_id: "beta/b1"
      catalog:
        free_type: "keyless"
"""


def _report_with_kill() -> BenchReport:
    return BenchReport(
        rows=(
            BenchRow(
                provider="gw-alpha-a1",
                model="alpha/a1",
                requests=12,
                ok=12,
                http_429=0,
                p50_ms=200.0,
                p95_ms=210.0,
                cost_usd=Decimal("0"),
                verdict="keep",
            ),
            BenchRow(
                provider="gw-beta-b1",
                model="beta/b1",
                requests=12,
                ok=10,
                http_429=2,
                p50_ms=300.0,
                p95_ms=340.0,
                cost_usd=Decimal("0"),
                verdict="kill",
            ),
        )
    )


def _enabled_by_name(seed_text: str) -> dict[str, bool]:
    enabled: dict[str, bool] = {}
    current: str | None = None
    for line in seed_text.splitlines():
        if line.startswith("  - name: "):
            current = line.removeprefix("  - name: ").strip()
        elif line.startswith("    enabled:") and current is not None:
            enabled[current] = line.endswith("true")
    return enabled


def test_apply_verdicts_flips_only_kill_rows_in_the_seed(tmp_path: Path) -> None:
    seed = tmp_path / "gateway-providers.yaml"
    seed.write_text(SEED_YAML, encoding="utf-8")
    dossier = tmp_path / "dossier.md"
    dossier.write_text(render_markdown(_report_with_kill()), encoding="utf-8")

    flipped = apply_verdicts(dossier.read_text(encoding="utf-8"), seed_path=seed)

    assert flipped == ["gw-beta-b1"]
    enabled = _enabled_by_name(seed.read_text(encoding="utf-8"))
    assert enabled == {"gw-alpha-a1": True, "gw-beta-b1": False}


def test_apply_verdicts_is_idempotent_when_the_row_is_already_disabled(tmp_path: Path) -> None:
    seed = tmp_path / "gateway-providers.yaml"
    seed.write_text(SEED_YAML, encoding="utf-8")
    dossier = tmp_path / "dossier.md"
    dossier.write_text(render_markdown(_report_with_kill()), encoding="utf-8")
    apply_verdicts(dossier.read_text(encoding="utf-8"), seed_path=seed)

    flipped = apply_verdicts(dossier.read_text(encoding="utf-8"), seed_path=seed)

    assert flipped == []
    assert _enabled_by_name(seed.read_text(encoding="utf-8"))["gw-beta-b1"] is False


def test_apply_verdicts_rejects_dossier_providers_missing_from_the_seed(tmp_path: Path) -> None:
    seed = tmp_path / "gateway-providers.yaml"
    seed.write_text(SEED_YAML, encoding="utf-8")

    with pytest.raises(ValueError, match="gw-missing"):
        apply_verdicts("- gw-missing: kill\n", seed_path=seed)


def test_sync_main_apply_verdicts_updates_the_seed_without_a_version(tmp_path: Path) -> None:
    seed = tmp_path / "gateway-providers.yaml"
    seed.write_text(SEED_YAML, encoding="utf-8")
    dossier = tmp_path / "dossier.md"
    dossier.write_text(render_markdown(_report_with_kill()), encoding="utf-8")

    exit_code = sync_main(["--apply-verdicts", str(dossier), "--seed", str(seed)])

    assert exit_code == 0
    assert _enabled_by_name(seed.read_text(encoding="utf-8"))["gw-beta-b1"] is False


def test_sync_main_requires_a_mode() -> None:
    with pytest.raises(SystemExit):
        sync_main([])


async def test_requests_to_the_loopback_gateway_name_the_route_and_carry_the_token() -> None:
    """The route-table gateway refuses a request without x-pitwall-route or the bearer token."""
    seen: list[tuple[str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers.get("x-pitwall-route"), request.headers.get("authorization")))
        return httpx.Response(200, json=_ok_body(str(json.loads(request.content)["model"])))

    async def pace(seconds: float) -> None:
        del seconds

    pools = [
        BenchPool(provider="gw-alpha-a1", model="alpha/a1", base_url="http://127.0.0.1:20130/v1"),
        BenchPool(provider="gw-beta-b1", model="beta/b1", base_url="https://bench.test/v1"),
    ]
    await run(
        pools,
        minutes=1,
        rpm=1,
        transport=httpx.MockTransport(handler),
        pace=pace,
        gateway_token="bench-token",
    )
    # The loopback fork gets the route and token; a direct keyless upstream gets neither.
    assert sorted(seen, key=str) == sorted(
        [("gw-alpha-a1", "Bearer bench-token"), (None, None)], key=str
    )


async def test_pools_are_probed_concurrently_so_minutes_is_the_whole_run() -> None:
    """Each pool waits here until every pool has sent its first request; one at a time never would."""
    first_seen: set[str] = set()
    everyone_started = asyncio.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        model = str(json.loads(request.content)["model"])
        first_seen.add(model)
        if len(first_seen) == len(_pools()):
            everyone_started.set()
        return httpx.Response(200, json=_ok_body(model))

    async def pace(seconds: float) -> None:
        del seconds
        await everyone_started.wait()

    report = await asyncio.wait_for(
        run(_pools(), minutes=1, rpm=2, transport=httpx.MockTransport(handler), pace=pace),
        timeout=HANG_GUARD_SECS,
    )
    assert [row.provider for row in report.rows] == [pool.provider for pool in _pools()]
    assert all(row.requests == 2 for row in report.rows)


async def test_every_probe_prompt_is_unique_so_no_upstream_cache_answers_it() -> None:
    """An upstream that caches identical prompts would pass a keyless pool that cannot generate."""
    prompts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompts.append(body["messages"][-1]["content"])
        return httpx.Response(200, json=_ok_body(str(body["model"])))

    async def pace(seconds: float) -> None:
        del seconds

    await run(_pools(), minutes=1, rpm=3, transport=httpx.MockTransport(handler), pace=pace)
    assert len(prompts) == 9
    assert len(set(prompts)) == len(prompts)
