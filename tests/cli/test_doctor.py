"""pitwall doctor: readiness checks per mode, with fake probes (plan Task 1)."""

from __future__ import annotations

import json
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pitwall import cli
from pitwall.config import PitwallSettings
from pitwall.doctor import (
    BurnFacts,
    DatabaseFacts,
    DoctorCheck,
    DoctorReport,
    ProbeError,
    Probes,
    _database_probe,
    run_doctor,
)
from pitwall.migrations import discover_migrations
from pitwall.personal.keys import ensure_endpoint_key

ALL_VERSIONS = frozenset(m.version for m in discover_migrations())


def _probes(
    *,
    facts: DatabaseFacts | None = None,
    db_error: bool = False,
    redis_error: bool = False,
    api: dict[str, Any] | None = None,
    api_error: bool = False,
    canary: dict[str, Any] | None = None,
) -> Probes:
    async def database(url: str, timeout: float) -> DatabaseFacts:
        if db_error:
            raise ProbeError("cannot connect to Postgres (ConnectionRefusedError)")
        return facts or DatabaseFacts(
            applied_versions=ALL_VERSIONS,
            schema_ready=True,
            enabled_capabilities=1,
            embedding_capabilities=("embedding.demo",),
            enabled_providers=1,
            healthy_providers=1,
            burn=BurnFacts(Decimal("50"), Decimal("1"), Decimal("5")),
        )

    async def redis(url: str, timeout: float) -> None:
        if redis_error:
            raise ProbeError("cannot reach Redis (ConnectionError)")

    async def api_health(url: str, token: str | None, timeout: float) -> dict[str, Any]:
        if api_error:
            raise ProbeError("API not reachable at http://127.0.0.1:8080 (ConnectError)")
        return api or {"status_code": 200, "body": {"ok": True}}

    async def dry_run(
        url: str, token: str | None, capability: str, timeout: float
    ) -> dict[str, Any]:
        return canary or {"status_code": 200, "body": {"result": {"dry_run": True}}}

    return Probes(database=database, redis=redis, api_health=api_health, canary=dry_run)


_REGISTRY_CONFIG = Path(tempfile.mkdtemp(prefix="pitwall-doctor-")) / "pitwall.toml"
_REGISTRY_CONFIG.write_text('[personal]\nbackend = "registry"\n', encoding="utf-8")


def _registry_env() -> dict[str, str]:
    # The registry backend is an explicit `[personal] backend` choice, not DATABASE_URL alone.
    return {
        "PITWALL_CONFIG_FILE": str(_REGISTRY_CONFIG),
        "DATABASE_URL": "postgresql://pitwall:hunter2@127.0.0.1:5444/pitwall_test",
        "REDIS_URL": "redis://127.0.0.1:6380/0",
        "RUNPOD_API_KEY": "rk-secret-value",
        "HOME": "/nonexistent-home",
        "PATH": "/usr/bin:/bin",
    }


def _by_id(report: DoctorReport) -> dict[str, str]:
    return {check.id: check.status for check in report.checks}


def _settings(**overrides: Any) -> PitwallSettings:
    values: dict[str, Any] = {
        "runpod_api_key": "rk-secret-value",
        "database_url": "postgresql://x",
        "redis_url": "redis://x",
    }
    values.update(overrides)
    return PitwallSettings(**values)


@pytest.fixture
def personal_env(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    ensure_endpoint_key(state / "pitwall")
    return {
        "HOME": str(tmp_path),
        "PATH": str(bin_dir),
        "XDG_STATE_HOME": str(state),
        "RUNPOD_API_KEY": "rk-secret-value",
        "PITWALL_ENDPOINT_KEY": "ek-secret-value",
    }


async def test_personal_mode_all_green(personal_env: dict[str, str]) -> None:
    report = await run_doctor(environ=personal_env, probes=_probes())
    assert report.mode == "personal"
    assert report.status == "ok"
    assert _by_id(report) == {
        "install.python": "ok",
        "config.file": "skip",
        "personal.runpod_credential": "ok",
        "personal.endpoint_key": "ok",
        "personal.routing_cli": "ok",
        "personal.leases": "ok",
        "registry.mode": "skip",
    }
    assert report.exit_code() == 0


async def test_registry_mode_skip_names_the_backend_setting(personal_env: dict[str, str]) -> None:
    report = await run_doctor(environ=personal_env, probes=_probes())
    detail = next(check.detail for check in report.checks if check.id == "registry.mode")
    assert "[personal] backend" in detail
    assert "DATABASE_URL" not in detail


async def test_personal_mode_names_the_fixes_and_never_the_secrets(tmp_path: Path) -> None:
    env = {
        "HOME": str(tmp_path),
        "PATH": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "PITWALL_ROUTING_CLI": "no-such-router",
    }
    report = await run_doctor(environ=env, probes=_probes())
    checks = {check.id: check for check in report.checks}
    assert checks["personal.runpod_credential"].status == "fail"
    assert checks["personal.endpoint_key"].status == "fail"
    assert checks["personal.endpoint_key"].next_step == "pitwall setup"
    assert checks["personal.routing_cli"].status == "warn"
    assert report.exit_code() == 1


async def test_registry_mode_all_green() -> None:
    report = await run_doctor(environ=_registry_env(), settings=_settings(), probes=_probes())
    assert report.mode == "registry"
    assert _by_id(report) == {
        "install.python": "ok",
        "config.file": "ok",  # registry mode is chosen in pitwall.toml, so a file is in use
        "config.runtime": "ok",
        "db.connect": "ok",
        "db.migrations": "ok",
        "registry.capabilities": "ok",
        "registry.providers": "ok",
        "redis.connect": "ok",
        "spend.budget": "ok",
        "spend.kill_switch": "ok",
        "spend.burn_rate": "ok",
        "api.health": "ok",
    }
    assert report.status == "ok"


async def test_registry_probes_use_the_loaded_settings_not_raw_environment() -> None:
    seen: dict[str, str] = {}
    base = _probes()

    async def database(url: str, timeout: float) -> DatabaseFacts:
        seen["db"] = url
        return await base.database(url, timeout)

    async def redis(url: str, timeout: float) -> None:
        seen["redis"] = url

    probes = Probes(database=database, redis=redis, api_health=base.api_health, canary=base.canary)
    environ = _registry_env()
    del environ["DATABASE_URL"], environ["REDIS_URL"]  # the URLs come from pitwall.toml only
    settings = _settings(database_url="postgresql://toml-db/x", redis_url="redis://toml-redis/0")

    report = await run_doctor(environ=environ, settings=settings, probes=probes)

    assert seen == {"db": "postgresql://toml-db/x", "redis": "redis://toml-redis/0"}
    assert _by_id(report)["db.connect"] == "ok"
    assert _by_id(report)["redis.connect"] == "ok"


async def test_unreachable_database_skips_its_dependents() -> None:
    report = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(db_error=True)
    )
    ids = _by_id(report)
    assert ids["db.connect"] == "fail"
    for dependent in (
        "db.migrations",
        "registry.capabilities",
        "registry.providers",
        "spend.kill_switch",
        "spend.burn_rate",
    ):
        assert ids[dependent] == "skip", dependent
    assert report.exit_code() == 1


async def test_pending_migrations_kill_switch_and_breach_fail() -> None:
    facts = DatabaseFacts(
        applied_versions=ALL_VERSIONS - {sorted(ALL_VERSIONS)[-1]},
        schema_ready=True,
        enabled_capabilities=1,
        enabled_providers=1,
        healthy_providers=0,
        kill_switch_engaged=True,
        burn=BurnFacts(Decimal("50"), Decimal("60"), Decimal("80")),
    )
    report = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(facts=facts)
    )
    checks = {check.id: check for check in report.checks}
    assert checks["db.migrations"].status == "fail"
    assert sorted(ALL_VERSIONS)[-1] in checks["db.migrations"].detail
    assert checks["db.migrations"].next_step == "pitwall db migrate"
    assert checks["registry.providers"].status == "warn"
    assert checks["spend.kill_switch"].status == "fail"
    assert checks["spend.burn_rate"].status == "fail"


async def test_budget_and_cap_rules() -> None:
    zero = await run_doctor(
        environ=_registry_env(),
        settings=_settings(pitwall_monthly_budget_usd=Decimal("0")),
        probes=_probes(),
    )
    assert _by_id(zero)["spend.budget"] == "fail"
    inverted = await run_doctor(
        environ=_registry_env(),
        settings=_settings(
            pitwall_monthly_budget_usd=Decimal("10"), pitwall_per_request_max_usd=Decimal("20")
        ),
        probes=_probes(),
    )
    assert _by_id(inverted)["spend.budget"] == "warn"


async def test_api_down_is_a_warning_that_strict_mode_fails() -> None:
    report = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(api_error=True)
    )
    assert _by_id(report)["api.health"] == "warn"
    assert (report.exit_code(), report.exit_code(strict=True)) == (0, 1)


async def test_canary_runs_only_for_enabled_embedding_capabilities() -> None:
    env = _registry_env()
    ok = await run_doctor(
        environ=env, settings=_settings(), probes=_probes(), canary="embedding.demo"
    )
    assert _by_id(ok)["canary.dry_run"] == "ok"
    other = await run_doctor(environ=env, settings=_settings(), probes=_probes(), canary="llm.chat")
    assert _by_id(other)["canary.dry_run"] == "skip"
    down = await run_doctor(
        environ=env, settings=_settings(), probes=_probes(api_error=True), canary="embedding.demo"
    )
    assert _by_id(down)["canary.dry_run"] == "skip"
    bad = await run_doctor(
        environ=env,
        settings=_settings(),
        probes=_probes(canary={"status_code": 422, "body": {}}),
        canary="embedding.demo",
    )
    assert _by_id(bad)["canary.dry_run"] == "fail"
    assert "canary.dry_run" not in _by_id(
        await run_doctor(environ=env, settings=_settings(), probes=_probes())
    )


async def test_report_json_never_contains_secret_values(personal_env: dict[str, str]) -> None:
    registry = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(db_error=True)
    )
    personal = await run_doctor(environ=personal_env, probes=_probes())
    for report in (registry, personal):
        text = json.dumps(report.to_dict())
        for secret in ("hunter2", "rk-secret-value", "ek-secret-value"):
            assert secret not in text
    payload = registry.to_dict()
    assert payload["schema_version"] == 1
    assert set(payload["summary"]) == {"ok", "warn", "fail", "skip"}


async def test_default_database_probe_reports_class_names_only() -> None:
    with pytest.raises(ProbeError) as caught:
        await _database_probe("postgresql://pitwall:hunter2@127.0.0.1:1/nope", 0.5)
    assert "hunter2" not in str(caught.value)
    assert "Postgres" in str(caught.value)


def test_python_check_matches_the_supported_minor() -> None:
    assert sys.version_info[:2] == (3, 14)


@pytest.fixture(autouse=True)
def _no_registered_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    """Broker-report tests stay hermetic: registered sections (agents, ...) are tested in
    tests/test_doctor_sections.py."""
    monkeypatch.setattr("pitwall.cli.doctor.run_registered_sections", lambda: ())


def _fixed_report(status: str) -> DoctorReport:
    return DoctorReport(
        "registry", "0.0.0", (DoctorCheck("api.health", "services", status, "detail", "next"),)
    )


@pytest.mark.parametrize(
    ("status", "argv", "code"),
    [
        ("ok", ["doctor"], 0),
        ("warn", ["doctor"], 0),
        ("warn", ["doctor", "--strict"], 1),
        ("fail", ["doctor"], 1),
    ],
)
def test_cli_exit_codes(
    monkeypatch: pytest.MonkeyPatch, status: str, argv: list[str], code: int
) -> None:
    async def fake(**_kwargs: Any) -> DoctorReport:
        return _fixed_report(status)

    monkeypatch.setattr("pitwall.cli.doctor.run_doctor", fake)
    assert cli.main(argv) == code


def test_cli_json_is_the_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    async def fake(**kwargs: Any) -> DoctorReport:
        seen.update(kwargs)
        return _fixed_report("ok")

    monkeypatch.setattr("pitwall.cli.doctor.run_doctor", fake)
    assert (
        cli.main(
            ["doctor", "--json", "--canary", "embedding.demo", "--api-url", "http://127.0.0.1:9"]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == _fixed_report("ok").to_dict()
    assert (seen["canary"], seen["api_url"]) == ("embedding.demo", "http://127.0.0.1:9")


def test_cli_table_names_every_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake(**_kwargs: Any) -> DoctorReport:
        return _fixed_report("warn")

    monkeypatch.setattr("pitwall.cli.doctor.run_doctor", fake)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "api.health" in out and "warn" in out and "mode registry" in out


def test_usage_lists_doctor_and_mcp_install(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["--help"])
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("Usage: pitwall {setup|serve|status|stop|doctor|")
    assert "pitwall doctor" in out
    assert "pitwall mcp install" in out and "pitwall mcp uninstall" in out


def test_cli_lines_keep_full_ids_and_next_steps_at_80_columns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Agents read this output through 80-column pipes; ids are the contract and must never be cut."""
    long_check = DoctorCheck(
        "personal.runpod_credential",
        "config",
        "fail",
        "no RunPod credential found in the environment or runpodctl config",
        "export RUNPOD_API_KEY, or run runpodctl doctor",
    )

    async def fake(**_kwargs: Any) -> DoctorReport:
        return DoctorReport("personal", "0.0.0", (long_check,))

    monkeypatch.setattr("pitwall.cli.doctor.run_doctor", fake)
    monkeypatch.setenv("COLUMNS", "80")
    cli.main(["doctor"])
    lines = capsys.readouterr().out.splitlines()
    assert (
        "[fail] personal.runpod_credential: no RunPod credential found in the environment or runpodctl config"
        in lines
    )
    assert "       next: export RUNPOD_API_KEY, or run runpodctl doctor" in lines
    assert not any("\u2026" in line for line in lines)


def _facts_with_burn(burn: BurnFacts) -> DatabaseFacts:
    return DatabaseFacts(
        applied_versions=ALL_VERSIONS,
        schema_ready=True,
        enabled_capabilities=1,
        embedding_capabilities=("embedding.demo",),
        enabled_providers=1,
        healthy_providers=1,
        burn=burn,
    )


async def _burn_check(burn: BurnFacts) -> Any:
    report = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(facts=_facts_with_burn(burn))
    )
    return {check.id: check for check in report.checks}["spend.burn_rate"]


async def test_burn_rate_fails_on_the_gate_spend_when_the_rollup_misses_it() -> None:
    # Live 2026-09-28: the rollup saw 0.31 of 18.60 month-to-date, and doctor reported ok.
    check = await _burn_check(
        BurnFacts(Decimal("15"), Decimal("0.31"), Decimal("1"), gate_spend_usd=Decimal("18.60"))
    )
    assert check.status == "fail"
    assert "18.60" in check.detail and "0.31" in check.detail


async def test_burn_rate_warns_when_rollup_and_gate_disagree() -> None:
    check = await _burn_check(
        BurnFacts(Decimal("50"), Decimal("1.00"), Decimal("5"), gate_spend_usd=Decimal("4.00"))
    )
    assert check.status == "warn"
    assert "rollup" in check.detail and "4.00" in check.detail


@pytest.mark.parametrize(
    ("stale", "sufficiency"), [(True, "sufficient"), (False, "sparse"), (False, "no_data")]
)
async def test_burn_rate_warns_on_stale_or_thin_data(stale: bool, sufficiency: str) -> None:
    check = await _burn_check(
        BurnFacts(
            Decimal("50"),
            Decimal("1"),
            Decimal("5"),
            gate_spend_usd=Decimal("1"),
            stale=stale,
            data_sufficiency=sufficiency,
        )
    )
    assert check.status == "warn"
    assert ("stale" in check.detail) if stale else (sufficiency in check.detail)


async def test_burn_rate_stays_ok_when_everything_agrees() -> None:
    check = await _burn_check(
        BurnFacts(Decimal("50"), Decimal("1"), Decimal("5"), gate_spend_usd=Decimal("1"))
    )
    assert check.status == "ok"


async def test_a_failing_burn_rate_read_is_a_warning_not_a_skip() -> None:
    facts = DatabaseFacts(
        applied_versions=ALL_VERSIONS,
        schema_ready=True,
        enabled_capabilities=1,
        embedding_capabilities=("embedding.demo",),
        enabled_providers=1,
        healthy_providers=1,
        burn=None,
        burn_error="NameError",
    )
    report = await run_doctor(
        environ=_registry_env(), settings=_settings(), probes=_probes(facts=facts)
    )
    check = {c.id: c for c in report.checks}["spend.burn_rate"]
    assert (check.status, check.detail) == ("warn", "burn-rate read failed (NameError)")


async def test_disarmed_serve_providers_are_not_counted_as_unhealthy() -> None:
    """A serve provider with no active lease is idle, not failing (finding #2)."""
    base: dict[str, Any] = {
        "applied_versions": ALL_VERSIONS,
        "schema_ready": True,
        "enabled_capabilities": 1,
    }
    idle = await run_doctor(
        environ=_registry_env(),
        settings=_settings(),
        probes=_probes(
            facts=DatabaseFacts(
                **base, enabled_providers=0, healthy_providers=0, disarmed_providers=2
            )
        ),
    )
    check = {c.id: c for c in idle.checks}["registry.providers"]
    assert check.status == "ok", check
    assert "2 disarmed" in check.detail

    mixed = await run_doctor(
        environ=_registry_env(),
        settings=_settings(),
        probes=_probes(
            facts=DatabaseFacts(
                **base, enabled_providers=1, healthy_providers=0, disarmed_providers=1
            )
        ),
    )
    check = {c.id: c for c in mixed.checks}["registry.providers"]
    assert check.status == "warn"
    assert check.detail == "1 enabled but none healthy (1 disarmed)"
