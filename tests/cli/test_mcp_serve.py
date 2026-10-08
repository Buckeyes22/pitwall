"""``pitwall mcp serve broker`` and ``pitwall mcp serve channel``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pitwall import cli
from tests.hang_guard import HANG_GUARD_SECS


def test_serve_broker() -> None:
    from pitwall import mcp

    with (
        patch("pitwall.mcp.ensure_runtime_env") as ensure,
        patch.object(mcp.mcp, "run") as run,
    ):
        assert cli.main(["mcp", "serve", "broker"]) == 0

    ensure.assert_called_once_with()
    run.assert_called_once_with(transport="stdio")


def test_serve_broker_still_requires_its_runtime_environment() -> None:
    with (
        patch("pitwall.mcp.ensure_runtime_env", side_effect=SystemExit(2)) as ensure,
        pytest.raises(SystemExit),
    ):
        cli.main(["mcp", "serve", "broker"])
    ensure.assert_called_once_with()


def test_serve_without_a_server_name_is_a_usage_error() -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["mcp", "serve"])
    assert raised.value.code == 2


def test_serve_channel_without_broker_config(tmp_path: Path) -> None:
    """The channel server starts with no DATABASE_URL, REDIS_URL, or RUNPOD_API_KEY, and
    never loads the broker's MCP SDK."""
    program = (
        "import sys\n"
        "from pitwall.cli import main\n"
        "rc = main(['mcp', 'serve', 'channel'])\n"
        "sys.stderr.write('loaded-mcp-sdk=%s\\n' % ('mcp' in sys.modules))\n"
        "raise SystemExit(rc)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "loaded-mcp-sdk=False" in result.stderr


def test_serve_broker_json_never_writes_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    from pitwall import mcp

    with patch("pitwall.mcp.ensure_runtime_env"), patch.object(mcp.mcp, "run"):
        assert cli.main(["mcp", "serve", "broker", "--json"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert '"transport": "stdio"' in captured.err


def test_serve_broker_without_json_also_never_writes_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pitwall import mcp

    with patch("pitwall.mcp.ensure_runtime_env"), patch.object(mcp.mcp, "run"):
        assert cli.main(["mcp", "serve", "broker"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
