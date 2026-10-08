"""workbench doctor: a real bubblewrap probe and one MIN_NODE constant."""

from __future__ import annotations

import subprocess
from typing import Any
from unittest import mock

import pytest

from pitwall.workbench import doctor as wb


def _probe(**changes: Any) -> wb.DoctorProbe:
    values: dict[str, Any] = {
        "platform": "linux",
        "architecture": "x64",
        "node_version": wb.MIN_NODE,
        "find_executable": lambda *_: "/fixture/flock",
        "path_exists": lambda _path: True,
        "setpriv_supports_seccomp_filter": lambda _path: True,
        **changes,
    }
    return wb.DoctorProbe(**values)


def test_min_node_is_one_string_constant() -> None:
    assert wb.MIN_NODE == "22.22.1"
    assert wb.MINIMUM_NODE == (22, 22, 1)
    assert wb._supported_node("22.22.1")
    assert not wb._supported_node("22.22.0")


def test_a_bubblewrap_that_cannot_sandbox_makes_restricted_mode_unavailable() -> None:
    report = wb.runtime_doctor(
        {},
        _probe(bubblewrap_failure=lambda _path: "setting up uid map: Permission denied"),
    )
    assert report["restricted"] == {
        "status": "bubblewrap-unusable",
        "detail": "setting up uid map: Permission denied",
    }


def test_a_working_bubblewrap_keeps_restricted_mode_available() -> None:
    report = wb.runtime_doctor({}, _probe(bubblewrap_failure=lambda _path: None))
    assert report["restricted"]["status"] == "available"


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout="", stderr=stderr)


def test_the_real_probe_runs_a_trivial_sandbox_and_reports_its_stderr() -> None:
    with mock.patch.object(
        wb.subprocess,
        "run",
        return_value=_completed(1, "bwrap: No permissions to create new namespace\n"),
    ) as run:
        failure = wb._bubblewrap_failure("/usr/bin/bwrap")
    assert failure is not None and "No permissions to create new namespace" in failure
    argv = run.call_args.args[0]
    assert argv[0] == "/usr/bin/bwrap" and "--ro-bind" in argv
    assert run.call_args.kwargs["timeout"] > 0
    with mock.patch.object(wb.subprocess, "run", return_value=_completed(0)):
        assert wb._bubblewrap_failure("/usr/bin/bwrap") is None


@pytest.mark.parametrize(
    "error", [subprocess.TimeoutExpired("bwrap", 10), FileNotFoundError("bwrap")]
)
def test_the_real_probe_reports_a_timeout_or_missing_binary(error: Exception) -> None:
    with mock.patch.object(wb.subprocess, "run", side_effect=error):
        assert wb._bubblewrap_failure("/usr/bin/bwrap")


def _runtime(**changes: Any) -> dict[str, Any]:
    report = wb.runtime_doctor({}, _probe(bubblewrap_failure=lambda _p: None))
    report.update(changes)
    return report


def _checks(runtime: dict[str, Any]) -> dict[str, Any]:
    with mock.patch.object(wb, "runtime_doctor", return_value=runtime):
        return {check.id: check for check in wb.workbench_section().checks}


def test_doctor_row_names_the_bubblewrap_failure() -> None:
    runtime = _runtime(
        restricted={"status": "bubblewrap-unusable", "detail": "uid map: Permission denied"}
    )
    row = _checks(runtime)["workbench.restricted"]
    assert row.status == "skip"
    assert "uid map: Permission denied" in row.detail


def test_node_row_states_the_pitwall_requirement() -> None:
    runtime = _runtime(node={"version": "20.1.0", "supported": False})
    row = _checks(runtime)["workbench.node"]
    assert "Pitwall requires Node 22.22.1 or newer" in row.detail
    assert "required by Pi" not in row.detail
    assert "22.22.1" in (row.next_step or "")
