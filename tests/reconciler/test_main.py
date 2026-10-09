"""Tests for the reconciler module entrypoint."""

from __future__ import annotations

import os
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

import pitwall.reconciler.__main__ as reconciler_main
from pitwall.reconciler import WorkerSettings
from tests.hang_guard import HANG_GUARD_SECS


def test_main_runs_arq_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    worker = MagicMock()
    create_worker = MagicMock(return_value=worker)
    monkeypatch.setattr(reconciler_main, "_ARQ_AVAILABLE", True)
    monkeypatch.setattr(reconciler_main, "create_worker", create_worker)
    monkeypatch.setattr(sys, "argv", ["pitwall-reconciler"])

    reconciler_main.main()

    create_worker.assert_called_once_with(WorkerSettings)
    worker.run.assert_called_once_with()


def test_main_check_mode_exits_with_config_check_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    check_redis_config = MagicMock(return_value=7)
    monkeypatch.setattr(reconciler_main, "check_redis_config", check_redis_config)
    monkeypatch.setattr(sys, "argv", ["pitwall-reconciler", "check"])

    with pytest.raises(SystemExit) as exc_info:
        reconciler_main.main()

    assert exc_info.value.code == 7
    check_redis_config.assert_called_once_with()


def test_main_fails_when_arq_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(reconciler_main, "_ARQ_AVAILABLE", False)
    monkeypatch.setattr(reconciler_main, "create_worker", None)
    monkeypatch.setattr(sys, "argv", ["pitwall-reconciler"])

    with pytest.raises(SystemExit) as exc_info:
        reconciler_main.main()

    assert exc_info.value.code == 1
    assert "arq is not installed; cannot run worker" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("redis_url", "exit_code", "message"),
    [
        ("not-a-dsn", 1, "REDIS_URL is not a valid redis:// DSN"),
        (None, 78, "missing-runtime-config"),
    ],
)
def test_check_reports_bad_redis_url_without_a_traceback(
    redis_url: str | None, exit_code: int, message: str
) -> None:
    env = {key: value for key, value in os.environ.items() if key != "REDIS_URL"}
    if redis_url is not None:
        env["REDIS_URL"] = redis_url

    result = subprocess.run(
        [sys.executable, "-m", "pitwall.reconciler", "check"],
        env=env,
        capture_output=True,
        text=True,
        timeout=HANG_GUARD_SECS,
        check=False,
    )

    assert result.returncode == exit_code
    assert message in result.stderr
    assert "Traceback" not in result.stderr


def test_worker_refuses_a_bad_redis_url_before_starting(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    create_worker = MagicMock()
    monkeypatch.setattr(reconciler_main, "_ARQ_AVAILABLE", True)
    monkeypatch.setattr(reconciler_main, "create_worker", create_worker)
    monkeypatch.setattr(sys, "argv", ["pitwall-reconciler"])
    monkeypatch.setenv("REDIS_URL", "not-a-dsn")

    with pytest.raises(SystemExit) as exc_info:
        reconciler_main.main()

    assert exc_info.value.code == 1
    assert "REDIS_URL is not a valid redis:// DSN" in capsys.readouterr().err
    create_worker.assert_not_called()
