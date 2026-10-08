from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from pitwall import cli
from pitwall.cli import leases as cli_leases
from pitwall.tui.leases import LeaseDisplayRow, LeasesSnapshot


def _snapshot() -> LeasesSnapshot:
    return LeasesSnapshot(
        rows=(
            LeaseDisplayRow(
                lease_id="lease-list-1",
                provider_id="prov-list-1",
                pod_id="pod-list-1",
                served_model="org/model",
                engine="vllm",
                variant="bf16",
                state="active",
                readiness="ready",
                expires_at=dt.datetime(2026, 8, 28, 14, 0, tzinfo=dt.UTC),
                cost_accrued_usd=Decimal("0.75"),
                last_traffic_at=dt.datetime(2026, 8, 28, 12, 5, tzinfo=dt.UTC),
                idle_timeout_min=20,
                renewal_policy="activity",
                max_usd_per_hour=Decimal("2.5000"),
            ),
        ),
        refreshed_at=dt.datetime(2026, 8, 28, 12, 6, tzinfo=dt.UTC),
    )


def test_main_dispatches_leases_list() -> None:
    command = MagicMock(return_value=0)
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(cli_leases, "cmd_leases", command)
        assert cli.main(["leases", "list", "--json"]) == 0
    command.assert_called_once_with(["list", "--json"])


def test_leases_list_json_exposes_automation_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = AsyncMock()
    source.load_leases.return_value = _snapshot()
    monkeypatch.setattr(cli_leases, "_leases_source", AsyncMock(return_value=source))

    assert cli_leases.cmd_leases(["list", "--json"]) == 0
    item = json.loads(capsys.readouterr().out)["items"][0]
    assert item["last_traffic_at"] == "2026-08-28T12:05:00+00:00"
    assert item["idle_timeout_min"] == 20
    assert item["renewal_policy"] == "activity"
    assert item["max_usd_per_hour"] == "2.5000"


def test_leases_list_plain_has_automation_columns(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = AsyncMock()
    source.load_leases.return_value = _snapshot()
    monkeypatch.setattr(cli_leases, "_leases_source", AsyncMock(return_value=source))

    assert cli_leases.cmd_leases(["list"]) == 0
    output = " ".join(capsys.readouterr().out.split())
    for expected in ("Last traffic", "Idle", "Policy", "Max $/h", "activity", "$2.5000"):
        assert expected in output


def test_lease_mutation_error_is_stable_and_precedes_pool_access(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import pitwall.db as db

    canary = "sk-lease-canary-1234567890abcdef"
    get_pool = AsyncMock(side_effect=AssertionError("pool opened before inspection"))
    monkeypatch.setattr(db, "get_pool", get_pool)

    assert cli_leases.cmd_leases(["renew", canary, "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"error": "lease_operation_failed"}
    assert canary not in str(payload)
    get_pool.assert_not_awaited()


@pytest.mark.parametrize(
    ("command", "patch_target", "error_path", "code"),
    [
        (
            "stop",
            "pitwall.api.leases.run_teardown",
            "pitwall.api.exceptions:LeaseNotFound",
            "lease_not_found",
        ),
        (
            "stop",
            "pitwall.api.leases.run_teardown",
            "pitwall.api.leases.teardown:TeardownFailed",
            "teardown_failed",
        ),
        (
            "renew",
            "pitwall.leases.mutations.renew_lease",
            "pitwall.leases.mutations:LeaseMutationNotFound",
            "lease_not_found",
        ),
        (
            "renew",
            "pitwall.leases.mutations.renew_lease",
            "pitwall.leases.mutations:LeaseMutationConflict",
            "lease_state_conflict",
        ),
    ],
)
def test_lease_lifecycle_errors_keep_their_stable_codes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    patch_target: str,
    error_path: str,
    code: str,
) -> None:
    import importlib

    import pitwall.db as db

    module_name, class_name = error_path.split(":")
    error_class = getattr(importlib.import_module(module_name), class_name)
    target_module, target_name = patch_target.rsplit(".", 1)
    monkeypatch.setattr(
        importlib.import_module(target_module),
        target_name,
        AsyncMock(
            side_effect=error_class("stopped", "renew")
            if class_name == "LeaseMutationConflict"
            else error_class("lease_missing")
        ),
    )
    monkeypatch.setattr(db, "get_pool", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        "pitwall.leases.mutations.lease_capability_name", AsyncMock(return_value=None)
    )

    assert cli_leases.cmd_leases([command, "lease_missing", "--json"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": code}
