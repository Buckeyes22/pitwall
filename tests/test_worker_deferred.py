"""The deferred GPU worker must never look healthy to old automation."""

from __future__ import annotations

import runpy
import sys

import pytest

from pitwall import worker


def test_legacy_worker_entrypoint_fails_closed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert worker.main(["--type", "llm"]) == worker.EX_UNAVAILABLE
    captured = capsys.readouterr()
    assert "unavailable in the public alpha" in captured.err
    assert "0002-worker-deferred.md" in captured.err


def test_legacy_worker_module_run_exits_unavailable(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `python -m pitwall.worker` must exit 69, not 0. Drop the cached module so
    # runpy executes a fresh copy without its "already imported" RuntimeWarning.
    monkeypatch.delitem(sys.modules, "pitwall.worker")
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("pitwall.worker", run_name="__main__")
    assert exc_info.value.code == worker.EX_UNAVAILABLE
    captured = capsys.readouterr()
    assert "unavailable in the public alpha" in captured.err
    assert "0002-worker-deferred.md" in captured.err
