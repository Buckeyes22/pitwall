"""The workbench flock doctor row: a skip off Linux, never an impossible remediation."""

from __future__ import annotations

from typing import Any

import pytest

from pitwall.workbench import doctor as wb


def _flock_check(monkeypatch: pytest.MonkeyPatch, flock: dict[str, Any]) -> Any:
    real = wb.runtime_doctor

    def runtime(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {**real(*args, **kwargs), "flock": flock}

    monkeypatch.setattr(wb, "runtime_doctor", runtime)
    section = wb.workbench_section()
    return next(check for check in section.checks if check.id == "workbench.flock")


def test_flock_off_linux_is_a_skip_without_a_util_linux_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = _flock_check(monkeypatch, {"status": "unsupported-platform"})
    assert check.status == "skip"
    assert check.detail == "workbench launch needs Linux (flock)"
    assert check.next_step is None


def test_missing_flock_on_linux_still_warns_with_the_install_hint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check = _flock_check(monkeypatch, {"status": "missing-flock"})
    assert check.status == "warn"
    assert check.next_step == "install util-linux so shared admission can lock"
