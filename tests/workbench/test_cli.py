"""``pitwall workbench`` CLI and doctor tests.

Translated from ``cli.test.ts`` and from the ``doctor.ts`` cases of ``hosted-profiles.test.ts``.
Nothing here starts a real Pi or reaches a provider: a fake Pi package tree stands in for the
pinned toolchain, and ``launch_pi`` is replaced wherever a case gets past the toolchain check.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench import cli
from pitwall.workbench.doctor import (
    INSTALL_COMMAND,
    PINNED_PI_VERSION,
    PINNED_SUBAGENTS_VERSION,
    DoctorProbe,
    doctor,
    read_opencode_metadata,
    runtime_doctor,
)
from pitwall.workbench.hosted_profiles import ProviderMetadata

PROFILE = {
    "provider": "fixture",
    "modelId": "fixture-model",
    "endpoint": "http://127.0.0.1:9/v1",
    "api": "openai-completions",
    "servedContextTokens": 32768,
    "maxCompletionTokens": 4096,
    "reasoningLevel": "off",
    "allowProviderFallback": False,
}


def install_fake_toolchain(
    root: Path, *, pi: str | None = PINNED_PI_VERSION, subagents: str | None = "0.19.0"
) -> Path:
    """A Pi package tree with the pinned layout; returns the Pi CLI script path."""
    modules = root / "toolchain" / "node_modules"
    package = modules / "@earendil-works" / "pi-coding-agent"
    (package / "dist").mkdir(parents=True)
    (package / "package.json").write_text(
        json.dumps({"name": "@earendil-works/pi-coding-agent", "version": pi})
    )
    script = package / "dist" / "cli.js"
    script.write_text("// fake pi\n")
    if subagents is not None:
        backend = modules / "@tintinweb" / "pi-subagents"
        (backend / "dist").mkdir(parents=True)
        (backend / "package.json").write_text(
            json.dumps({"name": "@tintinweb/pi-subagents", "version": subagents})
        )
        (backend / "dist" / "index.js").write_text("// fake backend\n")
    return script


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private HOME with a pinned fake toolchain; the real home is never read or written."""
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setenv("PITWALL_WORKBENCH_PI_BIN", str(install_fake_toolchain(tmp_path)))
    monkeypatch.setenv("PITWALL_WORKBENCH_AGENT_DIR", str(root / "agent"))
    monkeypatch.delenv("PITWALL_WORKBENCH_TINTIN_EXTENSION", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return root


def write_profile(root: Path, **changes: Any) -> Path:
    keyless = {} if "apiKeyEnv" in changes else {"keyless": "dummy"}
    profile = {"resourceGroup": "cli-test", **keyless, **PROFILE, **changes}
    path = root / "profile.json"
    path.write_text(
        json.dumps({"schemaVersion": 1, "profiles": {"local": profile}}), encoding="utf-8"
    )
    return path


class FakeChild:
    def wait(self) -> int:
        return 0


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Replace ``launch_pi`` and record the options it was called with."""
    seen: list[Any] = []

    def fake_launch(options: Any, *, rpc: bool = True) -> FakeChild:
        seen.append((options, rpc))
        return FakeChild()

    monkeypatch.setattr(cli, "launch_pi", fake_launch)
    return seen


def tree(root: Path) -> list[str]:
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


@pytest.mark.parity
def test_default_agent_state_is_isolated_by_mode_profile_and_canonical_cwd() -> None:
    """Source: cli.test.ts 'default agent state is isolated by mode, profile, and canonical cwd'."""
    root = Path("/tmp/pitwall-workbench-cli-state")
    a = cli.default_agent_dir("local-coder", "/tmp/project-a", False, root)
    assert a == cli.default_agent_dir("local-coder", "/tmp/project-a", False, root)
    assert a != cli.default_agent_dir("other-profile", "/tmp/project-a", False, root)
    assert a != cli.default_agent_dir("local-coder", "/tmp/project-a", True, root)
    assert a != cli.default_agent_dir("local-coder", "/tmp/project-a", True, root, True)
    assert a != cli.default_agent_dir("local-coder", "/tmp/project-b", False, root)


@pytest.mark.parity
def test_default_agent_state_uses_the_real_cwd_behind_a_symlink(tmp_path: Path) -> None:
    """Source: cli.test.ts 'default agent state uses the real cwd behind a symlink'."""
    target = tmp_path / "target"
    link = tmp_path / "link"
    target.mkdir()
    link.symlink_to(target)
    assert cli.default_agent_dir("local-coder", link, False, tmp_path) == cli.default_agent_dir(
        "local-coder", target, False, tmp_path
    )


@pytest.mark.parity
def test_doctor_and_usage_commands_are_read_only_json_interfaces(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Source: cli.test.ts 'doctor and usage commands are read-only JSON interfaces'."""
    accounting = home / "accounting.jsonl"
    accounting.write_text("")
    before = tree(home)
    assert cli.main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["hosted"] == []
    assert report["native"] == {"status": "local-profile-only"}
    assert cli.main(["usage", str(accounting)]) == 0
    usage = json.loads(capsys.readouterr().out)
    assert (usage["requests"], usage["settled"], usage["unavailable"]) == (0, 0, 0)
    assert tree(home) == before


@pytest.mark.parity
def test_missing_credential_is_a_concise_nonzero_cli_error(
    home: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Source: cli.test.ts 'missing credential is a concise nonzero CLI error'."""
    profile = write_profile(home, apiKeyEnv="PITWALL_WORKBENCH_TEST_MISSING_KEY")
    monkeypatch.delenv("PITWALL_WORKBENCH_TEST_MISSING_KEY", raising=False)
    assert cli.main(["launch", str(profile), "local", str(home)]) == 1
    captured = capsys.readouterr()
    assert captured.err.strip().splitlines()[-1] == (
        "missing credential environment variable: PITWALL_WORKBENCH_TEST_MISSING_KEY"
    )
    assert "Traceback" not in captured.err


def expect_concise_profile_error(home: Path, kind: str, capsys: pytest.CaptureFixture[str]) -> None:
    profile = home / "profile.json"
    if kind == "malformed":
        profile.write_text('{"canary":"PROFILE_SECRET_CANARY",\n')
    assert cli.main(["launch", str(profile), "local", str(home)]) == 1
    captured = capsys.readouterr()
    message = captured.err.strip().splitlines()[-1]
    assert message
    assert "Traceback" not in captured.err
    assert not message.startswith("Error:")
    assert "PROFILE_SECRET_CANARY" not in captured.out
    assert "PROFILE_SECRET_CANARY" not in captured.err
    if kind == "malformed":
        assert message == "invalid profile configuration"


@pytest.mark.parity
def test_missing_profile_is_a_concise_nonzero_cli_error(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Source: cli.test.ts 'missing profile is a concise nonzero CLI error'."""
    expect_concise_profile_error(home, "missing", capsys)


@pytest.mark.parity
def test_malformed_profile_is_a_concise_nonzero_cli_error(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Source: cli.test.ts 'malformed profile is a concise nonzero CLI error'."""
    expect_concise_profile_error(home, "malformed", capsys)


@pytest.mark.parity
def test_malformed_hosted_credential_metadata_is_concise_and_redacted(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Source: cli.test.ts 'malformed hosted credential metadata is concise and redacted'."""
    key_env = "PITWALL_WORKBENCH_TEST_AUTH_KEY"  # pragma: allowlist secret
    profile = write_profile(home, apiKeyEnv=key_env, accountRef="fixture-account")
    auth = home / ".local/share/opencode/auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text('{"fixture-account":{"key":"AUTH_SECRET_CANARY"}\n')
    assert cli.main(["launch", str(profile), "local", str(home)]) == 1
    captured = capsys.readouterr()
    assert captured.err.strip().splitlines()[-1] == "invalid hosted credential metadata"
    assert "AUTH_SECRET_CANARY" not in captured.out + captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parity
def test_invalid_usage_keeps_the_usage_exit_code_without_a_stack(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Source: cli.test.ts 'invalid usage keeps the usage exit code without a stack'."""
    assert cli.main(["usage"]) == 2
    err = capsys.readouterr().err
    assert err.strip() == "usage: pitwall workbench usage <accounting.jsonl>"
    assert "Traceback" not in err


# --- Review Focus 5: the toolchain check runs before any state is created ---------------------


def clear_pi(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.delenv("PITWALL_WORKBENCH_PI_BIN", raising=False)
    monkeypatch.setenv("PATH", str(empty))


LAUNCHING_COMMANDS = {
    "launch": lambda home, profile: ["launch", str(profile), "local", str(home)],
    "launch-native": lambda home, profile: [
        "launch",
        str(profile),
        "local",
        str(home),
        "--native-child",
    ],
    "compare": lambda home, profile: ["compare", str(profile), str(home / "compare-out")],
    "hosted-acceptance": lambda home, profile: [
        "hosted-acceptance",
        str(profile),
        str(home / "auth.json"),
        str(home / "hosted-out"),
    ],
    "hosted-native-acceptance": lambda home, profile: [
        "hosted-native-acceptance",
        str(profile),
        str(home / "auth.json"),
        str(home / "native-out"),
    ],
    "baseline": lambda home, profile: [
        "baseline",
        str(profile),
        "local",
        str(home / "fixture"),
        str(home / "report.json"),
    ],
}


@pytest.mark.parametrize("command", sorted(LAUNCHING_COMMANDS))
def test_missing_pi_fails_before_worktree(
    command: str,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    launched: list[Any],
) -> None:
    profile = write_profile(home, apiKeyEnv="PITWALL_WORKBENCH_TEST_MISSING_KEY")
    clear_pi(monkeypatch, tmp_path)
    before = tree(home)
    assert cli.main(LAUNCHING_COMMANDS[command](home, profile)) == 1
    err = capsys.readouterr().err
    assert "pi is not installed" in err
    assert PINNED_PI_VERSION in err
    assert PINNED_SUBAGENTS_VERSION in err
    assert INSTALL_COMMAND in err
    assert "pitwall agents setup pi" in err
    assert tree(home) == before, "no worktree, agent directory, or state file was created"
    assert launched == []


@pytest.mark.parametrize("command", sorted(LAUNCHING_COMMANDS))
def test_wrong_pi_version_fails_before_state_written(
    command: str,
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    launched: list[Any],
) -> None:
    older = tmp_path / "older"
    older.mkdir()
    monkeypatch.setenv("PITWALL_WORKBENCH_PI_BIN", str(install_fake_toolchain(older, pi="0.84.3")))
    profile = write_profile(home, apiKeyEnv="PITWALL_WORKBENCH_TEST_MISSING_KEY")
    before = tree(home)
    assert cli.main(LAUNCHING_COMMANDS[command](home, profile)) == 1
    err = capsys.readouterr().err
    assert "0.84.3" in err
    assert f"{PINNED_PI_VERSION} is required" in err
    assert INSTALL_COMMAND in err
    assert tree(home) == before, "no worktree, agent directory, or state file was created"
    assert launched == []


def test_a_native_launch_also_requires_the_pinned_subagents_backend(
    home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    launched: list[Any],
) -> None:
    stale = tmp_path / "stale"
    stale.mkdir()
    monkeypatch.setenv(
        "PITWALL_WORKBENCH_PI_BIN", str(install_fake_toolchain(stale, subagents="0.18.0"))
    )
    profile = write_profile(home)
    before = tree(home)
    assert cli.main(["launch", str(profile), "local", str(home), "--planning"]) == 1
    err = capsys.readouterr().err
    assert "@tintinweb/pi-subagents 0.18.0" in err
    assert "0.19.0 is required" in err
    assert INSTALL_COMMAND in err
    assert tree(home) == before
    assert launched == []


def test_a_pinned_toolchain_is_reported_and_launches_interactively(
    home: Path, capsys: pytest.CaptureFixture[str], launched: list[Any]
) -> None:
    profile = write_profile(home)
    assert cli.main(["launch", str(profile), "local", str(home), "--continue", "--restricted"]) == 0
    assert f"@earendil-works/pi-coding-agent {PINNED_PI_VERSION}" in capsys.readouterr().err
    ((options, rpc),) = launched
    assert rpc is False
    assert options.continue_session is True
    assert options.restricted is True
    assert options.cwd == home
    assert [Path(extension).name for extension in options.extensions] == ["extension.js"]
    assert Path(options.env["PITWALL_WORKBENCH_PROVIDER_PROFILE"]).parent == home / "agent"


def test_a_native_launch_loads_the_backend_then_the_native_extension(
    home: Path, capsys: pytest.CaptureFixture[str], launched: list[Any]
) -> None:
    profile = write_profile(home)
    assert cli.main(["launch", str(profile), "local", str(home), "--native-child"]) == 0
    assert "@tintinweb/pi-subagents 0.19.0" in capsys.readouterr().err
    ((options, _rpc),) = launched
    assert [Path(extension).name for extension in options.extensions] == [
        "index.js",
        "native-extension.js",
    ]
    assert "PITWALL_WORKBENCH_NATIVE_PROFILE" in options.env
    assert "PITWALL_WORKBENCH_PROVIDER_PROFILE" not in options.env


def test_compare_and_acceptance_commands_reach_their_runners(
    home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, tuple[Any, ...]]] = []

    def recorder(name: str, result: Any) -> Any:
        def call(*args: Any) -> Any:
            calls.append((name, args))
            return result

        return call

    monkeypatch.setattr(cli, "run_comparison", recorder("compare", {"ok": 1}))
    monkeypatch.setattr(cli, "run_hosted_acceptance", recorder("hosted-acceptance", 0))
    monkeypatch.setattr(
        cli, "run_hosted_native_acceptance", recorder("hosted-native-acceptance", 0)
    )
    monkeypatch.setattr(cli, "run_baseline", recorder("baseline", 3))
    monkeypatch.setattr(cli, "reevaluate_comparison", recorder("reevaluate", {}))
    monkeypatch.setattr(cli, "reevaluate_child_only", recorder("reevaluate-child", {}))
    assert cli.main(["compare", "config.json", "out", "3", "--bounded-single", "A-stock"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": 1}
    options = calls[-1][1][0]
    assert (options.repetitions, options.bounded_single, options.candidates) == (
        3,
        True,
        ("A-stock",),
    )
    assert cli.main(["hosted-acceptance", "c.json", "a.json", "out", "zai-coding-plan"]) == 0
    assert calls[-1][1][3] == ["zai-coding-plan"]
    assert cli.main(["hosted-native-acceptance", "c.json", "a.json", "out"]) == 0
    assert cli.main(["baseline", "c.json", "local", "fixture", "report.json"]) == 3
    assert cli.main(["reevaluate", "in.json", "out.json"]) == 0
    assert cli.main(["reevaluate-child", "in.json", "out.json"]) == 0
    assert [name for name, _ in calls] == [
        "compare",
        "hosted-acceptance",
        "hosted-native-acceptance",
        "baseline",
        "reevaluate",
        "reevaluate-child",
    ]


def test_fixture_creates_a_disposable_fixture_and_refuses_an_existing_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "fixture"
    assert cli.main(["fixture", str(target)]) == 0
    assert (target / "preserve.txt").is_file()
    capsys.readouterr()
    assert cli.main(["fixture", str(target)]) == 1
    assert capsys.readouterr().err.strip()


def test_unknown_and_missing_commands_print_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 2
    assert "usage: pitwall workbench" in capsys.readouterr().err
    assert cli.main(["nope"]) == 2
    assert "usage: pitwall workbench" in capsys.readouterr().err


def _forbid_running(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("help must not run a command or check the toolchain")

    monkeypatch.setattr(cli, "_check_toolchain", boom)
    monkeypatch.setattr(
        cli,
        "_COMMANDS",
        dict.fromkeys(cli._COMMANDS, boom),
    )


@pytest.mark.parametrize("flag", ["-h", "--help"])
def test_group_help_prints_usage_to_stdout_and_exits_zero(
    flag: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _forbid_running(monkeypatch)
    assert cli.main([flag]) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == cli.USAGE
    assert captured.err == ""


@pytest.mark.parametrize("flag", ["-h", "--help"])
@pytest.mark.parametrize("command", sorted(cli._COMMANDS))
def test_every_subcommand_answers_help_on_stdout_without_running(
    command: str,
    flag: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _forbid_running(monkeypatch)
    assert cli.main([command, flag]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith(f"usage: pitwall workbench {command}")
    assert captured.err == ""


def test_invalid_subcommand_arguments_still_exit_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["nope", "--help"]) == 2
    assert "usage: pitwall workbench" in capsys.readouterr().err
    assert cli.main(["fixture"]) == 2
    assert "usage: pitwall workbench fixture" in capsys.readouterr().err


# --- doctor.ts cases of hosted-profiles.test.ts ----------------------------------------------


@pytest.mark.parity
def test_doctor_reports_metadata_without_exposing_credential_values() -> None:
    """Source: hosted-profiles.test.ts 'doctor reports metadata without exposing credential values'."""
    report = doctor(
        {"MINIMAX_API_KEY": "secret"},  # pragma: allowlist secret
        [
            ProviderMetadata(
                name="minimax-coding-plan",
                models=["model"],
                auth_store_entry="minimax-coding-plan",
            )
        ],
    )
    assert "secret" not in json.dumps(report)
    assert report["hosted"][0]["credentialConfigured"] is True
    assert report["hosted"][0]["status"] == "discovery-required"


def linux_probe(**changes: Any) -> DoctorProbe:
    values: dict[str, Any] = {
        "platform": "linux",
        "architecture": "x64",
        "node_version": "22.22.1",
        # Never spawn the host's real bwrap: whether it works is a property of the CI host.
        "bubblewrap_failure": lambda _path: None,
        **changes,
    }
    return DoctorProbe(**values)


@pytest.mark.parity
def test_doctor_reports_deterministic_runtime_prerequisites_without_spawning_commands() -> None:
    """Source: hosted-profiles.test.ts 'doctor reports deterministic runtime prerequisites without spawning commands'."""
    report = runtime_doctor(
        {},
        linux_probe(
            find_executable=lambda name, _env: "/fixture/flock" if name == "flock" else None,
            path_exists=lambda _path: False,
        ),
    )
    assert report["node"] == {"version": "22.22.1", "supported": True}
    assert report["platform"] == "linux"
    assert report["architecture"] == {"name": "x64", "supported": True}
    assert report["flock"] == {"status": "available", "path": "/fixture/flock"}
    assert report["bubblewrap"] == {"status": "missing-bubblewrap", "optional": True}
    assert report["setpriv"] == {"status": "missing-setpriv"}
    assert report["restricted"] == {"status": "missing-bubblewrap"}
    assert runtime_doctor({}, linux_probe(find_executable=lambda *_: None))["flock"] == {
        "status": "missing-flock"
    }
    unsupported = runtime_doctor(
        {},
        linux_probe(architecture="s390x", find_executable=lambda *_: "/fixture/tool"),
    )
    assert unsupported["architecture"] == {"name": "s390x", "supported": False}
    assert unsupported["restricted"] == {
        "status": "unsupported-architecture",
        "architecture": "s390x",
    }

    def must_not_probe(*_: Any) -> str:
        raise AssertionError("must not probe on unsupported platform")

    darwin = runtime_doctor({}, linux_probe(platform="darwin", find_executable=must_not_probe))
    assert darwin["flock"] == {"status": "unsupported-platform"}


@pytest.mark.parity
def test_doctor_does_not_treat_path_aliases_as_restricted_prerequisites() -> None:
    """Source: hosted-profiles.test.ts 'doctor does not treat PATH aliases as restricted prerequisites'."""
    aliases = runtime_doctor(
        {},
        linux_probe(find_executable=lambda *_: "/tmp/fake-tool", path_exists=lambda _path: False),
    )
    assert aliases["restricted"] == {"status": "missing-bubblewrap"}
    report = runtime_doctor(
        {},
        linux_probe(
            find_executable=lambda *_: "/tmp/fake-tool",
            path_exists=lambda path: path in {"/usr/bin/bwrap", "/usr/bin/setpriv"},
            setpriv_supports_seccomp_filter=lambda _path: True,
        ),
    )
    assert report["bubblewrap"] == {
        "status": "available",
        "optional": True,
        "path": "/usr/bin/bwrap",
    }
    assert report["setpriv"] == {"status": "available", "path": "/usr/bin/setpriv"}
    assert report["restricted"] == {
        "status": "available",
        "architecture": "x64",
        "bubblewrap": "/usr/bin/bwrap",
        "setpriv": "/usr/bin/setpriv",
    }


@pytest.mark.parity
def test_doctor_refuses_restricted_mode_when_setpriv_predates_seccomp_filters() -> None:
    """Source: hosted-profiles.test.ts 'doctor refuses restricted mode when setpriv predates seccomp filters'."""
    probe: dict[str, Any] = {
        "find_executable": lambda *_: "/fixture/flock",
        "path_exists": lambda _path: True,
    }
    old = runtime_doctor({}, linux_probe(**probe, setpriv_supports_seccomp_filter=lambda _p: False))
    assert old["restricted"] == {
        "status": "setpriv-lacks-seccomp-filter",
        "setpriv": "/usr/bin/setpriv",
    }
    current = runtime_doctor(
        {}, linux_probe(**probe, setpriv_supports_seccomp_filter=lambda _p: True)
    )
    assert current["restricted"]["status"] == "available"


@pytest.mark.parity
def test_auth_only_provider_metadata_resolves_the_pinned_catalog_endpoint_and_api(
    tmp_path: Path,
) -> None:
    """Source: hosted-profiles.test.ts 'auth-only provider metadata resolves the pinned catalog endpoint and API'."""
    fixture_key = "redacted-test-key"  # pragma: allowlist secret
    entry = {"type": "api", "key": fixture_key}
    (tmp_path / "auth.json").write_text(
        json.dumps({"minimax-coding-plan": entry, "zai-coding-plan": entry})
    )
    metadata = {
        entry.name: entry
        for entry in read_opencode_metadata(
            tmp_path / "missing-config.json", tmp_path / "auth.json"
        )
    }
    minimax = metadata["minimax-coding-plan"]
    assert (minimax.base_url, minimax.api, minimax.endpoint_class) == (
        "https://api.minimax.io/anthropic",
        "anthropic-messages",
        "official-api",
    )
    zai = metadata["zai-coding-plan"]
    assert (zai.base_url, zai.api, zai.endpoint_class) == (
        "https://api.z.ai/api/coding/paas/v4",
        "openai-completions",
        "official-api",
    )


@pytest.mark.parity
def test_doctor_rejects_wrong_endpoint_and_malformed_auth_metadata(tmp_path: Path) -> None:
    """Source: hosted-profiles.test.ts 'doctor rejects wrong endpoint and malformed auth metadata'."""
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "provider": {
                    "zai": {
                        "options": {"baseURL": "https://wrong.example/v1"},
                        "models": {"glm-5.2": {}},
                    }
                }
            }
        )
    )
    (tmp_path / "auth.json").write_text(json.dumps({"zai": {"type": "api"}}))
    (entry,) = read_opencode_metadata(tmp_path / "config.json", tmp_path / "auth.json")
    assert entry.endpoint_class is None
    assert entry.auth_store_entry is None


@pytest.mark.parity
def test_doctor_startup_tolerates_missing_opencode_files(tmp_path: Path) -> None:
    """Source: hosted-profiles.test.ts 'doctor startup tolerates missing OpenCode files'."""
    assert (
        read_opencode_metadata(tmp_path / "missing-config.json", tmp_path / "missing-auth.json")
        == []
    )
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "provider": {
                    "alibaba-token-plan": {
                        "options": {
                            "baseURL": "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1"
                        },
                        "models": {"qwen3.8-flash": {}},
                    }
                }
            }
        )
    )
    (entry,) = read_opencode_metadata(tmp_path / "config.json", tmp_path / "missing-auth.json")
    assert entry.name == "alibaba-token-plan"
    assert entry.endpoint_class == "official-api"
    assert entry.auth_store_entry is None


def test_a_syntax_error_in_local_opencode_metadata_is_a_concise_cli_error(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = home / ".config/opencode/opencode.json"
    config.parent.mkdir(parents=True)
    config.write_text("{not json")
    assert cli.main(["doctor"]) == 1
    assert capsys.readouterr().err.strip() == "invalid local OpenCode metadata"
