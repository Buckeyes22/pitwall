"""Feature-local CLI adapter tests for the burn-rate read model."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from pitwall.cli import burn_rate as cli_burn_rate
from tests.finops._burn_rate import sample_burn_rate_read


def test_burn_rate_json_is_the_same_schema_as_the_service_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = sample_burn_rate_read()
    pool = object()
    get_pool = AsyncMock(return_value=pool)
    service = AsyncMock(return_value=expected)
    monkeypatch.setattr(cli_burn_rate, "get_pool", get_pool)
    monkeypatch.setattr(cli_burn_rate, "read_configured_burn_rate", service)
    monkeypatch.setattr(cli_burn_rate, "_utc_now", lambda: expected.now)

    assert cli_burn_rate.cmd_burn_rate(["--window-days", "7", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == expected.to_dict()
    get_pool.assert_awaited_once_with()
    service.assert_awaited_once_with(pool, now=expected.now, window_days=7)


def test_burn_rate_human_output_exposes_forecast_and_data_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected = sample_burn_rate_read()
    monkeypatch.setattr(cli_burn_rate, "_read_burn_rate", AsyncMock(return_value=expected))

    assert cli_burn_rate.cmd_burn_rate([]) == 0

    output = capsys.readouterr().out
    assert "Burn-rate forecast" in output
    assert "Forecast month-end: $305.00" in output
    assert "Projected breach: 2026-06-20T12:00:00Z" in output
    assert "Projected breach ETA: 10.0 days" in output
    assert "Data: sufficient, fresh, last rollup 2026-06-10" in output


def test_burn_rate_window_validation_is_local_and_bounded() -> None:
    with pytest.raises(SystemExit) as error:
        cli_burn_rate.cmd_burn_rate(["--window-days", "0"])

    assert error.value.code == 2


@pytest.mark.parametrize("json_args", [[], ["--json"]])
def test_burn_rate_failures_are_stable_and_do_not_reflect_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    json_args: list[str],
) -> None:
    credential_canary = "cli-burn-rate-credential-canary"
    monkeypatch.setattr(
        cli_burn_rate,
        "_read_burn_rate",
        AsyncMock(side_effect=RuntimeError(credential_canary)),
    )

    assert cli_burn_rate.cmd_burn_rate(json_args) == 1

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert credential_canary not in combined
    if json_args:
        assert json.loads(captured.out) == {"error": "burn_rate_unavailable"}
        assert captured.err == ""
    else:
        assert "burn_rate_unavailable" in captured.err
        assert captured.out == ""
