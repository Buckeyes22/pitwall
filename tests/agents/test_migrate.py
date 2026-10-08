"""`pitwall agents migrate` against a fixture of the standalone Agent Routing layout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from pitwall.agents import managed_channel, migrate, workspace
from pitwall.agents.migrate_env import LEGACY_PATHS, LEGACY_PREFIXES

FIXTURE = Path(__file__).parent / "fixtures" / "legacy_layout"
OLD_STATE_HOME = LEGACY_PREFIXES[0] + "STATE_HOME"
OLD_UNRESTRICTED = LEGACY_PREFIXES[0] + "UNRESTRICTED"
OLD_TIMEOUT = "SHIM_TIMEOUT_" + "SECS"


class Workstation:
    """A temporary home holding the legacy layout, with fake host CLIs on PATH."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.home = root / "home"
        self.bin = root / "bin"
        # Skip bytecode a local compileall (the CI lint step) may leave in the fixture tree.
        shutil.copytree(
            FIXTURE, self.home, symlinks=True, ignore=shutil.ignore_patterns("__pycache__")
        )
        # Stored as ``claude`` because a ``.claude`` directory is git-ignored.
        (self.home / "claude").rename(self.home / ".claude")
        self.bin.mkdir()
        for name in ("pitwall", "claude", "codex"):
            command = self.bin / name
            command.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            command.chmod(0o755)
        self.toml = self.home / ".config" / "pitwall" / "pitwall.toml"
        self.env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:/usr/bin:/bin",
            "PITWALL_CONFIG_FILE": str(self.toml),
        }
        self.commands: list[tuple[str, ...]] = []
        self.output: list[str] = []
        self.errors: list[str] = []

    @property
    def state(self) -> Path:
        return self.home / ".local" / "state" / "pitwall" / "agents"

    @property
    def share(self) -> Path:
        return self.home / ".local" / "share" / "subagent-model-routing"

    def runner(self, argv: Sequence[str]) -> int:
        self.commands.append(tuple(argv))
        return 0

    def run(self, env: dict[str, str] | None = None, harnesses: Sequence[str] = ()) -> int:
        self.output.clear()
        self.errors.clear()
        return migrate.migrate(
            env or self.env,
            self.home,
            runner=self.runner,
            harnesses=harnesses,
            plugin_hosts=(),
            out=self.output.append,
            err=self.errors.append,
        )

    def snapshot(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for path in sorted(self.home.rglob("*")):
            if path.is_symlink() or path.is_dir():
                found[str(path.relative_to(self.home))] = "<dir>" if path.is_dir() else "<link>"
            else:
                found[str(path.relative_to(self.home))] = path.read_text(encoding="utf-8")
        return found

    def profiles(self) -> dict[str, Any]:
        table: dict[str, Any] = tomllib.loads(self.toml.read_text(encoding="utf-8"))
        profiles: dict[str, Any] = table["agents"]["profiles"]
        return profiles


@pytest.fixture
def station(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workstation:
    workstation = Workstation(tmp_path)
    # Nothing the migration spawns may reach the real home or the real host CLIs.
    monkeypatch.setenv("HOME", workstation.env["HOME"])
    monkeypatch.setenv("PATH", workstation.env["PATH"])
    return workstation


def test_migrates_fixture_layout(station: Workstation) -> None:
    assert station.run() == 0, station.errors

    # Step 1: the profiles are in pitwall.toml.
    profiles = station.profiles()
    assert profiles["defaults"] == {"harness": "opencode", "endpointHarness": "qwen"}
    assert profiles["models"]["claude-home"] == {
        "model": "sonnet",
        "harness": "claude",
        "env": {"CLAUDE_CONFIG_DIR": "/srv/logins/claude-home"},
        "account": "home",
    }

    # Step 2: state and config moved to the new roots.
    assert (
        json.loads((station.state / "runs/20260901-done/run.json").read_text())["state"]
        == "succeeded"
    )
    assert (station.state / "runs/20260901-done/result.json").is_file()
    assert (station.state / "mailbox/inbox/m1.json").is_file()
    assert (station.state / "receipts/r1.json").is_file()
    assert (station.state / "usage/claude.json").is_file()
    assert (station.state / "ledger/observations.jsonl").read_text().count("codex") == 1
    assert (station.home / ".config/pitwall/agents/hooks.json").is_file()

    # Step 3: the shims call the new command, and the plugin manifest is written.
    shim = (station.home / ".claude/scripts/codex-shim.sh").read_text()
    assert shim.startswith("#!/usr/bin/env bash\ncommand -v pitwall")
    assert shim.endswith('; exec pitwall agents _shim codex "$@"\n')
    assert (station.home / ".local/share/pitwall/install-manifest.json").is_file()

    # Step 5: nothing of the old install is left.
    for literal in LEGACY_PATHS:
        assert not (station.home / literal.removeprefix("~/")).exists(), literal
    assert not (station.home / ".claude/scripts/pitwall-agent-routing").exists()
    assert not (station.home / ".claude/scripts/parse-shim-result.py").exists()
    assert not (station.home / ".local/bin/pitwall-agent-routing").exists()
    printed = "\n".join(station.output)
    assert f"removed {station.share}" in printed
    assert f"removed {station.home}/.local/bin/pitwall-agent-routing" in printed
    legacy_plugin_calls = [c for c in station.commands if "subagent-model-routing-local" in c[-1]]
    assert ("codex", "plugin", "marketplace", "remove", "subagent-model-routing-local") in (
        legacy_plugin_calls
    )
    # The old install declared its Claude marketplace in known_marketplaces.json, not in user
    # settings; `--scope user` fails there while exiting 0, so the removal names no scope.
    assert ("claude", "plugin", "marketplace", "remove", "pitwall") in station.commands


def test_moved_directories_keep_their_modification_times(station: Workstation) -> None:
    # `runs cleanup --older-than` ages a run by its directory's mtime, so the move must keep it.
    legacy_run = station.home / ".local/state/subagent-model-routing/runs/20260901-done"
    old = 1_756_700_000  # 2025-09-01
    os.utime(legacy_run, (old, old))
    assert station.run() == 0
    assert (station.state / "runs/20260901-done").stat().st_mtime == old


def test_second_run_is_noop(station: Workstation) -> None:
    assert station.run() == 0
    before = station.snapshot()
    station.commands.clear()
    assert station.run() == 0, station.errors
    assert station.snapshot() == before
    assert station.commands == []
    assert station.output == ["nothing to migrate"]


def test_refuses_with_active_runs(station: Workstation) -> None:
    running = station.home / ".local/state/subagent-model-routing/runs/20260929-live"
    running.mkdir()
    (running / "run.json").write_text('{"state": "running"}', encoding="utf-8")
    before = station.snapshot()

    assert station.run() == 2
    assert any("20260929-live (running)" in line for line in station.errors)
    assert station.snapshot() == before
    assert station.commands == []


def _legacy_run(
    station: Workstation,
    name: str,
    *,
    age: float = 0.0,
    launcher: dict[str, Any] | None = None,
    state: str = "running",
) -> Path:
    """A legacy run dir whose files were last touched ``age`` seconds ago."""
    run = station.home / ".local/state/subagent-model-routing/runs" / name
    run.mkdir()
    (run / "run.json").write_text(json.dumps({"state": state}), encoding="utf-8")
    (run / "events.jsonl").write_text("", encoding="utf-8")
    if launcher is not None:
        (run / "launcher.json").write_text(json.dumps(launcher), encoding="utf-8")
    stamp = time.time() - age
    for path in run.iterdir():
        os.utime(path, (stamp, stamp))
    os.utime(run, (stamp, stamp))
    return run


WEEK = 7 * 24 * 3600.0


def test_stale_running_record_is_abandoned(station: Workstation) -> None:
    _legacy_run(station, "20260821-dead", age=WEEK)

    assert station.run() == 0, station.errors
    printed = "\n".join(station.output)
    assert "treated as abandoned: 20260821-dead (running, last activity 2026-" in printed
    assert "1 non-terminal dispatch record(s)" in printed
    # Moved over unchanged.
    moved = station.state / "runs/20260821-dead/run.json"
    assert json.loads(moved.read_text(encoding="utf-8")) == {"state": "running"}


def test_live_launcher_pid_blocks_migration(station: Workstation) -> None:
    child = subprocess.Popen(["sleep", "60"])
    try:
        identity = managed_channel._process_identity(child.pid)
        _legacy_run(
            station,
            "20260821-alive",
            age=WEEK,
            launcher={"pid": child.pid, "pidStartIdentity": identity},
        )
        before = station.snapshot()

        assert station.run() == 2
        assert any("20260821-alive (running)" in line for line in station.errors)
        assert station.snapshot() == before
    finally:
        child.kill()
        child.wait()


def test_fresh_record_without_pid_blocks_migration(station: Workstation) -> None:
    _legacy_run(station, "20260929-fresh", age=5.0)
    _legacy_run(station, "20260821-dead", age=WEEK)

    assert station.run() == 2
    assert any("20260929-fresh (running)" in line for line in station.errors)
    assert not any("20260821-dead" in line for line in station.errors)


def test_dead_launcher_pid_with_old_mtimes_is_abandoned(station: Workstation) -> None:
    child = subprocess.Popen(["true"])
    child.wait()
    _legacy_run(
        station,
        "20260821-gone",
        age=WEEK,
        launcher={"pid": child.pid, "pidStartIdentity": "linux:1"},
    )

    assert station.run() == 0, station.errors
    assert "treated as abandoned: 20260821-gone (running" in "\n".join(station.output)


def test_recorded_timeout_sets_the_activity_window(station: Workstation) -> None:
    # Touched 2 hours ago: outside the default window, inside a 6 hour recorded timeout.
    run = _legacy_run(station, "20260929-long", age=7200.0)
    (run / "request.json").write_text('{"timeoutSeconds": 21600}', encoding="utf-8")
    stamp = time.time() - 7200.0
    os.utime(run / "request.json", (stamp, stamp))

    assert station.run() == 2
    assert any("20260929-long (running)" in line for line in station.errors)


# --- the old install's pitwall-channel registration is replaced, not refused ------------------


def _legacy_launcher(station: Workstation, name: str = "pitwall-agent-routing") -> str:
    return str(station.home / ".local/bin" / name)


def _legacy_json_entry(harness: str, launcher: str) -> dict[str, Any]:
    forwarded = [
        "SUBAGENT_MODEL_ROUTING_CHANNEL_DISPATCH_ID",
        "SUBAGENT_MODEL_ROUTING_CHANNEL_STATE_ROOT",
        "SUBAGENT_MODEL_ROUTING_CHANNEL_ATTEMPT",
        "SUBAGENT_MODEL_ROUTING_STATE_HOME",
        "XDG_STATE_HOME",
        "XDG_CONFIG_HOME",
    ]
    if harness == "opencode":
        return {
            "type": "local",
            "command": [launcher, "mcp"],
            "enabled": True,
            "environment": {name: "{env:" + name + "}" for name in forwarded},
        }
    return {"type": "stdio", "command": launcher, "args": ["mcp"]}


def _fresh_install_entry(station: Workstation, harness: str) -> Any:
    from pitwall.agents import mcp_registration

    command = mcp_registration.channel_server_command(station.env)
    return mcp_registration.render_entry(harness, command)


@pytest.mark.parametrize(
    ("harness", "relative", "section", "launcher"),
    [
        ("opencode", ".config/opencode/opencode.json", "mcp", "pitwall-agent-routing"),
        ("opencode", ".config/opencode/opencode.json", "mcp", "model-routing"),
        ("kimi", ".kimi-code/mcp.json", "mcpServers", "pitwall-agent-routing"),
        ("qwen", ".qwen/settings.json", "mcpServers", "model-routing"),
    ],
)
def test_legacy_json_channel_entry_is_replaced(
    station: Workstation, harness: str, relative: str, section: str, launcher: str
) -> None:
    path = station.home / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = _legacy_json_entry(harness, _legacy_launcher(station, launcher))
    path.write_text(json.dumps({section: {"pitwall-channel": legacy, "other": {"a": 1}}}))

    assert station.run(harnesses=(harness,)) == 0, station.errors
    written = json.loads(path.read_text(encoding="utf-8"))[section]
    assert written["pitwall-channel"] == _fresh_install_entry(station, harness)
    assert written["other"] == {"a": 1}


def test_legacy_codex_channel_block_is_replaced(station: Workstation) -> None:
    path = station.home / ".codex/config.toml"
    path.parent.mkdir(parents=True)
    legacy = _legacy_launcher(station)
    path.write_text(
        'model = "gpt"\n\n'
        "# >>> pitwall-channel (managed by pitwall-agent-routing setup mcp)\n"
        "[mcp_servers.pitwall-channel]\n"
        f'command = "{legacy}"\n'
        'args = ["mcp"]\n'
        'env_vars = ["XDG_STATE_HOME"]\n'
        "startup_timeout_sec = 30\n"
        "tool_timeout_sec = 3660\n"
        "# <<< pitwall-channel\n",
        encoding="utf-8",
    )

    assert station.run(harnesses=("codex",)) == 0, station.errors
    text = path.read_text(encoding="utf-8")
    assert "pitwall-agent-routing" not in text
    assert text.count("[mcp_servers.pitwall-channel]") == 1
    assert _fresh_install_entry(station, "codex") in text
    assert text.startswith('model = "gpt"')


def test_legacy_claude_channel_entry_is_replaced(station: Workstation) -> None:
    path = station.home / ".claude.json"
    legacy = _legacy_json_entry("claude", _legacy_launcher(station))
    path.write_text(json.dumps({"mcpServers": {"pitwall-channel": legacy}}), encoding="utf-8")

    assert station.run(harnesses=("claude",)) == 0, station.errors
    desired = json.dumps(_fresh_install_entry(station, "claude"), sort_keys=True)
    remove = ("claude", "mcp", "remove", "--scope", "user", "pitwall-channel")
    add = ("claude", "mcp", "add-json", "--scope", "user", "pitwall-channel", desired)
    claude_calls = [c for c in station.commands if c[:2] == ("claude", "mcp")]
    assert claude_calls == [remove, add]


@pytest.mark.parametrize("harness", ["opencode", "kimi", "claude", "codex"])
def test_foreign_channel_entry_is_still_refused(station: Workstation, harness: str) -> None:
    foreign = "/usr/bin/somebody-elses-server"
    if harness == "codex":
        path = station.home / ".codex/config.toml"
        path.parent.mkdir(parents=True)
        path.write_text(f'[mcp_servers.pitwall-channel]\ncommand = "{foreign}"\nargs = ["mcp"]\n')
    else:
        relative = {
            "opencode": ".config/opencode/opencode.json",
            "kimi": ".kimi-code/mcp.json",
            "claude": ".claude.json",
        }[harness]
        path = station.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        section = "mcp" if harness == "opencode" else "mcpServers"
        entry: Any = (
            {"type": "local", "command": [foreign, "mcp"]}
            if harness == "opencode"
            else {"command": foreign, "args": ["mcp"]}
        )
        path.write_text(json.dumps({section: {"pitwall-channel": entry}}), encoding="utf-8")
    before = path.read_text(encoding="utf-8")

    assert station.run(harnesses=(harness,)) == 1
    assert any("not managed by pitwall" in line for line in station.errors)
    assert path.read_text(encoding="utf-8") == before


# --- output volume ---------------------------------------------------------------------------


def test_moves_are_reported_per_top_level_item(station: Workstation) -> None:
    runs = station.home / ".local/state/subagent-model-routing/runs"
    for index in range(40):
        run = runs / f"run-{index:02d}"
        run.mkdir()
        (run / "run.json").write_text('{"state": "succeeded"}', encoding="utf-8")
        (run / "events.jsonl").write_text("", encoding="utf-8")

    assert station.run() == 0, station.errors
    moved = [line for line in station.output if line.startswith("moved ")]
    assert f"moved {runs} -> {station.state / 'runs'} (82 entries)" in moved
    assert not any("run-00" in line for line in moved)
    assert len(moved) < 10
    # Files that stand alone keep their own line.
    receipt = station.home / ".local/state/subagent-model-routing/receipts/r1.json"
    assert f"moved {receipt.parent} -> {station.state / 'receipts'} (1 entries)" in moved
    assert (station.state / "runs/run-39/run.json").is_file()


def test_conflicting_toml_value_not_overwritten(station: Workstation) -> None:
    station.toml.parent.mkdir(parents=True)
    station.toml.write_text(
        '[agents.profiles.defaults]\nharness = "claude"\n\n[other]\nkeep = 1\n', encoding="utf-8"
    )
    # A second conflict, in the state tree.
    (station.state / "receipts").mkdir(parents=True)
    (station.state / "receipts/r1.json").write_text('{"receipt": 2}\n', encoding="utf-8")
    before = station.snapshot()

    assert station.run() == 1
    text = "\n".join(station.errors)
    assert "agents.profiles.defaults.harness" in text
    assert "receipts/r1.json" in text
    assert "nothing was changed" in text
    assert station.snapshot() == before
    assert station.commands == []


def test_existing_toml_settings_are_kept_and_matching_values_accepted(station: Workstation) -> None:
    station.toml.parent.mkdir(parents=True)
    station.toml.write_text(
        '[other]\nkeep = 1\n\n[agents.profiles.defaults]\nharness = "opencode"\n', encoding="utf-8"
    )
    assert station.run() == 0, station.errors
    assert tomllib.loads(station.toml.read_text())["other"] == {"keep": 1}
    assert "claude-home" in station.profiles()["models"]


def test_reports_legacy_environment_variables(station: Workstation) -> None:
    env = {**station.env, OLD_UNRESTRICTED: "0", OLD_TIMEOUT: "30"}
    assert station.run(env) == 0, station.errors
    printed = "\n".join(station.output)
    assert f"{OLD_UNRESTRICTED} -> PITWALL_AGENTS_UNRESTRICTED" in printed
    assert f"{OLD_TIMEOUT} -> PITWALL_AGENTS_TIMEOUT_SECS" in printed


def test_old_install_removed_last(station: Workstation) -> None:
    seen: list[dict[str, bool]] = []

    def runner(argv: Sequence[str]) -> int:
        station.commands.append(tuple(argv))
        if "subagent-model-routing-local" in argv[-1]:
            seen.append(
                {
                    "share_still_there": station.share.exists(),
                    "new_manifest": (
                        station.home / ".local/share/pitwall/install-manifest.json"
                    ).is_file(),
                    "profiles_written": station.toml.is_file(),
                    "state_moved": (station.state / "runs/20260901-done/run.json").is_file(),
                    "new_shim": "pitwall agents _shim"
                    in (station.home / ".claude/scripts/codex-shim.sh").read_text(),
                }
            )
        return 0

    code = migrate.migrate(
        station.env,
        station.home,
        runner=runner,
        harnesses=(),
        plugin_hosts=(),
        out=station.output.append,
    )
    assert code == 0
    assert seen
    assert all(all(step.values()) for step in seen), seen
    assert not station.share.exists()


def test_failed_install_leaves_the_old_install_in_place(station: Workstation) -> None:
    # A file the installer did not write blocks the new shim, so the install step fails.
    (station.home / ".claude/scripts/agy-shim.sh").write_text(
        "# the user's own\n", encoding="utf-8"
    )

    assert station.run() == 1
    assert any("install failed" in line for line in station.errors)
    assert station.share.exists()
    assert "pitwall-agent-routing" in (station.home / ".claude/scripts/codex-shim.sh").read_text()
    assert (station.home / ".local/bin/pitwall-agent-routing").exists()
    assert station.commands == []


def _alias_locations(station: Workstation) -> tuple[Path, Path]:
    return (
        station.home / ".local/bin/model-routing",
        station.home / ".claude/scripts/model-routing",
    )


def test_legacy_alias_symlinked_to_the_launcher_is_removed(station: Workstation) -> None:
    for alias in _alias_locations(station):
        alias.symlink_to(station.home / ".local/bin/pitwall-agent-routing")

    assert station.run() == 0, station.errors

    printed = "\n".join(station.output)
    for alias in _alias_locations(station):
        assert not alias.is_symlink()
        assert f"removed {alias}" in printed


def test_dangling_alias_named_for_the_launcher_is_removed(station: Workstation) -> None:
    alias, _ = _alias_locations(station)
    alias.symlink_to("/nowhere/at/all/pitwall-agent-routing")

    assert station.run() == 0, station.errors

    assert not alias.is_symlink()
    assert f"removed {alias}" in "\n".join(station.output)


def test_alias_that_is_a_marked_legacy_file_is_removed(station: Workstation) -> None:
    alias, _ = _alias_locations(station)
    alias.write_text('#!/bin/sh\nexec pitwall-agent-routing "$@"\n', encoding="utf-8")

    assert station.run() == 0, station.errors

    assert not alias.exists()
    assert f"removed {alias}" in "\n".join(station.output)


def test_foreign_model_routing_entries_are_kept(station: Workstation) -> None:
    alias, script_alias = _alias_locations(station)
    alias.write_text("#!/bin/sh\necho my own tool\n", encoding="utf-8")
    other = station.home / "elsewhere/some-tool"
    other.parent.mkdir()
    other.write_text("#!/bin/sh\n", encoding="utf-8")
    script_alias.symlink_to(other)

    assert station.run() == 0, station.errors

    assert alias.read_text(encoding="utf-8") == "#!/bin/sh\necho my own tool\n"
    assert script_alias.is_symlink()
    printed = "\n".join(station.output)
    assert str(alias) not in printed
    assert str(script_alias) not in printed


def test_failed_install_leaves_the_legacy_alias_in_place(station: Workstation) -> None:
    alias, _ = _alias_locations(station)
    alias.symlink_to(station.home / ".local/bin/pitwall-agent-routing")
    (station.home / ".claude/scripts/agy-shim.sh").write_text(
        "# the user's own\n", encoding="utf-8"
    )

    assert station.run() == 1
    assert alias.is_symlink()


_OLD_MARKER = "managed by " + "subagent-model-routing"
_DSH_SETTINGS = (
    "llm-pi-ai:\n  providers:\n"
    "    # {marker}\n    glimmer:\n      baseUrl: http://127.0.0.1:1/v1\n"
    "    user-owned:\n      baseUrl: http://x/v1\n"
)
_HERMES_CONFIG = (
    "model: sonnet\ncustom_providers:\n"
    "  # {marker}\n  glimmer:\n    base_url: http://127.0.0.1:1/v1\n"
)
_OPENCODE_JSON = (
    '{\n  "provider": {\n    "glimmer": {\n      "name": "{prefix}: glimmer",\n'
    '      "options": {}\n    },\n    "mine": {\n      "name": "My own"\n    }\n  }\n}\n'
)


def _seed_harness_configs(home: Path, *, old: bool) -> dict[Path, str]:
    marker = _OLD_MARKER if old else "managed by pitwall"
    prefix = "subagent-model-routing" if old else "pitwall"
    files = {
        home / ".dsh" / "settings.yaml": _DSH_SETTINGS.replace("{marker}", marker),
        home / ".hermes" / "config.yaml": _HERMES_CONFIG.replace("{marker}", marker),
        home / ".config" / "opencode" / "opencode.json": _OPENCODE_JSON.replace("{prefix}", prefix),
    }
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return files


def test_migrate_rewrites_old_harness_config_markers(station: Workstation) -> None:
    _seed_harness_configs(station.home, old=True)
    expected = {
        path.relative_to(station.home / "expected"): text
        for path, text in _seed_harness_configs(station.home / "expected", old=False).items()
    }
    shutil.rmtree(station.home / "expected")

    assert station.run() == 0, station.errors

    for relative, text in expected.items():
        live = (station.home / relative).read_text(encoding="utf-8")
        assert live == text, relative
        assert _OLD_MARKER not in live
        assert "subagent-model-routing:" not in live
    printed = "\n".join(station.output)
    assert f"rewrote managed marker in {station.home / '.dsh' / 'settings.yaml'}" in printed


def test_marker_migration_is_idempotent_and_leaves_new_markers_alone(station: Workstation) -> None:
    _seed_harness_configs(station.home, old=True)
    assert station.run() == 0, station.errors
    after_first = station.snapshot()
    assert station.run() == 0, station.errors
    assert station.snapshot() == after_first
    assert "rewrote managed marker" not in "\n".join(station.output)


def test_marker_migration_runs_without_a_legacy_install(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _seed_harness_configs(home, old=True)
    out: list[str] = []
    code = migrate.migrate(
        {"HOME": str(home), "PATH": "/usr/bin:/bin"},
        home,
        harnesses=(),
        plugin_hosts=(),
        out=out.append,
        err=out.append,
    )
    assert code == 0
    assert _OLD_MARKER not in (home / ".dsh" / "settings.yaml").read_text(encoding="utf-8")
    assert "nothing to migrate" not in "\n".join(out)


# --- dispatch worktree branches: model-routing/<id> becomes pitwall-agents/<id> -----------------

OLD_BRANCH_PREFIX = "model-routing/"
NEW_BRANCH_PREFIX = "pitwall-agents/"


def _git(cwd: Path, *argv: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *argv],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _branches(repo: Path) -> set[str]:
    return set(_git(repo, "branch", "--format=%(refname:short)").split())


def _add_legacy_dispatch(
    station: Workstation, dispatch_id: str, *, prefix: str = OLD_BRANCH_PREFIX
) -> Path:
    """A temp repo with a real worktree on the old branch, recorded in the legacy run store."""
    repo = station.root / f"repo-{dispatch_id}"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-q", "-m", "init")
    base = _git(repo, "rev-parse", "HEAD")
    legacy_state = station.home / ".local/state/subagent-model-routing"
    worktree = legacy_state / "worktrees" / dispatch_id
    _git(repo, "worktree", "add", "-q", "-b", prefix + dispatch_id, str(worktree), base)
    run = legacy_state / "runs" / dispatch_id
    run.mkdir(parents=True)
    (run / "run.json").write_text('{"state": "succeeded"}', encoding="utf-8")
    (run / "workspace.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "dispatchId": dispatch_id,
                "ownerDispatchId": dispatch_id,
                "repositoryCommonDir": str(repo / ".git"),
                "sourceWorktree": str(repo),
                "baseRef": None,
                "baseSha": base,
                "branch": prefix + dispatch_id,
                "path": str(worktree),
                "createdAt": "2026-09-01T00:00:00Z",
                "cleanupEligible": True,
            }
        ),
        encoding="utf-8",
    )
    return repo


def _record(station: Workstation, dispatch_id: str) -> dict[str, Any]:
    record: dict[str, Any] = json.loads(
        (station.state / "runs" / dispatch_id / "workspace.json").read_text()
    )
    return record


def test_migrate_renames_legacy_worktree_branches(station: Workstation) -> None:
    repo = _add_legacy_dispatch(station, "11111111-1111-4111-8111-111111111111")

    assert station.run() == 0, station.errors

    assert _branches(repo) == {"main", NEW_BRANCH_PREFIX + "11111111-1111-4111-8111-111111111111"}
    listing = _git(repo, "worktree", "list", "--porcelain")
    new_path = station.state / "worktrees" / "11111111-1111-4111-8111-111111111111"
    assert f"worktree {new_path.resolve()}" in listing
    assert f"branch refs/heads/{NEW_BRANCH_PREFIX}11111111-1111-4111-8111-111111111111" in listing
    assert (
        _git(new_path, "rev-parse", "--abbrev-ref", "HEAD")
        == NEW_BRANCH_PREFIX + "11111111-1111-4111-8111-111111111111"
    )
    record = _record(station, "11111111-1111-4111-8111-111111111111")
    assert record["branch"] == NEW_BRANCH_PREFIX + "11111111-1111-4111-8111-111111111111"
    assert record["path"] == str(new_path)
    assert any(
        "11111111-1111-4111-8111-111111111111" in line and "pitwall-agents/" in line
        for line in station.output
    )


def test_migrated_worktree_passes_ownership_check(station: Workstation) -> None:
    _add_legacy_dispatch(station, "11111111-1111-4111-8111-111111111111")
    assert station.run() == 0, station.errors

    metadata = workspace.load_worktree_metadata(station.env, "11111111-1111-4111-8111-111111111111")

    assert metadata.branch == NEW_BRANCH_PREFIX + "11111111-1111-4111-8111-111111111111"


def test_missing_repo_or_branch_is_reported_not_fatal(station: Workstation) -> None:
    gone = _add_legacy_dispatch(station, "22222222-2222-4222-8222-222222222222")
    deleted = _add_legacy_dispatch(station, "33333333-3333-4333-8333-333333333333")
    fine = _add_legacy_dispatch(station, "44444444-4444-4444-8444-444444444444")
    # Remove the first repo entirely, and delete the second's branch (worktree first).
    shutil.rmtree(gone)
    legacy_wt = (
        station.home
        / ".local/state/subagent-model-routing/worktrees/33333333-3333-4333-8333-333333333333"
    )
    _git(deleted, "worktree", "remove", "--force", str(legacy_wt))
    _git(deleted, "branch", "-D", OLD_BRANCH_PREFIX + "33333333-3333-4333-8333-333333333333")

    assert station.run() == 0, station.errors

    assert _branches(fine) == {"main", NEW_BRANCH_PREFIX + "44444444-4444-4444-8444-444444444444"}
    assert _branches(deleted) == {"main"}
    joined = "\n".join(station.output + station.errors)
    assert "22222222-2222-4222-8222-222222222222" in joined
    assert "33333333-3333-4333-8333-333333333333" in joined
    assert (
        _record(station, "22222222-2222-4222-8222-222222222222")["branch"]
        == NEW_BRANCH_PREFIX + "22222222-2222-4222-8222-222222222222"
    )
    assert _record(station, "33333333-3333-4333-8333-333333333333")["branch"] == (
        OLD_BRANCH_PREFIX + "33333333-3333-4333-8333-333333333333"
    )


def test_migrate_renames_pitwall_agent_routing_branches(station: Workstation) -> None:
    dispatch_id = "55555555-5555-4555-8555-555555555555"
    repo = _add_legacy_dispatch(station, dispatch_id, prefix="pitwall-agent-routing/")

    assert station.run() == 0, station.errors

    assert _branches(repo) == {"main", NEW_BRANCH_PREFIX + dispatch_id}
    assert _record(station, dispatch_id)["branch"] == NEW_BRANCH_PREFIX + dispatch_id


def test_record_follows_a_moved_worktree_whose_branch_is_gone(station: Workstation) -> None:
    dispatch_id = "66666666-6666-4666-8666-666666666666"
    repo = _add_legacy_dispatch(station, dispatch_id)
    legacy_wt = station.home / ".local/state/subagent-model-routing/worktrees" / dispatch_id
    _git(repo, "worktree", "remove", "--force", str(legacy_wt))
    _git(repo, "branch", "-D", OLD_BRANCH_PREFIX + dispatch_id)
    legacy_wt.mkdir(parents=True)
    (legacy_wt / "unapplied.txt").write_text("work\n", encoding="utf-8")

    assert station.run() == 0, station.errors

    record = _record(station, dispatch_id)
    assert record["path"] == str(station.state / "worktrees" / dispatch_id)
    assert record["branch"] == NEW_BRANCH_PREFIX + dispatch_id


def _gone_branch_dispatch(station: Workstation, dispatch_id: str) -> Path:
    """A legacy dispatch whose branch was deleted but whose worktree directory remains."""
    repo = _add_legacy_dispatch(station, dispatch_id)
    legacy_wt = station.home / ".local/state/subagent-model-routing/worktrees" / dispatch_id
    _git(repo, "worktree", "remove", "--force", str(legacy_wt))
    _git(repo, "branch", "-D", OLD_BRANCH_PREFIX + dispatch_id)
    legacy_wt.mkdir(parents=True)
    (legacy_wt / "unapplied.txt").write_text("work\n", encoding="utf-8")
    return repo


def test_branch_gone_path_rewrite_is_idempotent(station: Workstation) -> None:
    dispatch_id = "77777777-7777-4777-8777-777777777777"
    _gone_branch_dispatch(station, dispatch_id)
    assert station.run() == 0, station.errors
    assert _record(station, dispatch_id)["path"] == str(station.state / "worktrees" / dispatch_id)
    before = station.snapshot()

    assert station.run() == 0, station.errors

    assert station.snapshot() == before
    assert station.output == ["nothing to migrate"]
    assert _record(station, dispatch_id)["branch"] == NEW_BRANCH_PREFIX + dispatch_id


def test_rerun_recovers_a_crash_between_branch_rename_and_record_write(
    station: Workstation,
) -> None:
    dispatch_id = "88888888-8888-4888-8888-888888888888"
    repo = _add_legacy_dispatch(station, dispatch_id)
    original = migrate.atomic_write_bytes
    state = {"armed": True}

    def crash_on_record(path: Path, data: bytes) -> None:
        if state["armed"] and Path(path).name == "workspace.json":
            raise OSError("simulated crash")
        original(path, data)

    with (
        mock.patch.object(migrate, "atomic_write_bytes", crash_on_record),
        pytest.raises(OSError, match="simulated crash"),
    ):
        station.run()
    state["armed"] = False
    assert _branches(repo) == {"main", NEW_BRANCH_PREFIX + dispatch_id}
    assert _record(station, dispatch_id)["branch"] == OLD_BRANCH_PREFIX + dispatch_id

    assert station.run() == 0, station.errors

    record = _record(station, dispatch_id)
    assert record["branch"] == NEW_BRANCH_PREFIX + dispatch_id
    assert record["path"] == str(station.state / "worktrees" / dispatch_id)
    assert _branches(repo) == {"main", NEW_BRANCH_PREFIX + dispatch_id}


def test_record_follows_a_moved_worktree_when_the_repository_is_gone(
    station: Workstation,
) -> None:
    dispatch_id = "99999999-9999-4999-8999-999999999999"
    repo = _add_legacy_dispatch(station, dispatch_id)
    shutil.rmtree(repo)

    assert station.run() == 0, station.errors

    record = _record(station, dispatch_id)
    assert record["path"] == str(station.state / "worktrees" / dispatch_id)
    assert record["branch"] == NEW_BRANCH_PREFIX + dispatch_id
    assert any(dispatch_id in line and "updated" in line for line in station.output)
    before = station.snapshot()

    assert station.run() == 0, station.errors

    assert station.snapshot() == before
    assert station.output == ["nothing to migrate"]


def test_record_is_reported_stale_when_neither_repository_nor_worktree_remain(
    station: Workstation,
) -> None:
    dispatch_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    repo = _add_legacy_dispatch(station, dispatch_id)
    shutil.rmtree(repo)
    shutil.rmtree(station.home / ".local/state/subagent-model-routing/worktrees" / dispatch_id)

    assert station.run() == 0, station.errors

    assert any(dispatch_id in line and "stale record" in line for line in station.output)
    assert _record(station, dispatch_id)["branch"] == OLD_BRANCH_PREFIX + dispatch_id


def test_existing_target_branch_is_a_conflict(station: Workstation) -> None:
    repo = _add_legacy_dispatch(station, "11111111-1111-4111-8111-111111111111")
    _git(repo, "branch", NEW_BRANCH_PREFIX + "11111111-1111-4111-8111-111111111111", "main")

    code = station.run()

    assert code == migrate.EXIT_CONFLICT
    assert _branches(repo) == {
        "main",
        OLD_BRANCH_PREFIX + "11111111-1111-4111-8111-111111111111",
        NEW_BRANCH_PREFIX + "11111111-1111-4111-8111-111111111111",
    }
    assert (
        _record(station, "11111111-1111-4111-8111-111111111111")["branch"]
        == OLD_BRANCH_PREFIX + "11111111-1111-4111-8111-111111111111"
    )
    assert any(
        "11111111-1111-4111-8111-111111111111" in line and "already exists" in line
        for line in station.errors
    )


def test_worktree_migration_is_idempotent(station: Workstation) -> None:
    repo = _add_legacy_dispatch(station, "11111111-1111-4111-8111-111111111111")
    assert station.run() == 0, station.errors
    before = station.snapshot()
    branches = _branches(repo)

    assert station.run() == 0, station.errors

    assert station.snapshot() == before
    assert _branches(repo) == branches
    assert station.output == ["nothing to migrate"]


def test_a_migrated_run_with_a_gone_branch_and_broken_link_can_be_discarded(
    station: Workstation,
) -> None:
    dispatch_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    repo = _add_legacy_dispatch(station, dispatch_id)
    legacy_wt = station.home / ".local/state/subagent-model-routing/worktrees" / dispatch_id
    # The live shape: the branch and the repository's worktree entry are gone, the directory stays.
    shutil.rmtree(repo / ".git" / "worktrees")
    _git(repo, "branch", "-D", OLD_BRANCH_PREFIX + dispatch_id)
    assert legacy_wt.is_dir()
    assert station.run() == 0, station.errors

    owned = station.state / "worktrees" / dispatch_id
    assert owned.is_dir()
    assert _record(station, dispatch_id)["branch"] == NEW_BRANCH_PREFIX + dispatch_id

    workspace.discard_run(station.env, dispatch_id, yes=True)

    assert not owned.exists()
    assert _branches(repo) == {"main"}
