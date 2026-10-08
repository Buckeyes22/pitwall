"""``pitwall.cli`` dispatches through a lazy group table."""

from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from pitwall import cli

CLI_DIR = Path(cli.__file__).parent
MAX_MODULE_LINES = 800


def test_every_group_lazy() -> None:
    """Importing ``pitwall.cli`` loads no group module; each entry resolves when invoked."""
    modules = sorted({module for _group, module, _function in cli.GROUPS})
    program = (
        "import sys\n"
        "import pitwall.cli\n"
        f"loaded = [m for m in {modules!r} if m in sys.modules]\n"
        "print(','.join(loaded))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
    for group, module, function in cli.GROUPS:
        assert callable(getattr(importlib.import_module(module), function)), group


def test_group_names_are_unique_and_include_the_new_groups() -> None:
    names = [group for group, _module, _function in cli.GROUPS]
    assert len(names) == len(set(names))
    assert {"agents", "usage", "mcp", "doctor"} <= set(names)


def test_main_calls_the_invoked_group(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    module = importlib.import_module("pitwall.cli.leases")
    monkeypatch.setattr(module, "cmd_leases", lambda argv: calls.append(argv) or 0)
    assert cli.main(["leases", "list"]) == 0
    assert calls == [["list"]]


def test_unknown_group_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["nope"]) == 1
    assert "Unknown command group: nope" in capsys.readouterr().err


def test_no_cli_module_exceeds_the_line_limit() -> None:
    lengths = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in CLI_DIR.glob("*.py")
    }
    assert {name: n for name, n in lengths.items() if n > MAX_MODULE_LINES} == {}
