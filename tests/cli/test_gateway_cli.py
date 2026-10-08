"""CLI gateway/quotas nouns and personal ``serve --gateway`` supervision wiring."""

from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from pitwall import cli
from pitwall.cli import gateway as cli_gateway
from pitwall.cli import personal as cli_personal
from pitwall.cli.gateway import _countdown, _headroom_bar
from pitwall.personal.gateway import GatewaySupervisor
from pitwall.personal.state import PersonalLease

LOCK = {
    "upstream_version": "3.8.51",
    "tarball_sha256": "0" * 64,
    "extractor_sha256": "1" * 64,
    "steady_monthly": 1_000_000,
    "pool_count": 2,
    "avoid_list": ["ai21"],
    "row_count": 2,
    "registry_covered": 2,
    "known_unreachable": ["agy"],
}

_CATALOG_META_ZED = {
    "free_type": "steady_monthly",
    "tos": "ok",
    "trains_on_prompts": False,
    "hard_stop_guaranteed": True,
    "pool_key": "alpha-pool",
    "monthly_tokens": 1_000_000,
    "credit_tokens": 0,
    "eligibility_gate": None,
    "display_name": "Zed Mini",
    "upstream_format": "openai",
    "executor": "default",
    "auth_type": "apikey",
    "direct_ok": False,
}

_CATALOG_META_KEYLESS = {
    "free_type": "keyless",
    "tos": "caution",
    "trains_on_prompts": True,
    "hard_stop_guaranteed": False,
    "pool_key": None,
    "monthly_tokens": 0,
    "credit_tokens": 0,
    "eligibility_gate": None,
    "display_name": "Keyless Demo",
    "upstream_format": "openai",
    "executor": "default",
    "auth_type": "apikey",
    "direct_ok": True,
}


def _provider(name: str, meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "capability": "coding.chat",
        "endpoint_id": name.removeprefix("gw-")[:64],
        "provider_type": "openai_gateway",
        "adapter": "openai_gateway",
        "region": "GLOBAL",
        "priority": 60,
        "enabled": True,
        "fallback_chain": [],
        "gateway": {
            "base_url": f"https://{name}.example/v1",
            "model_id": f"{name}-model",
            "catalog": meta,
        },
    }


CATALOG = {
    "schema_version": 1,
    "curated_at": "2026-09-10",
    "totals": {"steady_monthly": 1_000_000},
    "avoid_list": ["ai21"],
    "providers": [
        _provider("gw-zed", _CATALOG_META_ZED),
        _provider("gw-keyless", _CATALOG_META_KEYLESS),
    ],
}


def _workspace(
    tmp_path: Path,
    *,
    lock: dict[str, Any] | None = LOCK,
    catalog: dict[str, Any] | None = CATALOG,
    seed_rows: int = 2,
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "seed").mkdir()
    if lock is not None:
        (tmp_path / "config" / "gateway-catalog.lock.json").write_text(
            json.dumps(lock), encoding="utf-8"
        )
    if catalog is not None:
        (tmp_path / "config" / "gateway-catalog.json").write_text(
            json.dumps(catalog), encoding="utf-8"
        )
    lines = ["providers:"] + [f"  - name: gw-{index}" for index in range(seed_rows)]
    (tmp_path / "seed" / "gateway-providers.yaml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    (tmp_path / "seed" / "gateway-capabilities.yaml").write_text(
        "capabilities:\n  - name: coding.chat\n", encoding="utf-8"
    )


def test_gateway_sync_delegates_to_the_sync_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.gateway_catalog import sync as sync_catalog

    seen: list[list[str]] = []
    monkeypatch.setattr(sync_catalog, "main", lambda argv, **_: seen.append(argv) or 0)

    assert (
        cli.main(["gateway", "sync", "--version", "3.8.51", "--from-json", "extracted.json"]) == 0
    )
    assert seen == [["--version", "3.8.51", "--from-json", "extracted.json"]]

    assert cli.main(["gateway", "sync", "--version", "3.9.0"]) == 0
    assert seen[-1] == ["--version", "3.9.0"]


def test_gateway_sync_applies_benchmark_verdicts_to_the_seed(tmp_path: Path) -> None:
    """The benchmark dossier tells operators to run `pitwall gateway sync --apply-verdicts`."""
    import shutil

    import yaml

    from tools.gateway.bench_free_pools import BenchReport, BenchRow, render_markdown

    seed = tmp_path / "gateway-providers.yaml"
    shutil.copy(Path(__file__).resolve().parents[2] / "seed" / "gateway-providers.yaml", seed)
    rows = yaml.safe_load(seed.read_text(encoding="utf-8"))["providers"]
    killed, kept = [r["name"] for r in rows if r["enabled"]][:2]

    def row(provider: str, verdict: str) -> BenchRow:
        return BenchRow(provider, "m", 30, 30, 0, 1.0, 1.0, Decimal("0"), verdict)  # type: ignore[arg-type]  # reason: test row passes a plain str where BenchRow expects Literal keep/kill

    dossier = tmp_path / "dossier.md"
    dossier.write_text(render_markdown(BenchReport((row(killed, "kill"), row(kept, "keep")))))

    assert cli.main(["gateway", "sync", "--apply-verdicts", str(dossier), "--seed", str(seed)]) == 0
    after = {r["name"]: r["enabled"] for r in yaml.safe_load(seed.read_text())["providers"]}
    assert after[killed] is False
    assert after[kept] is True


def test_gateway_sync_passes_through_tool_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from pitwall.gateway_catalog import sync as sync_catalog

    monkeypatch.setattr(sync_catalog, "main", lambda argv, **_: 7)

    assert cli.main(["gateway", "sync", "--version", "3.8.51"]) == 7


def test_gateway_status_reads_the_local_lock_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PITWALL_API_URL", raising=False)
    _workspace(tmp_path)

    assert cli.main(["gateway", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "local"
    assert payload["quotas"] is None
    assert payload["lock"]["upstream_version"] == "3.8.51"
    assert payload["lock"]["row_count"] == 2


def test_gateway_status_text_mentions_the_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PITWALL_API_URL", raising=False)
    _workspace(tmp_path)

    assert cli.main(["gateway", "status"]) == 0
    out = capsys.readouterr().out
    assert "3.8.51" in out
    assert "rows=2" in out
    assert "PITWALL_API_URL" in out


def test_gateway_status_includes_live_quotas_when_api_url_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _workspace(tmp_path)
    monkeypatch.setenv("PITWALL_API_URL", "http://api.invalid")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "quotas": [
                    {
                        "provider_id": "gw-zed",
                        "pool_key": "alpha-pool",
                        "free_type": "steady_monthly",
                        "used_units": "200000",
                        "budget_units": "1000000",
                        "headroom": 0.8,
                        "reset_at": None,
                        "tos_verdict": "ok",
                        "lockout": None,
                    }
                ]
            },
        )

    monkeypatch.setattr(cli_gateway, "_REMOTE_TRANSPORT", httpx.MockTransport(handler))

    assert cli.main(["gateway", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "api"
    assert seen["url"] == "http://api.invalid/v1/quotas"
    assert payload["quotas"][0]["provider"] == "gw-zed"
    assert payload["quotas"][0]["headroom"] == 0.8


def test_gateway_status_fails_without_a_catalog_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    monkeypatch.delenv("PITWALL_API_URL", raising=False)
    monkeypatch.setenv("COLUMNS", "200")

    assert cli.main(["gateway", "status"]) == 1
    assert "gateway-catalog.lock.json" in capsys.readouterr().err


def _quiet_doctor_env(monkeypatch: pytest.MonkeyPatch, *, healthy: bool = True) -> None:
    monkeypatch.delenv("PITWALL_GATEWAY_URL", raising=False)
    monkeypatch.setattr(GatewaySupervisor, "health", lambda self: healthy)


def test_gateway_doctor_passes_when_lock_seeds_and_front_door_agree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _workspace(tmp_path)
    _quiet_doctor_env(monkeypatch)

    assert cli.main(["gateway", "doctor", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert [check["name"] for check in payload["checks"]] == [
        "catalog_lock",
        "seeds_match_lock",
        "gateway_health",
        "front_door_loopback",
    ]
    assert all(check["ok"] for check in payload["checks"])


def test_gateway_doctor_detects_seed_drift(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _workspace(tmp_path, seed_rows=5)
    _quiet_doctor_env(monkeypatch)

    assert cli.main(["gateway", "doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    seeds = next(check for check in payload["checks"] if check["name"] == "seeds_match_lock")
    assert seeds["ok"] is False


def test_gateway_doctor_detects_a_missing_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _workspace(tmp_path, lock=None)
    _quiet_doctor_env(monkeypatch)

    assert cli.main(["gateway", "doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    lock_check = next(check for check in payload["checks"] if check["name"] == "catalog_lock")
    assert lock_check["ok"] is False


def test_gateway_doctor_rejects_a_non_loopback_front_door(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _workspace(tmp_path)
    _quiet_doctor_env(monkeypatch)
    monkeypatch.setenv("PITWALL_GATEWAY_URL", "http://0.0.0.0:20130")

    assert cli.main(["gateway", "doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    front_door = next(
        check for check in payload["checks"] if check["name"] == "front_door_loopback"
    )
    assert front_door["ok"] is False


def test_gateway_doctor_reports_an_unhealthy_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    _workspace(tmp_path)
    _quiet_doctor_env(monkeypatch, healthy=False)

    assert cli.main(["gateway", "doctor", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    health = next(check for check in payload["checks"] if check["name"] == "gateway_health")
    assert health["ok"] is False


def test_quotas_renders_the_local_burn_down_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PITWALL_API_URL", raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    _workspace(tmp_path)

    assert cli.main(["quotas"]) == 0
    out = capsys.readouterr().out
    for header in (
        "PROVIDER",
        "POOL",
        "FREE TYPE",
        "USED/BUDGET",
        "HEADROOM",
        "RESET",
        "TOS",
        "LOCKOUT",
    ):
        assert header in out
    assert "gw-zed" in out
    assert "alpha-pool" in out
    assert "steady_monthly" in out
    assert "0/1000000" in out
    assert "[##########] 100%" in out
    assert "ok" in out


def test_quotas_json_rows_include_budget_headroom_tos_and_lockout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PITWALL_API_URL", raising=False)
    _workspace(tmp_path)

    assert cli.main(["quotas", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "local"
    [zed] = [row for row in payload["quotas"] if row["provider"] == "gw-zed"]
    assert zed["pool"] == "alpha-pool"
    assert zed["free_type"] == "steady_monthly"
    assert zed["used"] == "0"
    assert zed["budget"] == "1000000"
    assert zed["headroom"] == 1.0
    assert zed["tos"] == "ok"
    assert zed["lockout"] is None
    keyless = next(row for row in payload["quotas"] if row["provider"] == "gw-keyless")
    assert keyless["tos"] == "caution"
    assert keyless["budget"] is None


def test_quotas_uses_the_api_when_it_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PITWALL_API_URL", "http://api.invalid")

    def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "quotas": [
                    {
                        "provider_id": "gw-zed",
                        "pool_key": "alpha-pool",
                        "free_type": "steady_monthly",
                        "used_units": "200000",
                        "budget_units": "1000000",
                        "headroom": 0.8,
                        "reset_at": "2026-09-12T15:00:00+00:00",
                        "tos_verdict": "ok",
                        "lockout": {
                            "failures": 1,
                            "locked_until": None,
                            "reason": "quota_exhausted",
                            "permanent": False,
                        },
                    }
                ]
            },
        )

    monkeypatch.setattr(cli_gateway, "_REMOTE_TRANSPORT", httpx.MockTransport(handler))

    assert cli.main(["quotas", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source"] == "api"
    [row] = payload["quotas"]
    assert row["provider"] == "gw-zed"
    assert row["used"] == "200000"
    assert row["budget"] == "1000000"
    assert row["headroom"] == 0.8
    assert row["lockout"]["reason"] == "quota_exhausted"


def test_quota_row_rendering_helpers() -> None:
    assert _headroom_bar(1.0) == "[##########] 100%"
    assert _headroom_bar(0.4) == "[####------] 40%"
    assert _headroom_bar(0.0) == "[----------] 0%"
    assert _headroom_bar(None) == "—"
    now = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
    assert _countdown(now + dt.timedelta(days=2, hours=3), now=now) == "2d 3h"
    assert _countdown(now + dt.timedelta(minutes=45), now=now) == "45m"
    assert _countdown(now - dt.timedelta(minutes=1), now=now) == "now"
    assert _countdown(None, now=now) == "—"


def test_usage_lists_gateway_and_quotas(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    first_line = out.splitlines()[0]
    assert "|gateway|" in first_line
    assert "|quotas|" in first_line
    assert "pitwall gateway" in out
    assert "pitwall quotas" in out


def _lease() -> PersonalLease:
    now = dt.datetime(2026, 9, 10, 12, 0, tzinfo=dt.UTC)
    return PersonalLease(
        route="ornith",
        pod_id="pod-1",
        model="m",
        served_model_id="served",
        engine="llama.cpp",
        variant=None,
        image="example/image",
        gpu_class="g",
        gpu_count=1,
        cloud="community",
        price_per_hour_usd=str(Decimal("1")),
        endpoint_url="https://pod-1-8000.proxy.runpod.net/v1",
        key_env="PITWALL_ENDPOINT_KEY",
        launched_at=now,
        deadline_at=now + dt.timedelta(minutes=45),
        state="ready",
    )


class _FakeService:
    def __init__(self) -> None:
        self.gateways: list[Any] = []
        self.served: list[str] = []
        self.stopped: list[str] = []

    def with_gateway(self, gateway: Any) -> None:
        self.gateways.append(gateway)

    async def serve(self, spec: Any, *, progress: Any = None) -> PersonalLease:
        del spec, progress
        self.served.append("ornith")
        return _lease()

    async def status(self) -> list[PersonalLease]:
        return [_lease()]

    async def stop(self, route: str) -> PersonalLease:
        self.stopped.append(route)
        return _lease()


def _personal_backend(monkeypatch: pytest.MonkeyPatch, service: _FakeService) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "k")
    monkeypatch.setattr(cli_personal, "_build_service", lambda settings: service)


def _real_service_with_fake_provider(tmp_path: Path) -> Any:
    from unittest.mock import AsyncMock

    from pitwall.config import PitwallSettings
    from pitwall.personal.service import PersonalServeService
    from pitwall.personal.state import StateStore
    from tests.personal.test_service import _FakeCatalogue, _FakeRoutes, _FakeRunPod
    from tests.tui.test_personal_screens import _preview

    preview = _preview()
    backend = _FakeRunPod()
    service = PersonalServeService(
        store=StateStore(tmp_path / "state"),
        settings=PitwallSettings(),
        catalogue=_FakeCatalogue(),
        runpod=backend,
        routes=_FakeRoutes(),
        endpoint_key="fixture-key",
        verify=AsyncMock(return_value=[preview.plan.model_id]),
        cuda_versions=AsyncMock(return_value=("12.8", "13.0")),
    )
    service.plan = AsyncMock(return_value=preview)
    return service, backend


def test_serve_gateway_starts_the_supervisor_before_launching(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    service, backend = _real_service_with_fake_provider(tmp_path)
    _personal_backend(monkeypatch, service)
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", "tok")
    started: list[Any] = []
    monkeypatch.setattr(GatewaySupervisor, "start", lambda self: started.append(self))

    assert (
        cli.main(
            [
                "serve",
                "--model",
                "m",
                "--gpu-class",
                "NVIDIA GeForce RTX 3090",
                "--route",
                "ornith",
                "--max-usd-per-hour",
                "1",
                "--gateway",
                "--json",
            ]
        )
        == 0
    )
    assert len(started) == 1
    assert service._gateway is started[0]
    assert len(backend.created) == 1
    assert json.loads(capsys.readouterr().out)["route"] == "ornith"


def test_serve_gateway_fails_fast_without_a_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    service, backend = _real_service_with_fake_provider(tmp_path)
    _personal_backend(monkeypatch, service)
    monkeypatch.delenv("PITWALL_GATEWAY_TOKEN", raising=False)

    assert (
        cli.main(
            [
                "serve",
                "--model",
                "m",
                "--gpu-class",
                "NVIDIA GeForce RTX 3090",
                "--route",
                "ornith",
                "--max-usd-per-hour",
                "1",
                "--gateway",
            ]
        )
        == 2
    )
    assert "PITWALL_GATEWAY_TOKEN" in capsys.readouterr().err
    assert backend.created == []


def test_serve_without_gateway_never_touches_the_supervisor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = _FakeService()
    _personal_backend(monkeypatch, service)
    monkeypatch.setattr(
        GatewaySupervisor,
        "start",
        MagicMock(side_effect=AssertionError("supervisor must not start without --gateway")),
    )

    assert (
        cli.main(
            [
                "serve",
                "--model",
                "m",
                "--gpu-class",
                "g",
                "--route",
                "ornith",
                "--max-usd-per-hour",
                "1",
                "--json",
            ]
        )
        == 0
    )
    assert service.gateways == []


def test_stop_all_stops_the_gateway_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = _FakeService()
    _personal_backend(monkeypatch, service)
    stopped_gateways: list[Any] = []
    monkeypatch.setattr(GatewaySupervisor, "stop", lambda self: stopped_gateways.append(self))

    assert cli.main(["stop", "--all", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["gateway_stopped"] is True
    assert service.stopped == ["ornith"]
    assert len(stopped_gateways) == 1


def test_stop_single_route_leaves_the_gateway_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    service = _FakeService()
    _personal_backend(monkeypatch, service)
    monkeypatch.setattr(
        GatewaySupervisor,
        "stop",
        MagicMock(side_effect=AssertionError("single-route stop must not touch the gateway")),
    )

    assert cli.main(["stop", "ornith", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["stopped"][0]["route"] == "ornith"
    assert service.stopped == ["ornith"]


def test_installed_gateway_catalog_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "site-packages" / "pitwall"
    data = package / "gateway_catalog" / "data"
    data.mkdir(parents=True)
    _workspace(data)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setattr(cli_gateway, "__file__", str(package / "cli" / "gateway.py"))
    monkeypatch.setattr(cli_gateway, "_REPO_ROOT", tmp_path / "absent-source")
    assert cli_gateway._workspace_root() == data
    lock = cli_gateway._read_json(data / cli_gateway._LOCK_RELPATH)
    assert cli_gateway._catalog_lock_check(data, lock)["ok"]
    assert cli_gateway._seeds_check(data, lock)["ok"]
    # An explicitly present but incomplete workspace must not silently use defaults.
    (outside / "config").mkdir()
    assert cli_gateway._workspace_root() == outside
    assert not cli_gateway._catalog_lock_check(outside, None)["ok"]


@pytest.mark.parametrize("contents", [None, "{not json"], ids=["missing", "malformed"])
def test_gateway_sync_reports_an_unreadable_catalog_json_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], contents: str | None
) -> None:
    source = tmp_path / "catalog.json"
    if contents is not None:
        source.write_text(contents)

    code = cli.main(
        [
            "gateway",
            "sync",
            "--version",
            "0.0.0",
            "--from-json",
            str(source),
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert code == 2
    assert "cannot read --from-json" in capsys.readouterr().err
    assert not (tmp_path / "config").exists(), "nothing is written from an unreadable catalog"


def test_workspace_fallbacks_resolve_from_the_package_location() -> None:
    """The repo root is found from ``pitwall/cli/gateway.py``, two directories below ``src``."""
    assert (cli_gateway._REPO_ROOT / "pyproject.toml").is_file()
    assert (cli_gateway._REPO_ROOT / "config").is_dir()


def _exit_code(argv: list[str]) -> int:
    try:
        return cli.main(argv)
    except SystemExit as exc:
        assert isinstance(exc.code, int)
        return exc.code


def test_gateway_serve_help_lists_its_flags_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PITWALL_GATEWAY_TOKEN", raising=False)
    assert _exit_code(["gateway", "serve", "--help"]) == 0
    out = capsys.readouterr().out
    assert "--port" in out
    assert "--bind" in out


def test_gateway_serve_rejects_an_unknown_flag_with_a_usage_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PITWALL_GATEWAY_TOKEN", raising=False)
    assert _exit_code(["gateway", "serve", "--j36-not-a-flag"]) == 2
    assert "unrecognized arguments" in capsys.readouterr().err


def test_gateway_serve_rejects_a_flag_without_its_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PITWALL_GATEWAY_TOKEN", raising=False)
    assert _exit_code(["gateway", "serve", "--port"]) == 2
    assert "expected one argument" in capsys.readouterr().err


def test_gateway_serve_without_a_token_exits_one_with_the_token_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("PITWALL_GATEWAY_TOKEN", raising=False)
    assert _exit_code(["gateway", "serve"]) == 1
    assert "PITWALL_GATEWAY_TOKEN is required" in capsys.readouterr().err


def test_gateway_serve_passes_port_and_bind_to_the_gateway(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", "t")
    monkeypatch.setenv("PITWALL_GATEWAY_UPSTREAM_URL", "http://upstream.invalid/v1")
    seen: dict[str, Any] = {}

    def fake_run(_app: Any, **kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)
    assert _exit_code(["gateway", "serve", "--port", "20131", "--bind", "127.0.0.2"]) == 0
    assert (seen["host"], seen["port"]) == ("127.0.0.2", 20131)


def test_gateway_serve_reports_an_invalid_port_as_a_start_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PITWALL_GATEWAY_TOKEN", "t")
    assert _exit_code(["gateway", "serve", "--port", "70000"]) == 1
    assert "Invalid --port value" in capsys.readouterr().err
