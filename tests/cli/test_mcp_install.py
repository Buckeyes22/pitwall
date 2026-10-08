"""pitwall mcp install: per-harness registration plans (plan Task 4)."""

from __future__ import annotations

import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

from pitwall import cli
from pitwall.mcp_install import (
    FORWARDED_ENV,
    McpInstallError,
    apply_plan,
    config_path,
    detect_harnesses,
    plan_registration,
    render_snippet,
    server_command,
)

CMD = ["/opt/pitwall/.venv/bin/pitwall", "mcp", "serve", "broker"]


@pytest.fixture
def where(tmp_path: Path) -> dict[str, Any]:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    return {
        "environ": {"HOME": str(home), "PATH": str(tmp_path / "bin")},
        "home": home,
        "project_root": project,
    }


def _plan(where: dict[str, Any], harness: str, scope: str = "user", **kwargs: Any):  # type: ignore[no-untyped-def]  # reason: test helper leaves its return type to inference
    return plan_registration(harness, scope, command=CMD, **where, **kwargs)  # type: ignore[arg-type]  # reason: test passes a plain str scope where a Literal user/project is expected


def test_codex_block_round_trips_foreign_bytes(where: dict[str, Any]) -> None:
    path = config_path("codex", "user", **where)
    path.parent.mkdir(parents=True)
    original = 'model = "gpt-5.6-sol"\n\n[mcp_servers.docs]\ncommand = "docs"\n'
    path.write_text(original, encoding="utf-8")
    backup = apply_plan(_plan(where, "codex"))
    assert backup is not None and backup.read_text(encoding="utf-8") == original
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["mcp_servers"]["pitwall"] == {
        "command": CMD[0],
        "args": CMD[1:],
        "env_vars": list(FORWARDED_ENV),
    }
    assert data["mcp_servers"]["docs"] == {"command": "docs"}
    assert not _plan(where, "codex").changed
    apply_plan(_plan(where, "codex", remove=True))
    assert path.read_text(encoding="utf-8") == original


def test_codex_refuses_an_unmanaged_pitwall_table(where: dict[str, Any]) -> None:
    path = config_path("codex", "user", **where)
    path.parent.mkdir(parents=True)
    path.write_text('[mcp_servers.pitwall]\ncommand = "other"\n', encoding="utf-8")
    with pytest.raises(McpInstallError, match="outside the pitwall-managed block"):
        _plan(where, "codex", force=True)


@pytest.mark.parametrize(
    ("harness", "scope", "section"),
    [
        ("opencode", "user", "mcp"),
        ("opencode", "project", "mcp"),
        ("claude-code", "project", "mcpServers"),
    ],
)
def test_json_entries_merge_by_value_and_uninstall(
    where: dict[str, Any], harness: str, scope: str, section: str
) -> None:
    path = config_path(harness, scope, **where)  # type: ignore[arg-type]  # reason: test passes a plain str scope where a Literal user/project is expected
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({section: {"docs": {"command": "docs"}}, "theme": "dark"}), encoding="utf-8"
    )
    apply_plan(_plan(where, harness, scope))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["theme"] == "dark" and data[section]["docs"] == {"command": "docs"}
    entry = data[section]["pitwall"]
    if harness == "opencode":
        assert entry == {
            "type": "local",
            "command": CMD,
            "enabled": True,
            "environment": {v: "{env:" + v + "}" for v in FORWARDED_ENV},
        }
    else:
        assert entry == {
            "type": "stdio",
            "command": CMD[0],
            "args": CMD[1:],
            "env": {v: "${" + v + ":-}" for v in FORWARDED_ENV},
        }
    assert not _plan(where, harness, scope).changed
    apply_plan(_plan(where, harness, scope, remove=True))
    after = json.loads(path.read_text(encoding="utf-8"))
    assert "pitwall" not in after[section] and after[section]["docs"] == {"command": "docs"}


def test_foreign_entry_needs_force_and_jsonc_is_refused(where: dict[str, Any]) -> None:
    path = config_path("opencode", "user", **where)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"mcp": {"pitwall": {"type": "local", "command": ["someone-else"]}}}),
        encoding="utf-8",
    )
    with pytest.raises(McpInstallError, match="rerun with --force"):
        _plan(where, "opencode")
    assert _plan(where, "opencode", force=True).changed
    path.write_text("{\n  // a comment\n}\n", encoding="utf-8")
    with pytest.raises(McpInstallError, match="not strict JSON"):
        _plan(where, "opencode")


def test_claude_user_scope_uses_the_claude_cli(where: dict[str, Any]) -> None:
    plan = _plan(where, "claude-code")
    assert plan.before == plan.after
    (command,) = plan.commands
    assert list(command[:6]) == ["claude", "mcp", "add-json", "--scope", "user", "pitwall"]
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    apply_plan(plan, run=fake_run)
    assert calls == [list(command)]
    path = config_path("claude-code", "user", **where)
    path.write_text(
        json.dumps({"mcpServers": {"pitwall": json.loads(command[6])}}), encoding="utf-8"
    )
    assert not _plan(where, "claude-code").changed
    removal = _plan(where, "claude-code", remove=True)
    assert removal.commands == (("claude", "mcp", "remove", "--scope", "user", "pitwall"),)


def test_failed_harness_command_raises_with_its_stderr(where: dict[str, Any]) -> None:
    def failing(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, "", "not logged in")

    with pytest.raises(McpInstallError, match="not logged in"):
        apply_plan(_plan(where, "claude-code"), run=failing)


def test_new_user_files_are_private(where: dict[str, Any]) -> None:
    plan = _plan(where, "opencode")
    apply_plan(plan)
    assert plan.path.stat().st_mode & 0o777 == 0o600


def test_server_command_prefers_the_sibling_console_script(tmp_path: Path) -> None:
    python = tmp_path / "python3"
    python.write_text("", encoding="utf-8")
    serve = ["mcp", "serve", "broker"]
    assert server_command(str(python)) == [str(python), "-m", "pitwall", *serve]
    script = tmp_path / "pitwall"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(0o755)
    assert server_command(str(python)) == [str(script), *serve]


def test_only_the_new_serve_command_is_recognised_as_ours() -> None:
    from pitwall.mcp_install import _is_ours

    assert _is_ours({"command": "/x/pitwall", "args": ["mcp", "serve", "broker"]})
    assert _is_ours({"command": ["/x/pitwall", "mcp", "serve", "broker"]})
    assert _is_ours({"command": "/x/python", "args": ["-m", "pitwall", "mcp", "serve", "broker"]})
    assert not _is_ours({"command": "/x/pitwall-mcp"})
    assert not _is_ours({"command": "/x/python", "args": ["-m", "pitwall.mcp"]})
    assert not _is_ours({"command": "/x/pitwall", "args": ["mcp", "serve", "channel"]})


def test_detection_uses_binaries_or_configs(where: dict[str, Any], tmp_path: Path) -> None:
    assert detect_harnesses(**where) == []
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text("#!/bin/sh\n", encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    opencode = config_path("opencode", "user", **where)
    opencode.parent.mkdir(parents=True)
    opencode.write_text("{}", encoding="utf-8")
    assert detect_harnesses(**where) == ["codex", "opencode"]


def test_snippets_match_the_registrations(where: dict[str, Any]) -> None:
    snippet = render_snippet("opencode", "project", CMD)
    assert json.loads(snippet) == {
        "mcp": {
            "pitwall": json.loads(
                json.dumps(
                    {
                        "type": "local",
                        "command": CMD,
                        "enabled": True,
                        "environment": {v: "{env:" + v + "}" for v in FORWARDED_ENV},
                    }
                )
            )
        }
    }
    assert render_snippet("claude-code", "user", CMD).startswith(
        "claude mcp add-json --scope user pitwall '"
    )
    assert "[mcp_servers.pitwall]" in render_snippet("codex", "user", CMD)


CHANNEL_CALLS: list[tuple[Any, ...]] = []
CHANNEL_KWARGS: list[dict[str, Any]] = []


def _cli_env(monkeypatch: pytest.MonkeyPatch, where: dict[str, Any]) -> None:
    monkeypatch.setenv("HOME", str(where["home"]))
    monkeypatch.setenv("PATH", where["environ"]["PATH"])
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr("pitwall.cli.mcp_install.server_command", lambda: CMD)
    CHANNEL_CALLS.clear()
    CHANNEL_KWARGS.clear()

    def fake_setup_mcp(*args: Any, **kwargs: Any) -> int:
        CHANNEL_CALLS.append(args)
        CHANNEL_KWARGS.append(kwargs)
        return 0

    monkeypatch.setattr("pitwall.agents.cli.setup_mcp", fake_setup_mcp)


def test_cli_dry_run_writes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    assert cli.main(["mcp", "install", "codex", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[mcp_servers.pitwall]" in out
    assert not config_path("codex", "user", **where).exists()


def test_cli_install_uninstall_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    assert (
        cli.main(
            [
                "mcp",
                "install",
                "opencode",
                "--scope",
                "project",
                "--project-root",
                str(where["project_root"]),
                "--json",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)["results"][0]
    assert (result["harness"], result["changed"], result["error"]) == ("opencode", True, None)
    assert result["verify"] == "opencode mcp list, run from this project"
    assert (
        cli.main(
            [
                "mcp",
                "uninstall",
                "opencode",
                "--scope",
                "project",
                "--project-root",
                str(where["project_root"]),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    data = json.loads((where["project_root"] / "opencode.json").read_text(encoding="utf-8"))
    assert "pitwall" not in data["mcp"]


def test_cli_reports_a_failing_harness_and_continues(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    bad = config_path("opencode", "user", **where)
    bad.parent.mkdir(parents=True)
    bad.write_text("{ not json", encoding="utf-8")
    assert cli.main(["mcp", "install", "opencode", "codex"]) == 1
    out = capsys.readouterr()
    assert "error:" in out.out + out.err
    assert config_path("codex", "user", **where).exists()


def test_cli_without_harnesses_detected(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    assert cli.main(["mcp", "install"]) == 1
    assert "no supported harness found" in capsys.readouterr().err


def test_install_does_not_need_the_server_runtime(
    monkeypatch: pytest.MonkeyPatch, where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)

    def explode() -> None:
        raise AssertionError("install must not validate the MCP runtime")

    monkeypatch.setattr("pitwall.mcp.ensure_runtime_env", explode)
    assert cli.main(["mcp", "install", "codex", "--dry-run"]) == 0


def test_cli_output_never_echoes_foreign_config_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    """Harness configs can hold other servers' literal keys; install output must not repeat them."""
    _cli_env(monkeypatch, where)
    path = config_path("opencode", "user", **where)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"provider": {"x": {"options": {"apiKey": "sk-leak-canary"}}}}), encoding="utf-8"
    )
    assert cli.main(["mcp", "install", "opencode", "--dry-run"]) == 0
    assert cli.main(["mcp", "install", "opencode"]) == 0
    assert cli.main(["mcp", "uninstall", "opencode"]) == 0
    captured = capsys.readouterr()
    assert "sk-leak-canary" not in captured.out + captured.err
    assert "[mcp_servers" not in captured.out  # opencode shows its own JSON entry, not TOML
    assert '"pitwall"' in captured.out


def test_cli_snippets_print_unwrapped_at_80_columns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    """Printed snippets must be copyable: a wrapped TOML array or shell command is broken."""
    _cli_env(monkeypatch, where)
    monkeypatch.setenv("COLUMNS", "80")
    assert cli.main(["mcp", "install", "codex", "claude-code", "--dry-run"]) == 0
    lines = capsys.readouterr().out.splitlines()
    env_vars = 'env_vars = ["RUNPOD_API_KEY", "DATABASE_URL", "REDIS_URL", "PITWALL_CONFIG_FILE"]'
    assert env_vars in lines
    assert any(
        line.startswith("  $ claude mcp add-json --scope user pitwall ") and line.endswith("}'")
        for line in lines
    )


def test_codex_is_user_scope_only(where: dict[str, Any]) -> None:
    """codex-cli ignores [mcp_servers] in a project's .codex/config.toml, so refuse instead of writing it."""
    with pytest.raises(McpInstallError, match="rerun with --scope user"):
        _plan(where, "codex", "project")
    with pytest.raises(McpInstallError, match="rerun with --scope user"):
        render_snippet("codex", "project", CMD)
    assert not (where["project_root"] / ".codex").exists()


def test_cli_codex_project_scope_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    assert (
        cli.main(
            [
                "mcp",
                "install",
                "codex",
                "--scope",
                "project",
                "--project-root",
                str(where["project_root"]),
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert "rerun with --scope user" in captured.out + captured.err


def test_install_registers_the_channel_server_beside_the_broker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    assert cli.main(["mcp", "install", "codex", "--dry-run"]) == 0
    assert CHANNEL_CALLS == [(["codex"], None, False, True, True)]
    assert cli.main(["mcp", "uninstall", "codex", "--dry-run"]) == 0
    assert CHANNEL_CALLS[-1] == (["codex"], None, True, True, True)
    assert CHANNEL_KWARGS[0]["scope"] == "user"


def test_project_scope_passes_scope_and_root_to_the_channel_step(
    monkeypatch: pytest.MonkeyPatch, where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    root = str(where["project_root"])
    argv = ["mcp", "install", "opencode", "--scope", "project", "--project-root", root, "--dry-run"]
    assert cli.main(argv) == 0
    assert CHANNEL_CALLS == [(["opencode"], None, False, True, True)]
    assert [{"scope": "project", "project_root": where["project_root"].resolve()}] == CHANNEL_KWARGS


def test_channel_step_runs_only_for_harnesses_whose_broker_step_succeeded(
    monkeypatch: pytest.MonkeyPatch, where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)
    root = str(where["project_root"])
    # Codex loads user-scope config only, so its project-scope broker step is refused.
    argv = ["mcp", "install", "claude-code", "codex", "--scope", "project", "--project-root", root]
    assert cli.main([*argv, "--dry-run"]) == 1
    assert [call[0] for call in CHANNEL_CALLS] == [["claude"]]
    CHANNEL_CALLS.clear()
    assert cli.main(["mcp", "install", "codex", "--scope", "project", "--dry-run"]) == 1
    assert CHANNEL_CALLS == []


def test_a_failed_channel_registration_fails_install_and_keeps_json_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], where: dict[str, Any]
) -> None:
    _cli_env(monkeypatch, where)

    def failing(*args: Any, **kwargs: Any) -> int:
        print("channel report line")
        return 2

    monkeypatch.setattr("pitwall.agents.cli.setup_mcp", failing)
    assert cli.main(["mcp", "install", "codex", "--dry-run", "--json"]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["channel"] == {"exit_code": 2}
    assert "channel report line" in captured.err
