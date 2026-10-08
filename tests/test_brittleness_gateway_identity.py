"""The gateway supervisor recognises its process on hosts without procfs (macOS)."""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

import pytest

from pitwall.personal import gateway


def test_identity_falls_back_to_ps_without_procfs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import pitwall.agents.pids as pids

    seen: list[int] = []

    def ps_identity(pid: int) -> str:
        seen.append(pid)
        return "ps:Mon Oct  5 10:00:00 2026"

    monkeypatch.setattr(pids, "process_identity", ps_identity)

    identity = gateway._process_identity(os.getpid(), proc=tmp_path / "no-proc")

    assert identity == "ps:Mon Oct  5 10:00:00 2026"
    assert seen == [os.getpid()]


def test_is_ours_is_true_for_a_live_process_without_procfs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pitwall.agents.pids as pids

    monkeypatch.setattr(pids, "process_identity", lambda pid: f"ps:started-{pid}")
    monkeypatch.setattr(gateway, "PROC_ROOT", tmp_path / "no-proc")
    record = gateway.GatewayProcess(
        pid=os.getpid(),
        argv=["x"],
        health_url=gateway.DEFAULT_HEALTH_URL,
        started_at=dt.datetime(2026, 10, 5, tzinfo=dt.UTC),
        identity=f"ps:started-{os.getpid()}",
    )

    assert gateway._is_ours(record) is True
    assert gateway._is_ours(record.model_copy(update={"identity": "ps:other"})) is False


def test_linux_identity_keeps_the_recorded_boot_form() -> None:
    identity = gateway._process_identity(os.getpid())

    assert identity is not None
    assert identity.startswith("boot:") or identity.startswith(("linux:", "ps:"))
