"""Identifier columns are never truncated on a narrow, non-terminal console."""

from __future__ import annotations

import io

import pytest

from pitwall.cli.output import Output

LONG_ID = "gw-agentrouter-claude-opus-4-8-with-a-very-long-provider-identifier-suffix"


def _render(**kwargs: object) -> str:
    buffer = io.StringIO()
    out = Output(stdout_file=buffer)
    out.print_table(
        "Things",
        ["ID", "Pool", "Free type", "Used/budget", "Headroom"],
        [[LONG_ID, "agentrouter", "one-time-initial", "0/200000000", "[##########] 100%"]],
        **kwargs,  # type: ignore[arg-type]
    )
    return buffer.getvalue()


def test_kept_column_survives_an_80_column_console_intact() -> None:
    text = _render(keep_whole=("ID",))

    assert LONG_ID in text
    assert "…" not in text
    assert "agentrouter" in text, "other columns are not cropped away"


def test_other_columns_fold_instead_of_ending_in_an_ellipsis() -> None:
    text = _render(keep_whole=("ID",))

    assert "one-time" in text
    assert "200000000" in text
    assert "…" not in text


def test_without_keep_whole_the_table_still_fits_the_console() -> None:
    text = _render()

    assert max(len(line) for line in text.splitlines()) <= 80


def test_quotas_keeps_provider_ids_whole(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pitwall.cli import gateway

    row = {
        "provider": LONG_ID,
        "pool": "agentrouter",
        "free_type": "one-time-initial",
        "used": 0,
        "budget": 200000000,
        "headroom": 1.0,
        "reset_at": None,
        "tos": "caution",
        "lockout": None,
    }
    monkeypatch.setattr(gateway, "_quota_rows", lambda: ([row], "local"))
    monkeypatch.setattr(gateway, "_used_budget", lambda _row: "0/200000000")
    monkeypatch.setattr(gateway, "_headroom_bar", lambda _value: "[##########] 100%")
    monkeypatch.setattr(gateway, "_lockout_badge", lambda _value: "—")

    assert gateway.cmd_quotas([]) == 0

    out = capsys.readouterr().out
    assert LONG_ID in out
    assert "…" not in out


def test_models_list_keeps_model_ids_whole(capsys: pytest.CaptureFixture[str]) -> None:
    from pitwall.cli import models

    assert models.cmd_models(["list"]) == 0

    out = capsys.readouterr().out
    assert "…" not in out
    assert "MiniMaxAI/MiniMax-H3" in out
