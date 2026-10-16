"""MiniMax Code dispatch contract: stdin prompt, explicit model, workspace, and policy."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from pitwall.agents import setup
from pitwall.agents.errors import UsageError
from pitwall.agents.harnesses import get_adapter
from pitwall.agents.registry import load_registry
from tests.agents.shim_test_support import ShimSandbox

M31 = "minimax/MiniMax-M3.1-Flash-Preview"


def _run(args: list[str], **env: str):
    sandbox = ShimSandbox()
    sandbox.install_harness("mcode")
    prompt = sandbox.prompt("private prompt\n")
    result = sandbox.run("mcode", [str(prompt), *args], env=sandbox.environment(**env))
    return sandbox, result


@pytest.mark.parametrize(
    "flag",
    [
        "--input=-",
        "--input-format=json",
        "--cwd=x",
        "--permission=full",
        "--output-format=json",
        "--session=x",
        "--continue",
        "-o",
        "--output-last-message=x",
        "--config=x",
        "--diagnostics-dir=x",
        "--output-schema=x",
        "review",
    ],
)
def test_reserved_flags_do_not_start_mcode(flag: str) -> None:
    sandbox, result = _run([flag])
    try:
        assert result.returncode == 64
        assert result.stdout == b"SHIM-DONE exit=64\n"
        assert not sandbox.args_file.exists()
    finally:
        sandbox.cleanup()


@pytest.mark.parametrize("unrestricted,policy", [("1", "full"), ("0", "smart")])
def test_dispatch_sends_prompt_on_stdin_with_workspace_policy_and_model(
    unrestricted: str, policy: str
) -> None:
    sandbox, result = _run([], PITWALL_AGENTS_UNRESTRICTED=unrestricted)
    try:
        assert result.returncode == 0, result.stderr
        args = sandbox.captured_args()
        assert args[:3] == ["exec", "--input", "-"]
        assert args[args.index("--permission") + 1] == policy
        assert args[args.index("--output-format") + 1] == "json"
        assert args[args.index("--model") + 1] == M31
        assert args.count("--model") == 1
        assert sandbox.captured_stdin() == b"private prompt\n"
        assert sandbox.ledger_records()[-1]["model"] == M31
    finally:
        sandbox.cleanup()
    adapter = get_adapter("mcode")
    prepared = adapter.prepare(
        adapter.parse(["/p.md"], {}, Path("/home")),
        "/bin/mcode",
        b"x",
        {},
        {"workspacePath": "/ws"},
    )
    assert prepared.argv[prepared.argv.index("--cwd") + 1] == "/ws"
    assert prepared.stdin == b"x"


@pytest.mark.parametrize(
    "forwarded",
    [["-m", "minimax/MiniMax-M3"], ["-m=minimax/MiniMax-M3"], ["--model", "minimax/MiniMax-M3"]],
)
def test_short_model_flag_becomes_one_long_flag(forwarded: list[str]) -> None:
    sandbox, result = _run(forwarded)
    try:
        assert result.returncode == 0, result.stderr
        args = sandbox.captured_args()
        assert "-m" not in args and not any(a.startswith("-m=") for a in args)
        assert args.count("--model") == 1
        assert args[args.index("--model") + 1] == "minimax/MiniMax-M3"
    finally:
        sandbox.cleanup()


def test_variant_suffix_is_kept_and_validated_on_its_base() -> None:
    sandbox, result = _run(["--model", f"{M31}#none-thinking"])
    try:
        assert result.returncode == 0, result.stderr
        args = sandbox.captured_args()
        assert args[args.index("--model") + 1] == f"{M31}#none-thinking"
    finally:
        sandbox.cleanup()


@pytest.mark.parametrize("model", ["minimax/MiniMax-M3.2", "MiniMax-M3", "minimax/"])
def test_unregistered_minimax_model_is_refused(model: str) -> None:
    sandbox, result = _run(["--model", model])
    try:
        assert result.returncode == 64
        assert not sandbox.args_file.exists()
    finally:
        sandbox.cleanup()


def test_custom_provider_models_pass_through() -> None:
    sandbox, result = _run(["--model", "my-provider/some-model"])
    try:
        assert result.returncode == 0, result.stderr
        assert "my-provider/some-model" in sandbox.captured_args()
    finally:
        sandbox.cleanup()


def test_smart_mode_refusal_becomes_exit_77() -> None:
    adapter = get_adapter("mcode")
    tail = b"The runtime rejected the call:\nHOST_CAPABILITY_UNAVAILABLE: this Runtime host cannot prompt for permission.\n"
    reason = adapter.detect_soft_denial(0, tail)
    assert reason is not None and "PITWALL_AGENTS_UNRESTRICTED=1" in reason
    assert adapter.detect_soft_denial(0, b"all done\n") is None
    assert adapter.detect_soft_denial(1, tail) is None


def test_not_signed_in_exit_propagates() -> None:
    sandbox = ShimSandbox()
    try:
        sandbox.install_harness("mcode")
        result = sandbox.run(
            "mcode",
            [str(sandbox.prompt())],
            env=sandbox.environment(
                FAKE_EXIT="3", FAKE_STDERR="mcode exec failed: Sign in to MiniMax\n"
            ),
        )
        assert result.returncode == 3
        assert b"Sign in to MiniMax" in result.stderr
    finally:
        sandbox.cleanup()


def test_workflow_args_and_registered_models() -> None:
    adapter = get_adapter("mcode")
    assert adapter.workflow_args(M31, "high", Path("/p.md")) == [
        "/p.md",
        "--model",
        M31,
        "--effort",
        "high",
    ]
    assert adapter.workflow_args(M31, None, Path("/p.md")) == ["/p.md", "--model", M31]
    models = load_registry()["harnesses"]["mcode"]["models"]
    assert set(models) == {
        "minimax/MiniMax-M2.7",
        "minimax/MiniMax-M2.7-highspeed",
        "minimax/MiniMax-M3",
        M31,
    }
    assert models[M31]["effortValues"] == ["low", "medium", "high", "xhigh", "max"]
    assert models["minimax/MiniMax-M3"]["effortValues"] == []


def test_npm_install_recipe(tmp_path: Path) -> None:
    spec = next(s for s in setup.load_install_specs(system_name="Linux") if s.harness_id == "mcode")
    assert spec.recipe.kind == "npm"
    assert spec.recipe.package == "@minimax-ai/code"
    binary = tmp_path / ".minimax-code/bin/mcode"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    runner = Mock()
    result = setup.install_selected(
        [spec],
        {"HOME": str(tmp_path), "PATH": str(tmp_path / "bin")},
        -1,
        downloader=Mock(),
        installer_runner=runner,
        verifier=Mock(),
    )
    assert result[0].status == "already-installed"
    runner.assert_not_called()


def test_parse_rejects_missing_prompt_source() -> None:
    with pytest.raises(UsageError):
        get_adapter("mcode").parse([], {}, Path("/home"))
