"""The workbench keeps its state under ``$XDG_STATE_HOME`` when set, in Python and in the extension."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest

from pitwall.workbench.account_budget import default_account_budget_dir
from pitwall.workbench.admission import default_admission_dir
from pitwall.workbench.launcher import PiLaunchOptions, _runtime_base
from pitwall.workbench.profile import CompiledProfile

EXTENSIONS = Path(__file__).resolve().parents[2] / "src/pitwall/workbench/pi_extensions"


@pytest.fixture
def homes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    home = tmp_path / "home"
    state = tmp_path / "xdg-state"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("PITWALL_WORKBENCH_RUNTIME_DIR", raising=False)
    return home, state


def test_python_state_directories_follow_xdg_state_home(
    homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _home, state = homes
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    assert default_admission_dir() == state / "pitwall/pi-workbench/admission"
    assert default_account_budget_dir() == state / "pitwall/pi-workbench/account-budgets"
    assert _runtime_base(_options()) == state / "pitwall/pi-workbench/runtime"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_python_state_directories_default_to_local_state(
    value: str | None, homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    home, _state = homes
    if value is None:
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_STATE_HOME", value)

    assert default_admission_dir() == home / ".local/state/pitwall/pi-workbench/admission"
    assert default_account_budget_dir() == (
        home / ".local/state/pitwall/pi-workbench/account-budgets"
    )


def _options() -> PiLaunchOptions:
    return PiLaunchOptions(cwd=Path.cwd(), profile=cast(CompiledProfile, None))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for the extensions")
@pytest.mark.parametrize("state_home", ["/xdg/state", "", None])
def test_extension_directories_follow_xdg_state_home(
    state_home: str | None, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ["PATH"], "HOME": str(home)}
    if state_home is not None:
        env["XDG_STATE_HOME"] = state_home
    script = (
        "const a = await import(process.argv[1] + '/shared-admission.js');"
        "const b = await import(process.argv[1] + '/account-budget.js');"
        "console.log(JSON.stringify({"
        "admission: new a.SharedRequestAdmission('g').directory,"
        "budget: new b.AccountBudgetAdmission("
        "{accountGroup: 'g', maxConcurrent: 1, inFlightTokenBudget: 1, unknownUsage: 'hold'}).directory}));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(EXTENSIONS)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr[-500:]
    directories = json.loads(result.stdout)
    base = state_home or str(home / ".local/state")
    assert directories["admission"] == f"{base}/pitwall/pi-workbench/admission"
    assert directories["budget"] == f"{base}/pitwall/pi-workbench/account-budgets"
