"""Hermetic characterization tests for the ``db`` group dispatcher."""

from __future__ import annotations

import pytest

from pitwall import db


@pytest.mark.parametrize("flag", ["-h", "--help", "help"])
def test_db_help_flag_prints_usage_to_stdout(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert db.main([flag]) == 0

    captured = capsys.readouterr()
    assert "Usage: pitwall db" in captured.out
    assert captured.err == ""


def test_db_help_with_json_flag_prints_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert db.main(["--json", "--help"]) == 0

    assert "Usage: pitwall db" in capsys.readouterr().out


def test_db_usage_lists_every_accepted_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert db.main(["--help"]) == 0

    assert capsys.readouterr().out.strip() == (
        "Usage: pitwall db {migrate|reset [--force]|status} [--json]"
    )


def test_db_no_args_prints_usage_to_stderr_and_returns_1(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert db.main([]) == 1

    captured = capsys.readouterr()
    assert "Usage: pitwall db" in captured.err
    assert captured.out == ""


def test_db_unknown_command_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    assert db.main(["bogus"]) == 1

    captured = capsys.readouterr()
    assert "Unknown command: bogus" in captured.err
    assert "Usage: pitwall db" in captured.err


@pytest.mark.parametrize("argv", [["migrate", "--help"], ["status", "-h"], ["reset", "help"]])
def test_db_help_after_subcommand_prints_usage_without_running(
    argv: list[str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _must_not_run(*args: object, **kwargs: object) -> int:
        raise AssertionError("db subcommand must not execute when help is requested")

    for name in ("cmd_migrate", "cmd_reset", "cmd_status"):
        monkeypatch.setattr(db, name, _must_not_run)

    assert db.main(argv) == 0

    captured = capsys.readouterr()
    assert "Usage: pitwall db" in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    "argv", [["status", "--bogus"], ["migrate", "--bogus"], ["reset", "--forse"]]
)
def test_db_unrecognized_arguments_exit_2_like_argparse(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert db.main(argv) == 2

    captured = capsys.readouterr()
    assert f"pitwall db {argv[0]}: error: unrecognized arguments: {argv[1]}" in captured.err
    assert "Usage: pitwall db" in captured.err
