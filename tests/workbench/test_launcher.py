"""Launcher tests, translated from launcher-dependencies.test.ts and launcher-environment.test.ts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from pitwall.workbench.launcher import (
    PI_EXTENSIONS_DIR,
    LauncherError,
    PiLaunchOptions,
    extension_path,
    hoisted_dependency_roots,
    launch_command,
    launch_environment,
    launch_pi,
    pi_args,
    runtime_root,
)
from pitwall.workbench.profile import CompiledProfile, compile_profile
from tests.workbench.pi_support import pinned_pi, requires_pi

PROFILE: dict[str, Any] = {
    "provider": "fixture",
    "modelId": "model",
    "endpoint": "http://localhost/v1",
    "api": "openai-completions",
    "apiKeyEnv": "SELECTED_KEY",  # pragma: allowlist secret
    "servedContextTokens": 32768,
    "maxCompletionTokens": 4096,
    "resourceGroup": "env",
    "allowProviderFallback": False,
}


def compiled_in(root: Path, **changes: Any) -> CompiledProfile:
    profile = {key: value for key, value in {**PROFILE, **changes}.items() if value is not None}
    return compile_profile("test", profile, root / "agent")


@pytest.mark.parity
def test_a_dependency_installed_inside_the_package_runtime_needs_no_extra_mount() -> None:
    """Source: launcher-dependencies.test.ts 'a dependency installed inside the package runtime needs no extra mount'."""
    assert (
        hoisted_dependency_roots("/opt/wb/", "/opt/wb/node_modules/@earendil-works/pi-coding-agent")
        == []
    )


@pytest.mark.parity
def test_an_npm_hoisted_dependency_root_is_mounted() -> None:
    """Source: launcher-dependencies.test.ts 'an npm-hoisted dependency root is mounted'."""
    assert hoisted_dependency_roots(
        "/opt/app/node_modules/pi-workbench/",
        "/opt/app/node_modules/@earendil-works/pi-coding-agent",
    ) == [Path("/opt/app/node_modules")]


@pytest.mark.parity
def test_an_unscoped_hoisted_dependency_resolves_to_its_node_modules_root() -> None:
    """Source: launcher-dependencies.test.ts 'an unscoped hoisted dependency resolves to its node_modules root'."""
    assert hoisted_dependency_roots(
        "/opt/app/node_modules/pi-workbench/", "/opt/app/node_modules/pi-coding-agent"
    ) == [Path("/opt/app/node_modules")]


def test_a_dependency_outside_any_node_modules_needs_no_mount() -> None:
    assert hoisted_dependency_roots("/opt/wb", "/usr/local/pi") == []


@pytest.mark.parity
@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_child_gets_selected_credential_but_not_unrelated_ambient_accounts(tmp_path: Path) -> None:
    """Source: launcher-environment.test.ts 'child gets selected credential but not unrelated ambient accounts'."""
    compiled = compiled_in(tmp_path)
    script = tmp_path / "probe.mjs"
    script.write_text(
        "console.log(JSON.stringify({selected: !!process.env.SELECTED_KEY,"
        " unrelated: !!process.env.UNRELATED_ACCOUNT_KEY,"
        " checks: !!process.env.PITWALL_WORKBENCH_APPROVED_CHECKS,"
        " accounting: process.env.PITWALL_WORKBENCH_ACCOUNTING_PATH}));"
    )
    child = launch_pi(
        PiLaunchOptions(
            cwd=tmp_path,
            profile=compiled,
            pi_bin=script,
            env={
                "SELECTED_KEY": "fixture",
                "UNRELATED_ACCOUNT_KEY": "must-not-propagate",  # pragma: allowlist secret
                "PITWALL_WORKBENCH_APPROVED_CHECKS": "[]",
                "PITWALL_WORKBENCH_ACCOUNTING_PATH": "/tmp/accounting.jsonl",  # noqa: S108  # reason: fixture value that is only echoed back
            },
        )
    )
    assert child.stdout is not None
    output = child.stdout.read().decode()
    child.wait()
    for stream in (child.stdin, child.stdout, child.stderr):
        if stream:
            stream.close()
    assert json.loads(output) == {
        "selected": True,
        "unrelated": False,
        "checks": True,
        "accounting": "/tmp/accounting.jsonl",  # noqa: S108  # reason: fixture value that is only echoed back
    }


def test_the_launcher_environment_is_isolated_and_records_the_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNRELATED_ACCOUNT_KEY", "must-not-propagate")
    monkeypatch.delenv("PITWALL_WORKBENCH_RESOURCE_DIR", raising=False)
    compiled = compiled_in(tmp_path)
    env = launch_environment(
        PiLaunchOptions(
            cwd=tmp_path,
            profile=compiled,
            env={"SELECTED_KEY": "value", "FIXTURE_FLAG": "1"},
            passthrough_env=["FIXTURE_FLAG"],
        )
    )
    assert "UNRELATED_ACCOUNT_KEY" not in env
    assert env["SELECTED_KEY"] == "value"
    assert env["FIXTURE_FLAG"] == "1"
    assert env["PI_CODING_AGENT_DIR"] == str(compiled.agent_dir)
    assert (env["PI_TELEMETRY"], env["PI_OFFLINE"]) == ("0", "1")
    assert "PITWALL_WORKBENCH_RESTRICTED" not in env
    assert "PITWALL_WORKBENCH_RESOURCE_DIR" not in env
    assert env["PITWALL_WORKBENCH_ACCOUNT_BUDGET_DIR"].endswith("pi-workbench/account-budgets")


def test_a_missing_selected_credential_stops_the_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SELECTED_KEY", raising=False)
    with pytest.raises(
        LauncherError, match="missing credential environment variable: SELECTED_KEY"
    ):
        launch_environment(PiLaunchOptions(cwd=tmp_path, profile=compiled_in(tmp_path)))


def test_a_restricted_environment_names_the_runtime_root_and_credential(
    tmp_path: Path,
) -> None:
    compiled = compiled_in(tmp_path)
    env = launch_environment(
        PiLaunchOptions(
            cwd=tmp_path,
            profile=compiled,
            env={"SELECTED_KEY": "value"},
            restricted=True,
        )
    )
    assert env["PITWALL_WORKBENCH_RESTRICTED"] == "1"
    assert env["PITWALL_WORKBENCH_RESTRICTED_CREDENTIAL_ENV"] == "SELECTED_KEY"
    assert env["PITWALL_WORKBENCH_RESTRICTED_RUNTIME_ROOT"] == str(PI_EXTENSIONS_DIR)
    assert env["PITWALL_WORKBENCH_RESOURCE_DIR"].endswith("pi-workbench/admission")


@pytest.mark.parity
def test_launcher_argument_order_matches_the_typescript_launcher(tmp_path: Path) -> None:
    """Source: launcher.ts:50 (args) as pinned by launcher-environment.test.ts and restricted.test.ts."""
    compiled = compiled_in(tmp_path, reasoningLevel="high")
    options = PiLaunchOptions(
        cwd=tmp_path,
        profile=compiled,
        extensions=["/x/one.js"],
        extension="/x/two.js",
        continue_session=True,
    )
    assert pi_args(options) == [
        "--provider", "fixture",
        "--model", "model",
        "--continue",
        "--thinking", "high",
        "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
        "--mode", "rpc",
        "--extension", "/x/one.js",
        "--extension", "/x/two.js",
    ]  # fmt: skip
    plain = PiLaunchOptions(cwd=tmp_path, profile=compiled_in(tmp_path / "b", reasoningLevel=None))
    assert pi_args(plain, rpc=False) == [
        "--provider", "fixture",
        "--model", "model",
        "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
    ]  # fmt: skip


def test_a_restricted_launch_appends_the_packaged_restricted_extension_last(
    tmp_path: Path,
) -> None:
    options = PiLaunchOptions(
        cwd=tmp_path,
        profile=compiled_in(tmp_path),
        extension="/x/extension.js",
        restricted=True,
    )
    args = pi_args(options)
    assert args[-4:] == [
        "--extension",
        "/x/extension.js",
        "--extension",
        str(extension_path("restricted-extension")),
    ]


def test_extension_paths_come_from_package_data() -> None:
    for name in ("extension", "provider-extension", "native-extension", "restricted-extension"):
        assert extension_path(name).parent == PI_EXTENSIONS_DIR
    with pytest.raises(LauncherError, match="not found"):
        extension_path("missing-extension")


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_an_unrestricted_launch_is_node_running_pi_with_the_argument_vector(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    script = tmp_path / "pi.mjs"
    script.write_text("")
    options = PiLaunchOptions(cwd=tmp_path, profile=compiled_in(tmp_path), pi_bin=script)
    command = launch_command(options)
    assert command[:2] == [node, str(script)]
    assert command[2:] == pi_args(options)


@requires_pi
def test_packaged_extensions_are_staged_read_only_next_to_the_pinned_pi_packages(
    tmp_path: Path,
) -> None:
    options = PiLaunchOptions(
        cwd=tmp_path,
        profile=compiled_in(tmp_path),
        pi_bin=pinned_pi(),
        runtime_dir=tmp_path / "runtime",
        extension=extension_path("extension"),
    )
    root = runtime_root(options)
    assert root.parent == tmp_path / "runtime"
    assert runtime_root(options) == root  # content-addressed and reused
    staged = root / "extension.js"
    assert staged.read_bytes() == extension_path("extension").read_bytes()
    assert staged.stat().st_mode & 0o222 == 0
    assert (root / "node_modules/@earendil-works/pi-ai/package.json").is_file()
    assert (root / "node_modules/@earendil-works/pi-coding-agent/package.json").is_file()
    args = pi_args(options, root=root)
    assert args[-2:] == ["--extension", str(staged)]
