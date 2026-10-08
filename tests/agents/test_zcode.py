"""ZCode dispatch contract: saved login/model, explicit policy, and no auth mutation."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from pitwall.agents import setup
from pitwall.agents.errors import UsageError
from pitwall.agents.harnesses import get_adapter
from pitwall.agents.harnesses.base import ARGV_PROMPT_LIMIT_BYTES
from tests.agents.shim_test_support import ShimSandbox


@pytest.mark.parametrize(
    "flag",
    [
        "--model",
        "--model=x",
        "-m",
        "--prompt=x",
        "-p",
        "--target=x",
        "--continue",
        "--resume=x",
        "--cwd=x",
        "--mode=yolo",
        "--output-format=json",
        "--enable-workflow",
        "--effort=high",
        "login",
        "logout",
        "app-server",
    ],
)
def test_reserved_flags_do_not_start_zcode(flag: str) -> None:
    sandbox = ShimSandbox()
    try:
        sandbox.install_harness("zcode")
        result = sandbox.run("zcode", [str(sandbox.prompt()), flag])
        assert result.returncode == 64
        assert result.stdout == b"SHIM-DONE exit=64\n"
        assert not sandbox.args_file.exists()
        assert not sandbox.ledger_records()
    finally:
        sandbox.cleanup()


@pytest.mark.parametrize("restricted,mode", [(False, "yolo"), (True, "build")])
def test_headless_dispatch_uses_workspace_and_hides_prompt(restricted: bool, mode: str) -> None:
    sandbox = ShimSandbox()
    try:
        sandbox.install_harness("zcode")
        prompt = sandbox.prompt("private prompt\n")
        result = sandbox.run(
            "zcode",
            [str(prompt)],
            env=sandbox.environment(
                PITWALL_AGENTS_UNRESTRICTED="0" if restricted else "1",
                PITWALL_AGENTS_RESULT="1",
            ),
        )
        assert result.returncode == 0, result.stderr
        args = sandbox.captured_args()
        assert args[args.index("--mode") + 1] == mode
        assert args[args.index("--prompt") + 1] == "private prompt"
        assert "--cwd" in args
        assert "--model" not in args
        records = sandbox.ledger_records()
        assert records[-1]["model"] == "zcode-default"
        adapter = get_adapter("zcode")
        prepared = adapter.prepare(
            adapter.parse([str(prompt)], {}, sandbox.home),
            "/bin/zcode",
            b"private prompt\n",
            {},
            {"workspacePath": "/workspace"},
        )
        assert "private prompt" not in prepared.sanitized_args
        assert prepared.argv[prepared.argv.index("--cwd") + 1] == "/workspace"
    finally:
        sandbox.cleanup()


def test_no_model_or_effort_claim_and_oversized_prompt_refused() -> None:
    adapter = get_adapter("zcode")
    for model, effort in [("glm-5.3", None), ("zcode-default", "high")]:
        with pytest.raises(UsageError):
            adapter.workflow_args(model, effort, Path("/prompt.md"))
    with pytest.raises(UsageError, match="limited"):
        adapter.argv_prompt_text(b"x" * (ARGV_PROMPT_LIMIT_BYTES + 1))


def test_manual_install_never_downloads_or_executes(tmp_path: Path) -> None:
    spec = next(s for s in setup.load_install_specs(system_name="Linux") if s.harness_id == "zcode")
    downloader, runner, verifier = Mock(), Mock(), Mock()
    env = {"HOME": str(tmp_path), "PATH": str(tmp_path / "bin")}
    for dry_run in (False, True):
        result = setup.install_selected(
            [spec],
            env,
            -1,
            dry_run=dry_run,
            downloader=downloader,
            installer_runner=runner,
            verifier=verifier,
        )
        assert result[0].status == "failed"
    binary = tmp_path / ".local/bin/zcode"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    assert (
        setup.install_selected(
            [spec], env, -1, downloader=downloader, installer_runner=runner, verifier=verifier
        )[0].status
        == "already-installed"
    )
    downloader.assert_not_called()
    runner.assert_not_called()
    verifier.assert_not_called()
