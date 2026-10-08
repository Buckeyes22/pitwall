import shlex
from pathlib import Path

import pytest

from pitwall.cli import models as cli_models
from pitwall.cli import serve_model as cli_serve_model

QUICKSTART = Path(__file__).parents[1] / "docs/operator/serve-quickstart.md"


def test_serve_model_quickstart_covers_hermetic_to_live_flow() -> None:
    text = QUICKSTART.read_text(encoding="utf-8")

    for heading in (
        "Catalogue",
        "Fit",
        "Dry run",
        "Serve",
        "Verify `/v1/models`",
        "Verify chat",
        "Stop",
    ):
        assert f"## {heading}" in text

    assert "models list" in text
    assert "models fit <model>" in text
    assert "pitwall serve --plan-only" in text
    assert "pitwall serve --capability <capability>" in text
    assert "/v1/openai/<capability>/v1/chat/completions" in text
    assert "pitwall agents profiles add <route> --from-pitwall <capability>" in text
    assert "paid" in text.lower()


def test_serve_model_quickstart_stop_names_lease_mutate_scope() -> None:
    text = QUICKSTART.read_text(encoding="utf-8")
    stop_section = text.split("## Stop", maxsplit=1)[1]

    assert "lease:mutate" in stop_section


def test_serve_model_quickstart_distinguishes_plan_dry_run_and_live() -> None:
    text = QUICKSTART.read_text(encoding="utf-8")

    assert "does not require `DATABASE_URL`" in text
    assert "not launch a pod" in text
    assert "creates or replays a lease" in text
    assert "not proof" in text.lower()


def _documented_pitwall_argv(text: str) -> list[list[str]]:
    commands: list[list[str]] = []
    for block in text.split("```bash")[1:]:
        command, _, _ = block.partition("```")
        tokens = shlex.split(command.replace("\\\n", " "))
        if "pitwall" in tokens:
            commands.append(tokens[tokens.index("pitwall") + 1 :])
    return commands


def _parse_documented_command(argv: list[str]) -> None:
    replacements = {
        "<ttl-minutes>": "120",
        "<gpu-count>": "1",
        "<container-disk-gb>": "20",
        "<engine>": "vllm",
        "<usd-per-second>": "0.001",
    }
    argv = [replacements.get(value, value) for value in argv]
    if argv[0] == "models":
        cli_models._parse_models_args(argv[1:])
    elif argv[0] == "serve":
        cli_serve_model._parse_serve_model_args(argv[1:])


def test_every_fenced_quickstart_pitwall_command_parses() -> None:
    commands = _documented_pitwall_argv(QUICKSTART.read_text(encoding="utf-8"))

    assert commands
    for command in commands:
        _parse_documented_command(command)


def test_documented_command_parser_rejects_a_bogus_flag_in_fixture_copy() -> None:
    text = QUICKSTART.read_text(encoding="utf-8").replace(
        "models list", "models list --bogus-flag", 1
    )
    commands = _documented_pitwall_argv(text)

    with pytest.raises(SystemExit):
        _parse_documented_command(commands[0])
