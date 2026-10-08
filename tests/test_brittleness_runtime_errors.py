"""A missing or unreachable database, a missing budget, or an unwritable home is one clear line."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from pitwall.cli import runtime_errors

NO_DATABASE_REASON = "DATABASE_URL is not set"
UNREACHABLE_URL = "postgresql://someone:s3cr3t-pw@127.0.0.1:1/pitwall"

# (argv, machine-readable code that must stay the first word of the human line)
DB_COMMANDS: list[tuple[list[str], str]] = [
    (["budget", "show"], "budget_unavailable"),
    (["cost", "summary"], "cost_unavailable"),
    (["cost", "workloads"], "cost_unavailable"),
    (["burn-rate"], "burn_rate_unavailable"),
    (["leases", "list"], "lease_operation_failed"),
    (["provider-ops", "list"], "provider_operations_unavailable"),
    (["init"], "init_failed"),
]


def _run(argv: list[str]) -> int:
    from pitwall.cli import main

    return main(argv)


@pytest.mark.parametrize(("argv", "code"), DB_COMMANDS, ids=[" ".join(a) for a, _ in DB_COMMANDS])
def test_missing_database_url_is_one_actionable_line(
    argv: list[str],
    code: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    exit_code = _run(argv)

    captured = capsys.readouterr()
    assert exit_code != 0
    text = captured.out + captured.err
    assert NO_DATABASE_REASON in text
    assert code in text
    assert "Traceback" not in text


@pytest.mark.parametrize(("argv", "code"), DB_COMMANDS, ids=[" ".join(a) for a, _ in DB_COMMANDS])
def test_unreachable_database_names_the_host_and_never_the_password(
    argv: list[str],
    code: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)

    exit_code = _run(argv)

    text = capsys.readouterr()
    combined = text.out + text.err
    assert exit_code != 0
    assert "cannot reach Postgres at 127.0.0.1:1" in combined
    assert code in combined
    assert "s3cr3t-pw" not in combined
    assert "Traceback" not in combined


def test_json_mode_keeps_the_code_and_adds_the_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert _run(["cost", "summary", "--json"]) != 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "cost_unavailable"
    assert NO_DATABASE_REASON in payload["reason"]


def test_budget_show_json_reports_the_unreachable_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)

    assert _run(["budget", "show", "--json"]) != 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "budget_unavailable"
    assert "127.0.0.1:1" in payload["reason"]


def test_runtime_reason_ignores_unknown_errors() -> None:
    assert runtime_errors.runtime_reason(RuntimeError("boom")) is None
    assert runtime_errors.failure_line("code_x", RuntimeError("boom")) == "code_x"


def test_runtime_reason_for_missing_budget() -> None:
    from pitwall.cost.budget_gate import BudgetNotConfigured

    reason = runtime_errors.runtime_reason(
        BudgetNotConfigured("PITWALL_MONTHLY_BUDGET_USD must be set")
    )

    assert reason is not None
    assert "PITWALL_MONTHLY_BUDGET_USD must be set" in reason


def test_database_endpoint_never_includes_credentials() -> None:
    assert (
        runtime_errors.database_endpoint("postgresql://u:pw@db.example:6543/x") == "db.example:6543"
    )
    assert runtime_errors.database_endpoint("not a url") == "the configured database"


def test_preflight_reports_missing_and_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert NO_DATABASE_REASON in (runtime_errors.database_preflight() or "")

    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    assert "127.0.0.1:1" in (runtime_errors.database_preflight() or "")


def test_cost_exporter_without_a_budget_exits_with_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.cost import __main__ as exporter_main

    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    monkeypatch.delenv("PITWALL_MONTHLY_BUDGET_USD", raising=False)
    monkeypatch.setattr(exporter_main, "require_valid_service_env", lambda _name: None)
    monkeypatch.setattr(
        exporter_main.uvicorn, "run", lambda *a, **k: pytest.fail("must not start the server")
    )

    with pytest.raises(SystemExit) as exited:
        exporter_main.main([])

    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert "PITWALL_MONTHLY_BUDGET_USD must be set" in err
    assert "Traceback" not in err


def test_api_with_postgres_down_exits_with_one_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.api import __main__ as api_main

    monkeypatch.setenv("DATABASE_URL", UNREACHABLE_URL)
    monkeypatch.setattr(api_main, "require_valid_service_env", lambda _name: None)
    monkeypatch.setattr(
        api_main.uvicorn, "run", lambda *a, **k: pytest.fail("must not start the server")
    )

    with pytest.raises(SystemExit) as exited:
        api_main.main([])

    assert exited.value.code == 1
    err = capsys.readouterr().err
    assert "cannot reach Postgres at 127.0.0.1:1" in err
    assert "Traceback" not in err
    assert "s3cr3t-pw" not in err


def test_mcp_install_in_a_read_only_home_is_one_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    request: pytest.FixtureRequest,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    home.chmod(stat.S_IRUSR | stat.S_IXUSR)
    request.addfinalizer(lambda: home.chmod(stat.S_IRWXU))
    probe = home / "probe"
    try:
        probe.mkdir()
    except PermissionError:
        pass
    else:  # running as root: permissions do not bind, so the scenario cannot be built
        probe.rmdir()
        pytest.skip("directory permissions are not enforced for this user")
    monkeypatch.setenv("HOME", str(home))
    for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_DATA_HOME"):
        monkeypatch.delenv(name, raising=False)

    exit_code = _run(["mcp", "install", "opencode", "--scope", "user"])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "Traceback" not in captured.out + captured.err
    assert "cannot write" in captured.err
    assert "writable directory" in captured.err


MORE_DB_COMMANDS: list[tuple[list[str], str]] = [
    (["routing", "status", "route-1"], "routing_operation_failed"),
    (["set-provider-health", "provider-1", "healthy"], "set_provider_health_failed"),
]


@pytest.mark.parametrize(
    ("argv", "code"), MORE_DB_COMMANDS, ids=[" ".join(a) for a, _ in MORE_DB_COMMANDS]
)
def test_other_database_commands_name_the_fix(
    argv: list[str],
    code: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert _run(argv) != 0

    captured = capsys.readouterr()
    assert NO_DATABASE_REASON in captured.out + captured.err
    assert code in captured.out + captured.err


def test_seed_without_a_database_names_the_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    seed_file = tmp_path / "seed.json"
    seed_file.write_text("{}", encoding="utf-8")

    assert _run(["seed", str(seed_file)]) != 0

    err = capsys.readouterr()
    assert NO_DATABASE_REASON in err.out + err.err
    assert "seed_failed" in err.out + err.err
